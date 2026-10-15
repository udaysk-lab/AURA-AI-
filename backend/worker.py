"""Background worker.

Four loops on one timer:
  * automation rules (fixed tool lists)  — every minute
  * schedules (full agent prompts)       — every minute
  * heartbeat (proactive work)           — per-user interval, checked every minute
  * memory compaction                    — once a day

Run alongside the API:  python worker.py
On a platform with no long-running process (Vercel), the platform's cron calls
GET /api/cron/tick instead, which runs one pass of the same loops — see
app/api/cron.py. On a local install the loops run in-process.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.automation import engine
from app.db import SessionLocal, init_db
from app.models import User
from app.services import heartbeat as heartbeat_service
from app.services import memory as memory_service
from app.services import schedules as schedule_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("aura.worker")

TICK_SECONDS = 60
COMPACTION_INTERVAL = timedelta(hours=24)


def compact_all(db, active_since: datetime | None = None, should_stop=None) -> None:
    query = select(User).where(User.is_active == True)  # noqa: E712
    if active_since is not None:
        query = query.where(User.last_login_at >= active_since)
    users = db.scalars(query).all()
    for user in users:
        if should_stop is not None and should_stop():
            break
        try:
            result = memory_service.compact(db, user.id)
            if result["merged"] or result["dropped"] or result["promoted"]:
                log.info(
                    "Compacted memory for %s: %s merged, %s promoted, %s dropped",
                    user.email, result["merged"], result["promoted"], result["dropped"],
                )
        except Exception:
            log.exception("Compaction failed for %s", user.email)
            db.rollback()


def tick(
    compact: bool = False,
    active_since: datetime | None = None,
    budget_seconds: float | None = None,
) -> dict:
    """One pass over all four loops. Never raises — a bad tick must not kill the
    process, and on a cron platform a non-zero exit reads as a failed job.

    `active_since` limits the heartbeat and compaction to accounts used since
    then; the hosted cron passes it. `budget_seconds` stops it starting new
    work once that long has passed — a cron call is a request with a time
    limit, and whatever didn't fit is still due on the next one. Returns what
    ran, for the cron's log.
    """
    summary = {"automations": 0, "schedules": 0, "heartbeats": 0, "compacted": False, "ok": True}
    deadline = time.monotonic() + budget_seconds if budget_seconds is not None else None

    def out_of_time() -> bool:
        if deadline is None or time.monotonic() < deadline:
            return False
        summary["stopped_early"] = True
        return True

    db = SessionLocal()
    try:
        if not out_of_time():
            fired = engine.run_due_schedules(db, should_stop=out_of_time)
            summary["automations"] = len(fired)
            if fired:
                log.info("Fired %d automation rule(s)", len(fired))

        if not out_of_time():
            ran = schedule_service.run_due(db, should_stop=out_of_time)
            summary["schedules"] = len(ran)
            if ran:
                log.info("Ran %d schedule(s): %s", len(ran), ", ".join(r["schedule"] for r in ran))

        if not out_of_time():
            reports = heartbeat_service.run_all_due(
                db, active_since=active_since, should_stop=out_of_time
            )
            summary["heartbeats"] = len(reports)
            if reports:
                log.info("Ran %d heartbeat(s)", len(reports))

        if compact and not out_of_time():
            compact_all(db, active_since=active_since, should_stop=out_of_time)
            summary["compacted"] = True
        if summary.get("stopped_early"):
            log.info("Tick stopped early to stay inside its time budget; the rest runs next tick")
    except Exception:
        log.exception("Worker tick failed")
        db.rollback()
        summary["ok"] = False
    finally:
        db.close()
    return summary


def run_once() -> None:
    """A single pass, for platform cron.

    Free-tier hosts sleep idle services, so a `while True` timer inside the web
    process fires unpredictably or not at all. Running the same work from cron
    makes the schedule real. Compaction is cheap and idempotent, so it just runs
    on every invocation rather than needing state to track the last run.
    """
    init_db()
    log.info("Worker: single pass")
    tick(compact=True)
    log.info("Worker: done")


def _loop(should_stop=None) -> None:
    """The timer. `should_stop` lets an embedded run exit cleanly on shutdown."""
    last_compaction = datetime.now(timezone.utc) - COMPACTION_INTERVAL

    while True:
        now = datetime.now(timezone.utc)
        due_for_compaction = now - last_compaction >= COMPACTION_INTERVAL
        tick(compact=due_for_compaction)
        if due_for_compaction:
            last_compaction = now

        # Sleep in short slices so a stop request is honoured promptly rather
        # than after a full tick — otherwise Ctrl+C on a local install hangs
        # for up to a minute waiting for this thread.
        if should_stop is None:
            time.sleep(TICK_SECONDS)
            continue
        for _ in range(TICK_SECONDS):
            if should_stop():
                log.info("Worker loop stopping")
                return
            time.sleep(1)


def start_embedded():
    """Run the worker on a daemon thread inside the API process.

    This is what makes a local install a single launch: the same loops the
    standalone worker runs, in-process, so heartbeat, schedules, automations
    and compaction happen without a second terminal. Returns a callable that
    asks the thread to stop; the app's lifespan calls it on shutdown.

    A real server should keep running worker.py (or a platform cron) on its
    own, so that web traffic and background work scale separately.
    """
    import threading

    stopping = threading.Event()
    thread = threading.Thread(
        target=_loop,
        args=(stopping.is_set,),
        name="aura-worker",
        daemon=True,
    )
    thread.start()
    log.info("Worker running in-process — ticking every %ss", TICK_SECONDS)

    def stop() -> None:
        stopping.set()
        thread.join(timeout=5)

    return stop


def main() -> None:
    init_db()
    log.info("Worker started — schedules + heartbeat every %ss, compaction daily", TICK_SECONDS)
    _loop()


if __name__ == "__main__":
    import sys

    if "--once" in sys.argv:
        run_once()
    else:
        main()
