"""Post-action checks (spec 20 section 5.4).

After a write tool succeeds, the loop reads back what it changed, in code and without a model call,
using the id or path in the tool's own result. A check returns
`{"passed": bool, "method": str, "evidence": str}`; a tool with no check, or a call that cannot be
checked safely, returns None (no verification row).

Reading back never widens access: file checks resolve the path through the same fence the file
tools use, bound to the user's connected folders, and a path the fence refuses is simply not
checked.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models import CalendarEvent, EmailMessage, Memory, Task, User

log = logging.getLogger("aura.verifiers")

Check = dict[str, Any]


def _result(passed: bool, method: str, evidence: str) -> Check:
    return {"passed": bool(passed), "method": method, "evidence": evidence[:300]}


def _failed(payload: Any) -> bool:
    return isinstance(payload, dict) and bool(payload.get("error") or payload.get("blocked"))


def _create_task(db: Session, user: User, args: dict, payload: dict) -> Check | None:
    task = db.get(Task, payload.get("id") or "")
    ok = task is not None and task.user_id == user.id
    return _result(ok, "looked the task up again", f"task “{task.title}” exists" if ok else "the task is not there")


def _complete_task(db: Session, user: User, args: dict, payload: dict) -> Check | None:
    task = db.get(Task, args.get("task_id") or "") if args.get("task_id") else None
    if task is None or task.user_id != user.id:
        return None  # found by title: the tool's own message is the evidence
    db.refresh(task)
    ok = task.status == "done"
    return _result(ok, "looked the task up again", f"“{task.title}” is {task.status}")


def _delete_task(db: Session, user: User, args: dict, payload: dict) -> Check | None:
    task_id = args.get("task_id") or ""
    if not task_id:
        return None
    gone = db.get(Task, task_id) is None
    return _result(gone, "looked the task up again", "the task is gone" if gone else "the task is still there")


def _create_event(db: Session, user: User, args: dict, payload: dict) -> Check | None:
    event = db.get(CalendarEvent, payload.get("id") or "")
    ok = event is not None and event.user_id == user.id
    if ok and payload.get("start_at") and event.start_at is not None:
        ok = event.start_at.isoformat()[:16] == str(payload["start_at"])[:16]
    return _result(ok, "looked the event up again", f"event “{event.title}” is on the calendar" if ok else "the event is not on the calendar")


def _delete_event(db: Session, user: User, args: dict, payload: dict) -> Check | None:
    event_id = args.get("event_id") or ""
    if not event_id:
        return None
    gone = db.get(CalendarEvent, event_id) is None
    return _result(gone, "looked the event up again", "the event is gone" if gone else "the event is still there")


def _save_memory(db: Session, user: User, args: dict, payload: dict) -> Check | None:
    if payload.get("saved") is False:
        return None
    row = db.get(Memory, payload.get("id") or "")
    ok = row is not None and row.user_id == user.id
    return _result(ok, "looked the memory up again", "the memory is stored" if ok else "the memory is not stored")


def _send_email(db: Session, user: User, args: dict, payload: dict) -> Check | None:
    if not payload.get("sent"):
        return _result(False, "checked the send result", str(payload.get("message") or "nothing was sent")[:200])
    msg_id = payload.get("message_id") or payload.get("id") or payload.get("external_id")
    if msg_id:
        return _result(True, "checked the send result", f"the mail route accepted it ({str(msg_id)[:60]})")
    return _result(True, "checked the send result", f"sent to {payload.get('to')} via {payload.get('route')}")


def _archive_email(db: Session, user: User, args: dict, payload: dict) -> Check | None:
    email = db.get(EmailMessage, args.get("email_id") or "")
    if email is None or email.user_id != user.id:
        return None
    db.refresh(email)
    return _result(bool(email.is_archived), "looked the email up again",
                   "it is out of the inbox" if email.is_archived else "it is still in the inbox")


def _with_fence(db: Session, user: User, fn: Callable[[Any], Check | None]) -> Check | None:
    """Run a file check inside the user's connected folders; a refused path is not checked."""
    from app.device import files
    from app.permissions import access

    try:
        with access.bound(db, user):
            return fn(files)
    except Exception as exc:  # PathNotAllowed, DeviceUnavailable, anything odd: no check
        log.debug("file check skipped: %s", exc)
        return None


def _write_file(db: Session, user: User, args: dict, payload: dict) -> Check | None:
    def check(files) -> Check | None:
        real = files.resolve(str(args.get("path") or ""))
        if not os.path.isfile(real):
            return _result(False, "looked at the file again", "the file is not there")
        if args.get("append"):
            return _result(True, "looked at the file again", f"the file exists ({os.path.getsize(real)} bytes)")
        size = os.path.getsize(real)
        want = payload.get("bytes")
        if isinstance(want, int) and not isinstance(want, bool):
            return _result(size == want, "looked at the file again",
                           f"the file is {size} bytes" + ("" if size == want else f", expected {want}"))
        return _result(True, "looked at the file again", f"the file exists ({size} bytes)")

    return _with_fence(db, user, check)


def _edit_file(db: Session, user: User, args: dict, payload: dict) -> Check | None:
    def check(files) -> Check | None:
        real = files.resolve(str(args.get("path") or ""), must_exist=True)
        with open(real, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        replace = str(args.get("replace") or "")
        ok = (replace in text) if replace else True
        return _result(ok, "read the file again", "the new text is in the file" if ok else "the new text is not in the file")

    return _with_fence(db, user, check)


def _move_file(db: Session, user: User, args: dict, payload: dict) -> Check | None:
    def check(files) -> Check | None:
        dst = files.resolve(str(args.get("destination") or ""))
        src = files.resolve(str(args.get("source") or ""))
        ok = os.path.exists(dst) and (not os.path.exists(src) or os.path.realpath(src) == os.path.realpath(dst))
        return _result(ok, "looked at both paths again", "it is at the new path and gone from the old" if ok else "the move did not complete")

    return _with_fence(db, user, check)


def _make_directory(db: Session, user: User, args: dict, payload: dict) -> Check | None:
    def check(files) -> Check | None:
        ok = os.path.isdir(files.resolve(str(args.get("path") or "")))
        return _result(ok, "looked at the folder again", "the folder exists" if ok else "the folder is not there")

    return _with_fence(db, user, check)


def _delete_file(db: Session, user: User, args: dict, payload: dict) -> Check | None:
    def check(files) -> Check | None:
        gone = not os.path.exists(files.resolve(str(args.get("path") or "")))
        return _result(gone, "looked at the path again", "it is gone" if gone else "it is still there")

    return _with_fence(db, user, check)


CHECKS: dict[str, Callable[[Session, User, dict, dict], Check | None]] = {
    "create_task": _create_task,
    "complete_task": _complete_task,
    "delete_task": _delete_task,
    "create_event": _create_event,
    "delete_event": _delete_event,
    "save_memory": _save_memory,
    "send_email": _send_email,
    "archive_email": _archive_email,
    "write_file": _write_file,
    "edit_file": _edit_file,
    "move_file": _move_file,
    "make_directory": _make_directory,
    "delete_file": _delete_file,
}


def check(db: Session, user: User, tool: str, args: dict | None, payload: Any) -> Check | None:
    """The read-back for one finished write call, or None (reads, unknown tools, failed calls,
    anything that cannot be checked). Never raises."""
    fn = CHECKS.get(tool)
    if fn is None or _failed(payload) or not isinstance(payload, dict):
        return None
    try:
        return fn(db, user, args or {}, payload)
    except Exception:
        log.exception("verifier for %s failed", tool)
        db.rollback()
        return None
