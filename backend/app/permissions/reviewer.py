"""The auto-mode safety reviewer (spec 10 section 7).

In `auto` mode an action that would have asked goes to a separate, second model call that decides
whether the user would want it done, given only what they asked. `review(...)` is called by the
gate (step 10) and returns a `ReviewerVerdict`; the gate turns it into allow or deny and writes the
`review` event.

What it sees, and what it never sees
  * the owner's own instruction (`ctx.instruction`, supplied by the loop), the limits the user
    wrote in Settings (`reviewer_boundaries`), and the proposed call: tool, kind, one-line
    description, extracted targets, arguments (truncated);
  * never a tool result, an email body, a file's contents or the model's own earlier text. The
    arguments can still carry such text (a message the agent drafted); the prompt says argument text
    is data, and the deterministic pre-blocks below cover the commonest exfiltration shapes.

Fail closed
  * a pre-block, an unusable reply, an exception, an empty reply, a reply that wants to call a tool,
    a spend cap reached: all are blocks. A reviewer that is not available (the keyword mock
    provider) never allows anything; `auto` then acts as `ask` (see `available`).
  * the fall-back counters (3 blocks in a row, 20 in one run) belong to the loop: it resets
    `ctx.reviewer_streak` on a reviewer allow and adds one to both counters on a reviewer deny
    (error and pre-block included). The gate reads them before calling this module.

Usage: `usage.check(trigger="review", background=...)` before and `usage.record(trigger="review")`
after. The call counts in the background share of the daily cap when the action is unattended
(schedule, automation, inbound) and as ordinary spend when the user is watching.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("aura.permissions.reviewer")

MAX_REASON = 300
MAX_ARGS_CHARS = 4000
MAX_REQUEST_CHARS = 6000
MAX_BOUNDARIES_CHARS = 2000
UNATTENDED = ("schedule", "automation", "inbound")

# Pre-block codes (logged as the verdict's `code`; the gate maps every one to REVIEWER_BLOCK).
PRE_SHELL = "PREBLOCK_SHELL"
PRE_RECIPIENT = "PREBLOCK_RECIPIENT"
PRE_EXFIL_URL = "PREBLOCK_EXFIL_URL"
PRE_SCRIPT = "PREBLOCK_SCRIPT"
PRE_LONG_ARGS = "PREBLOCK_LONG_ARGS"

_RUN64 = re.compile(r"[A-Za-z0-9+/_=-]{64,}")  # base64, base64url or hex run
_MAX_QUERY = 512
_MAX_PATH = 512
_HOST_RUN = re.compile(r"[A-Za-z0-9_=-]{32,}")  # F11: one host label that long is a payload, not a name
_ADDRESS = re.compile(r"(?<![\w.+%-])[\w.+%-]+@[\w-]+(?:\.[\w-]+)+(?![\w-])")


@dataclass(frozen=True)
class ReviewerVerdict:
    allow: bool
    reason: str = ""
    code: str = ""  # REVIEWER_BLOCK | REVIEWER_ERROR | a pre-block code
    model: str = ""
    latency_ms: int = 0
    tokens: int = 0


# ---------------------------------------------------------------------------
# Availability and model
# ---------------------------------------------------------------------------


def available() -> bool:
    """False for the keyword mock provider: it cannot judge anything, so `auto` acts as `ask`."""
    from app.config import settings

    if settings.resolved_provider() == "mock":
        return False
    try:
        from app.llm import get_provider

        return get_provider().name != "mock"
    except Exception:  # a provider that cannot even be built cannot review
        return False


def model_name() -> str | None:
    """REVIEWER_MODEL, else BACKGROUND_MODEL, else None (the provider's default model)."""
    from app.config import settings

    return (settings.reviewer_model or "").strip() or (settings.background_model or "").strip() or None


# ---------------------------------------------------------------------------
# Deterministic pre-blocks (7.2): no model call
# ---------------------------------------------------------------------------


def _contacts(db, user) -> set[str]:
    from sqlalchemy import select

    from app.models import Contact

    try:
        return {c.lower().strip() for c in db.scalars(select(Contact.email).where(Contact.user_id == user.id)).all() if c}
    except Exception:  # pragma: no cover - a failed lookup must not turn into an allow
        log.exception("could not read contacts")
        return set()


def _instruction_addresses(instruction: str) -> set[str]:
    """F16: whole addresses written in the request, never a piece of a longer one."""
    return {m.group(0).lower() for m in _ADDRESS.finditer(instruction or "")}


def _recipient_known(address: str, named: set[str], contacts: set[str]) -> bool:
    address = (address or "").lower().strip()
    return address in contacts or address in named


def _long_run(text: str) -> bool:
    return bool(_RUN64.search(text))


def _url_exfil(raw: Any) -> bool:
    """F11: the query and fragment, every path segment (decoded too) and every host label are checked."""
    from urllib.parse import unquote, urlsplit

    try:
        parts = urlsplit(str(raw or ""))
        host = parts.hostname or ""
        userinfo = parts.netloc.rpartition("@")[0] if "@" in parts.netloc else ""
    except ValueError:
        return True  # a URL that cannot be read is not one to fetch on the user's behalf
    tail = (parts.query or "") + (parts.fragment or "")
    if len(tail) > _MAX_QUERY or _long_run(tail) or _long_run(unquote(tail)):
        return True
    path = parts.path or ""
    if len(path) > _MAX_PATH:
        return True
    for seg in re.split(r"[/;]", path + "/" + unquote(path)):
        if _long_run(seg):
            return True
    if userinfo and (len(userinfo) > 64 or _long_run(userinfo)):
        return True
    labels = [x for x in host.split(".") if x]
    return len(host) > 100 or any(_HOST_RUN.search(x) for x in labels)


def _writes_script(policy, args: dict, targets) -> bool:
    from app.device import apps

    if policy.name not in ("write_file", "edit_file"):
        return False
    text = args.get("content") if policy.name == "write_file" else args.get("replace")
    if isinstance(text, str) and any(line.startswith("#!") for line in text.splitlines()):
        return True
    for t in targets:
        if t.kind == "path" and t.role == "write" and t.value.lower().endswith(apps._SCRIPTS):
            return True
    return False


def pre_block(db, user, policy, args, targets, ctx) -> tuple[str, str] | None:
    """(code, reason) when the call is stopped without asking a model, else None."""
    if not isinstance(args, dict):
        args = {}
    for t in targets:
        if t.kind == "command" and t.parsed is not None:
            p = t.parsed
            if p.opaque or p.dangerous or any(s.opaque or s.dangerous for s in p.segments):
                return PRE_SHELL, "This command is too hard to check safely (it hides what it runs, or it is dangerous)."
    if policy.name in ("send_email", "create_event"):
        recipients = [t.value for t in targets if t.kind == "recipient"]
        if recipients:
            contacts = _contacts(db, user)
            named = _instruction_addresses(ctx.instruction or "")
            unknown = [r for r in recipients if not _recipient_known(r, named, contacts)]
            if unknown:
                return PRE_RECIPIENT, f"{unknown[0]} was not named in the request and is not one of the user's contacts."
    try:
        arg_len = len(json.dumps(args, ensure_ascii=False, default=str, sort_keys=True))
    except (TypeError, ValueError):
        arg_len = MAX_ARGS_CHARS + 1
    if arg_len > MAX_ARGS_CHARS:
        # F11: the reviewer must see all of it; text it cannot see could be anything.
        return PRE_LONG_ARGS, "The request is too long for the safety reviewer to read in full, so it needs the user."
    url_arg = args.get("url") if policy.name == "fetch_url" else (
        args.get("name") if policy.name == "open_app" and any(t.kind == "url" for t in targets) else None)
    if url_arg is not None and _url_exfil(url_arg):
        return PRE_EXFIL_URL, "The address carries a long or encoded payload, which could be data being sent out."
    if _writes_script(policy, args, targets):
        return PRE_SCRIPT, "Writing a script is running code by another route, so it needs the user."
    return None


# ---------------------------------------------------------------------------
# The model call
# ---------------------------------------------------------------------------


def _clip(text: Any, limit: int) -> str:
    return str(text or "")[:limit]


def _fence(text: str) -> str:
    """Stop quoted text from closing the prompt's <<< >>> blocks."""
    return text.replace("<<<", "< < <").replace(">>>", "> > >")


def _one_line(text: str) -> str:
    return " ".join((text or "").split())[:200]


def _describe(policy, args, targets, ctx) -> str:
    from app.agent.tools import REGISTRY

    tool = REGISTRY.get(policy.name)
    shown = []
    for t in targets:
        if t.kind == "pid":
            continue
        shown.append({"kind": t.kind, "role": t.role, "value": t.raw if t.kind == "command" else t.value})
    try:
        arg_json = json.dumps(args, ensure_ascii=False, default=str, sort_keys=True)
    except (TypeError, ValueError):
        arg_json = "{}"
    return json.dumps(
        {
            "tool": policy.name,
            "kind": policy.kind,
            "does": _one_line(tool.spec.description if tool else ""),
            "targets": shown,
            "arguments": _clip(arg_json, MAX_ARGS_CHARS),
            "trigger": ctx.trigger,
        },
        ensure_ascii=False, default=str,
    )


def build_messages(user, policy, args, targets, ctx, boundaries: str) -> list[dict]:
    """The whole input of the reviewer call. Nothing but the pieces named in 7.1 goes in."""
    from app.agent.prompts import REVIEWER

    away = ctx.trigger in UNATTENDED
    prompt = REVIEWER.format(
        name=_clip(getattr(user, "name", "") or "the user", 80),
        presence="away and not watching this happen" if away else "watching this conversation right now",
        boundaries=_fence(_clip(boundaries, MAX_BOUNDARIES_CHARS).strip()) or "none",
        request=_fence(_clip(ctx.instruction, MAX_REQUEST_CHARS)) or "(no request text)",
        action=_fence(_describe(policy, args, targets, ctx)),
    )
    return [{"role": "user", "content": prompt}]


def parse_verdict(text: str) -> tuple[bool, str] | None:
    """(allow, reason) from a reply that is exactly {"decision": allow|block, "reason": str}; else None."""
    try:
        data = json.loads((text or "").strip())
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or set(data) != {"decision", "reason"}:
        return None
    if data["decision"] not in ("allow", "block") or not isinstance(data["reason"], str):
        return None
    return data["decision"] == "allow", data["reason"].strip()[:MAX_REASON]


def _error(reason: str, started: float, model: str = "", tokens: int = 0) -> ReviewerVerdict:
    return ReviewerVerdict(False, reason, "REVIEWER_ERROR", model, int((time.perf_counter() - started) * 1000), tokens)


def review(db, user, policy, args, targets, ctx) -> ReviewerVerdict:
    """Ask the reviewer about one call. Never raises; anything but a clean `allow` is a block."""
    started = time.perf_counter()
    try:
        blocked = pre_block(db, user, policy, args, targets, ctx)
    except Exception:
        log.exception("pre-block check failed")
        return _error("The safety reviewer could not check this.", started)
    if blocked:
        return ReviewerVerdict(False, blocked[1], blocked[0], "", int((time.perf_counter() - started) * 1000), 0)
    if not available():
        return _error("The safety reviewer is not available.", started)

    from app.llm import get_provider
    from app.models import UserSettings
    from app.services import usage as usage_service
    from sqlalchemy import select

    try:
        row = user.settings or db.scalars(select(UserSettings).where(UserSettings.user_id == user.id)).first()
        boundaries = (row.reviewer_boundaries if row else "") or ""
        messages = build_messages(user, policy, args, targets, ctx, boundaries)
        model = model_name()
        try:
            usage_service.check(db, user.id, trigger="review", background=ctx.trigger in UNATTENDED)
        except usage_service.SpendCapReached:
            return _error("Today's spend cap is reached, so the safety reviewer could not check this.", started)
        response = get_provider().complete(messages, None, model=model)
    except Exception as exc:  # the SDKs raise their own types
        log.warning("reviewer call failed: %s", type(exc).__name__)
        return _error("The safety reviewer could not check this.", started)

    used = getattr(response, "usage", None)
    tokens = int(getattr(used, "total", 0) or 0)
    used_model = (getattr(used, "model", "") or model or "")
    try:
        if used is not None:
            usage_service.record(db, user.id, used, trigger="review")
    except Exception:
        log.exception("could not record the reviewer's usage")

    if getattr(response, "tool_calls", None):
        return _error("The safety reviewer gave an unusable answer.", started, used_model, tokens)
    parsed = parse_verdict(getattr(response, "text", ""))
    if parsed is None:
        return _error("The safety reviewer gave an unusable answer.", started, used_model, tokens)
    allow, reason = parsed
    latency = int((time.perf_counter() - started) * 1000)
    if allow:
        return ReviewerVerdict(True, reason or "The safety reviewer allowed this.", "", used_model, latency, tokens)
    return ReviewerVerdict(False, reason or "The safety reviewer blocked this.", "REVIEWER_BLOCK", used_model, latency, tokens)
