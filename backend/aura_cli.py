#!/usr/bin/env python3
"""AURA from a terminal.

The point of a local install is that AURA is *here*, so it should be reachable
without opening a browser:

    python aura_cli.py "what's on my calendar today"
    python aura_cli.py                      # interactive
    python aura_cli.py --status             # what it can do on this machine

It talks to the local API over HTTP like any other client — same agent, same
skills, same approval gate — so nothing here can do more than the web app can.
When the agent parks an action for approval, this prints the card and lets you
answer it on the spot, which is the whole point of being at the machine.

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


class Aura:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        self.client = httpx.Client(timeout=180, follow_redirects=True)
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
        """Stream one turn. Returns the conversation id to continue in."""
        payload = {"message": message, "conversation_id": conversation}
        reply_started = False
        convo_id = conversation

        try:
            with self.client.stream(
                "POST",
                f"{self.url}/api/chat/stream",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
            ) as response:
                if response.status_code != 200:
                    response.read()
                    print(red(f"Error {response.status_code}: {_detail(response)}"), file=sys.stderr)
                    return convo_id

                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    kind = event.get("type")
                    if kind == "start":
                        convo_id = event.get("conversation_id", convo_id)
                    elif kind == "skill":
                        mark = "·" if event.get("ok", True) else "✗"
                        print(dim(f"  {mark} [{event.get('code')}] {event.get('summary')}"))
                    elif kind == "learned":
                        print(teal(f"  ✎ learned: {event.get('value')}"))
                    elif kind == "pending_action":
                        self._approve(event)
                    elif kind == "delta":
                        if not reply_started:
                            print()
                            reply_started = True
                        sys.stdout.write(event.get("value", ""))
                        sys.stdout.flush()
                    elif kind == "capped":
                        print(amber("  ! daily spend cap reached"))
                    elif kind == "error":
                        print(red(f"\n{event.get('message')}"), file=sys.stderr)
        except httpx.HTTPError as exc:
            print(red(f"Couldn't reach AURA at {self.url}: {exc}"), file=sys.stderr)
            print(dim("Is the backend running?  uvicorn app.main:app --port 8000"), file=sys.stderr)
            return convo_id

        if reply_started:
            print("\n")
        return convo_id

    def _approve(self, event: dict) -> None:
        """An action is waiting. Ask here rather than sending them to the app."""
        print()
        print(amber("  ┌ Needs your say-so"))
        for line in str(event.get("preview", "")).splitlines():
            print(amber(f"  │ ") + line)
        print(amber("  └ ") + dim("[y] once  [a] always  [10] ten minutes  [n] no"))

        try:
            answer = input("    > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        decision = {
            "y": "once", "yes": "once", "": "once",
            "a": "always", "always": "always",
            "10": "window", "w": "window",
        }.get(answer, "reject")

        resp = self._request(
            "POST",
            f"/api/pending-actions/{event['id']}",
            json={"decision": decision, "minutes": 10},
        )
        if resp.status_code != 200:
            print(red(f"    couldn't record that: {_detail(resp)}"))
            return
        body = resp.json()
        if decision == "reject":
            print(dim("    turned down"))
        else:
            print(green("    ✓ ") + dim(str(body.get("message") or body.get("result") or "done")))

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

        policy = data.get("policy") or {}
        if policy:
            print(f"  shell approval: {policy.get('exec_mode')}")
            print(f"  folders: {', '.join(policy.get('allowed_roots') or [])}")
        if data.get("active_skills"):
            print(f"  skills on: {', '.join(data['active_skills'])}")
        return 0


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
