#!/usr/bin/env python3
"""AURA from a terminal.

The point of a local install is that AURA is *here*, so it should be reachable
without opening a browser:

    python aura_cli.py "what's on my calendar today"
    python aura_cli.py                      # interactive
    python aura_cli.py --status             # what it can do on this machine

It talks to the local API over HTTP like any other client — same agent, same
skills, same approval gate — so nothing here can do more than the web app can.
It shows the task as it runs (the plan, each step, the checks, the reviewer's blocks, the
report). When the agent parks an action for approval, this prints the card after the stream ends
and lets you answer it on the spot (yes once, or no), which is the whole point of being at the
machine; the task then carries on by itself. Ctrl-C while it works stops the task.
Longer-lasting answers and the cards that widen access are made in the AURA app.

The session token is cached in ~/.aura/cli-token so you sign in once. Delete
that file to forget it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

DEFAULT_URL = os.environ.get("AURA_URL", "http://127.0.0.1:8000")
TOKEN_PATH = Path(os.path.expanduser("~/.aura/cli-token"))

# Enough colour to make the skill lines scannable, and none if the terminal
# isn't one (a pipe, a CI log, Windows without ANSI).
_COLOUR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOUR else text


dim = lambda t: paint(t, "2")          # noqa: E731
bold = lambda t: paint(t, "1")         # noqa: E731
teal = lambda t: paint(t, "36")        # noqa: E731
amber = lambda t: paint(t, "33")       # noqa: E731
red = lambda t: paint(t, "31")         # noqa: E731
green = lambda t: paint(t, "32")       # noqa: E731


# A task that keeps pausing (approvals, step budgets) is continued at most this many times in one go;
# the server's own task step cap is the real limit.
MAX_CONTINUES = 12

PLAN_MARK = {"todo": "○", "doing": "◐", "done": "●", "skipped": "–", "failed": "✗"}


class Turn:
    """What one streamed request produced: the state the CLI needs once the stream has ended."""

    def __init__(self, conversation: str | None = None) -> None:
        self.conversation = conversation
        self.reply_started = False
        self.work_item_id: str | None = None
        self.work_status: str | None = None
        self.approvals: list[dict] = []     # approval_needed events, answered after [DONE]
        self.carry_on = False               # a `continue` event arrived (a step budget, not an approval)
        self.plan: dict[int, str] = {}      # plan step index -> status last printed
        self.interrupted = False            # Ctrl-C while it was streaming
        self.failed = False                 # the request itself failed (no stream)


class Aura:
    def __init__(self, url: str, client: httpx.Client | None = None) -> None:
        self.url = url.rstrip("/")
        self.client = client or httpx.Client(timeout=180, follow_redirects=True)
        self.token = self._load_token()

    # --- plumbing -------------------------------------------------------

    def _load_token(self) -> str:
        try:
            return TOKEN_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _save_token(self, token: str) -> None:
        try:
            TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_PATH.write_text(token, encoding="utf-8")
            # The token is a session credential; keep it to this user.
            try:
                os.chmod(TOKEN_PATH, 0o600)
            except OSError:
                pass  # Windows doesn't do POSIX modes
        except OSError as exc:
            print(dim(f"(couldn't cache the token: {exc})"), file=sys.stderr)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        return self.client.request(method, f"{self.url}{path}", headers=self._headers(), **kwargs)

    # --- auth -----------------------------------------------------------

    def sign_in(self) -> bool:
        """Reuse a cached token, else ask the local API for one."""
        if self.token:
            me = self._request("GET", "/api/auth/me")
            if me.status_code == 200:
                return True
            self.token = ""  # expired or revoked

        resp = self._request("POST", "/api/auth/local")
        if resp.status_code == 200:
            body = resp.json()
            self.token = body["access_token"]
            self._save_token(self.token)
            print(dim(f"Signed in as {body['user']['email']}"))
            return True

        if resp.status_code == 403:
            detail = _detail(resp)
            print(red("Can't sign in from here."), file=sys.stderr)
            print(dim(detail), file=sys.stderr)
            print(
                dim(
                    "A local install needs LOCAL_MODE=true. Otherwise set AURA_TOKEN to a "
                    "token from the web app."
                ),
                file=sys.stderr,
            )
            return False

        print(red(f"Sign-in failed ({resp.status_code}): {_detail(resp)}"), file=sys.stderr)
        return False

    # --- the agent ------------------------------------------------------

    def ask(self, message: str, conversation: str | None) -> str | None:
        """Run one turn to the end: stream it, answer any approvals, and let a paused task carry on.
        Returns the conversation id to continue in."""
        turn = self._stream("/api/chat/stream", {"message": message, "conversation_id": conversation}, conversation)
        for _ in range(MAX_CONTINUES):
            item_id = self._settle(turn)
            if not item_id or turn.interrupted:
                break
            turn = self._stream("/api/chat/continue", {"work_item_id": item_id}, turn.conversation)
        return turn.conversation

    def _stream(self, path: str, payload: dict, conversation: str | None) -> Turn:
        """POST to a streaming endpoint and print its events as they arrive."""
        turn = Turn(conversation)
        try:
            with self.client.stream(
                "POST",
                f"{self.url}{path}",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
            ) as response:
                if response.status_code != 200:
                    response.read()
                    turn.failed = True
                    if response.status_code == 409:
                        print(amber(f"  ! {_detail(response)}"), file=sys.stderr)
                        held = _json(response).get("work_item_id")
                        if held:
                            turn.work_item_id = held
                    else:
                        print(red(f"Error {response.status_code}: {_detail(response)}"), file=sys.stderr)
                    return turn
                self.consume(response.iter_lines(), turn)
        except KeyboardInterrupt:
            turn.interrupted = True
            self._stop(turn)
        except httpx.HTTPError as exc:
            turn.failed = True
            print(red(f"Couldn't reach AURA at {self.url}: {exc}"), file=sys.stderr)
            print(dim("Is the backend running?  uvicorn app.main:app --port 8000"), file=sys.stderr)
            return turn

        if turn.reply_started:
            print("\n")
        return turn

    def consume(self, lines, turn: Turn) -> None:
        """Read server-sent-event lines until [DONE], handling each event."""
        for line in lines:
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                self.handle(event, turn)

    def handle(self, event: dict, turn: Turn) -> None:
        """Show one progress event (spec 20 section 8). Approval cards are collected, never asked
        mid-stream: the run is still going and the prompt would sit in the middle of its output."""
        kind = event.get("type")
        if event.get("work_item_id"):
            turn.work_item_id = event["work_item_id"]

        if kind == "start":
            turn.conversation = event.get("conversation_id", turn.conversation)
        elif kind == "recall":
            parts = [f"{event.get(key)} {label}" for key, label in
                     (("work_items", "past tasks"), ("preferences", "preferences"), ("memories", "facts"))
                     if event.get(key)]
            if parts:
                print(dim("  ↺ recalled " + ", ".join(parts)))
        elif kind == "task":
            if event.get("resumed"):
                print(dim(f"  ▶ carrying on: {event.get('title')}"))
            elif event.get("title"):
                print(bold(f"  ▸ {event.get('title')}") + dim(f"  [{event.get('task_type', '')}]"))
        elif kind == "plan":
            self._show_plan(event, turn)
        elif kind == "step_started":
            print(dim(f"  → {event.get('label') or event.get('tool')}"))
        elif kind == "step_finished":
            mark = "·" if event.get("ok", True) else "✗"
            code = f"[{event['code']}] " if event.get("code") else ""
            print(dim(f"  {mark} {code}{event.get('summary') or event.get('tool')}"))
        elif kind == "verification":
            mark = green("✓") if event.get("passed") else red("✗")
            print(f"  {mark} " + dim(f"checked: {event.get('method')}"
                                     + (f" ({event['evidence']})" if event.get("evidence") else "")))
        elif kind == "reviewer_block":
            print(amber(f"  ⊘ blocked by the safety reviewer: {event.get('tool')}")
                  + dim(f" — {event.get('reason')} ({event.get('streak')} in a row, {event.get('total')} in total)"))
        elif kind == "preference_noted":
            print(teal(f"  ✎ noted: {event.get('text')}"))
        elif kind == "approval_needed":
            turn.approvals.append(event)
        elif kind == "waiting":
            n = len(event.get("pending_action_ids") or [])
            print(dim(f"  ⏸ waiting for {n} approval{'s' if n != 1 else ''}"))
        elif kind == "continue":
            turn.carry_on = True
            print(dim("  … still working, carrying on"))
        elif kind == "delta":
            if not turn.reply_started:
                print()
                turn.reply_started = True
            sys.stdout.write(event.get("value", ""))
            sys.stdout.flush()
        elif kind == "report":
            turn.work_status = event.get("status")
            label = str(event.get("status", "")).replace("_", " ")
            checks = event.get("verification")
            tail = f" · checks {checks}" if checks and checks != "none" else ""
            print(dim(f"  ■ {label}{tail}"))
        elif kind == "done":
            turn.work_status = event.get("work_status") or turn.work_status
        elif kind == "capped":
            print(amber("  ! daily spend cap reached"))
        elif kind == "error":
            print(red(f"\n{event.get('message')}"), file=sys.stderr)

    def _show_plan(self, event: dict, turn: Turn) -> None:
        steps = event.get("steps") or []
        first = not turn.plan
        for step in steps:
            index, status = step.get("index"), step.get("status", "todo")
            if not first and turn.plan.get(index) == status:
                continue
            turn.plan[index] = status
            note = f" — {step['note']}" if step.get("note") else ""
            print(dim(f"  {PLAN_MARK.get(status, '○')} {step.get('text')}{note}"))

    def _stop(self, turn: Turn) -> None:
        """Ctrl-C during a stream: ask the server to stop the task, not just stop listening."""
        print()
        if not turn.work_item_id:
            print(dim("  stopped listening (there was no task to stop yet)"))
            return
        try:
            resp = self._request("POST", "/api/chat/stop", json={"work_item_id": turn.work_item_id})
        except httpx.HTTPError as exc:
            print(red(f"  couldn't ask AURA to stop: {exc}"), file=sys.stderr)
            return
        if resp.status_code == 200:
            print(amber("  ■ stop requested") + dim(f" ({resp.json().get('status')})"))
        else:
            print(red(f"  couldn't stop it: {_detail(resp)}"), file=sys.stderr)

    def _settle(self, turn: Turn) -> str | None:
        """After a stream: answer the approvals it collected. Returns the work item to continue,
        if one is now ready to carry on (or its step budget ran out), else None."""
        carry = turn.work_item_id if turn.carry_on and not turn.approvals else None
        for event in turn.approvals:
            item = self._approve(event)
            if item.get("status") == "ready" and item.get("id"):
                carry = item["id"]
        if turn.approvals and not carry:
            waiting = [e for e in turn.approvals if e.get("work_item_id") or e.get("pending_action_id")]
            if waiting:
                print(dim("  Still waiting on your answer; open AURA to give it.\n"))
        return carry

    def _card(self, event: dict) -> dict:
        """The approval card as the API holds it (options, kind, whether only you may answer)."""
        action_id = event.get("pending_action_id") or event.get("id")
        card = {"id": action_id, "preview": event.get("preview", ""), "card": event.get("card") or "tool",
                "options": list(event.get("options") or []), "always_user": bool(event.get("always_user")),
                "status": "pending"}
        if event.get("options"):
            return card  # the event already says what can be answered
        try:
            resp = self._request("GET", f"/api/pending-actions/{action_id}")
            if resp.status_code == 200:
                card.update({k: v for k, v in resp.json().items() if k in card})
        except httpx.HTTPError:
            pass  # the event's own preview is enough to ask
        return card

    def _approve(self, event: dict) -> dict:
        """An action is waiting. Ask here rather than sending them to the app.

        This terminal can say yes once or no. "For 10 minutes", "always" and the cards that
        connect a folder, allow an app or allow deleting need the AURA app itself, so they are
        not offered here (the server would refuse them for this login anyway).
        Returns the `work_item` the server reports after the answer ({} if none was given)."""
        card = self._card(event)
        if card.get("status") not in ("pending", "resolving"):
            print(dim(f"    {card.get('preview') or 'that request'}: already {card.get('status')}"))
            return {}
        needs_app = bool(card.get("always_user")) or card.get("card", "tool") != "tool"
        options = card.get("options") or []
        print()
        print(amber("  ┌ Needs your say-so"))
        for line in str(card.get("preview", "")).splitlines():
            print(amber("  │ ") + line)
        if event.get("expires_at"):
            print(amber("  │ ") + dim(f"lapses {event['expires_at']}"))
        if needs_app:
            print(amber("  └ ") + dim("This one needs you in the AURA app.  [n] no, or Enter to leave it waiting"))
        else:
            print(amber("  └ ") + dim("[y] once  [n] no"))
            if "window" in options or "always" in options:
                print(dim("    Open AURA to allow this for longer."))

        try:
            answer = input("    > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if needs_app:
            if answer not in ("n", "no"):
                print(dim("    left waiting"))
                return {}
            decision = "reject"
        else:
            decision = "once" if answer in ("y", "yes", "") else "reject"

        resp = self._request("POST", f"/api/pending-actions/{card['id']}", json={"decision": decision})
        if resp.status_code != 200:
            print(red(f"    couldn't record that: {_detail(resp)}"))
            return {}
        body = resp.json()
        if decision == "reject":
            print(dim("    turned down"))
        else:
            print(green("    ✓ ") + dim(str(body.get("message") or body.get("result") or "done")))
        # An answer can free a paused task to carry on (the server says so with status "ready").
        return body.get("work_item") or {}

    # --- device ---------------------------------------------------------

    def status(self) -> int:
        resp = self._request("GET", "/api/device")
        if resp.status_code != 200:
            print(red(f"Couldn't read the device status: {_detail(resp)}"), file=sys.stderr)
            return 1
        data = resp.json()

        if not data.get("enabled"):
            print(bold("Device control: ") + amber("off"))
            print(dim("  AURA is working on its own data only."))
            print(dim("  Set LOCAL_MODE=true and restart to let it operate this computer."))
            return 0

        print(bold("Device control: ") + green("on"))
        print(f"  {data['os']} {data['arch']} · {data['hostname']} · as {data['user']}")
        print(f"  shell: {(data.get('shell') or {}).get('kind', '?')}")
        able = [
            name
            for name, key in (
                ("commands", "shell_control"), ("files", "file_control"),
                ("processes", "process_control"), ("screen", "screen_control"),
                ("clipboard", "clipboard"),
            )
            if data.get(key)
        ]
        print("  can: " + (", ".join(able) if able else "nothing"))
        missing = [n for n, present in (data.get("packages") or {}).items() if not present]
        if missing:
            print(dim(f"  not installed: {', '.join(missing)}"))
            print(dim("    pip install -r requirements-device.txt"))

        # What AURA asks about is the owner's permission mode, and what it may touch is the folders
        # the owner connected (both per user, in the database). The install's own limit comes from
        # the device policy and only caps what can be connected.
        perms = self._request("GET", "/api/permissions")
        if perms.status_code == 200:
            pdata = perms.json()
            label = pdata.get("mode_label") or pdata.get("mode") or "?"
            print(f"  permission mode: {label}")
        folders = self._request("GET", "/api/device/folders")
        if folders.status_code == 200:
            paths = [f.get("path", "") for f in (folders.json() or [])]
            print(f"  connected folders: {', '.join(paths) if paths else 'none (connect one in Settings)'}")
        policy = data.get("policy") or {}
        limit = policy.get("allowed_roots") or []
        if limit:
            print(dim(f"  this install only allows folders inside: {', '.join(limit)}"))
        if data.get("active_skills"):
            print(f"  skills on: {', '.join(data['active_skills'])}")
        return 0


def _json(response: httpx.Response) -> dict:
    try:
        body = response.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        detail = body.get("detail")
        if isinstance(detail, str):
            return detail
        return json.dumps(body)[:300]
    except Exception:
        return (response.text or "")[:300]


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="aura",
        description="Talk to AURA from this terminal.",
        epilog="With no message it starts an interactive session. Ctrl+D to leave.",
    )
    parser.add_argument("message", nargs="*", help="What to ask. Omit for interactive mode.")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"API address (default {DEFAULT_URL})")
    parser.add_argument("--status", action="store_true", help="Show what AURA can do on this machine")
    parser.add_argument("--new", action="store_true", help="Start a fresh conversation")
    args = parser.parse_args()

    aura = Aura(args.url)
    # An explicit token beats the cache — handy for talking to a remote install.
    if os.environ.get("AURA_TOKEN"):
        aura.token = os.environ["AURA_TOKEN"]
    elif not aura.sign_in():
        return 1

    if args.status:
        return aura.status()

    if args.message:
        aura.ask(" ".join(args.message), None)
        return 0

    # Interactive.
    print(bold("AURA") + dim(f" · {args.url}"))
    print(dim("Ask it anything. /new starts a fresh conversation, Ctrl+D leaves.\n"))
    conversation: str | None = None
    while True:
        try:
            line = input(teal("you ") + "› ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/exit", "/quit"):
            return 0
        if line == "/new":
            conversation = None
            print(dim("new conversation\n"))
            continue
        if line == "/status":
            aura.status()
            print()
            continue
        conversation = aura.ask(line, conversation)


if __name__ == "__main__":
    sys.exit(main())
