"""Model-provider abstraction.

AURA talks to one normalised interface. Swapping OpenAI for Anthropic (or a
local model later) means writing one adapter, not touching the agent loop.

Internal message format (provider-agnostic):
    {"role": "system"|"user"|"assistant", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [ToolCall, ...]}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str}
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.config import settings

log = logging.getLogger("aura.llm")


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Usage:
    """Token counts for one call. Zeros mean the provider didn't report them."""

    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


class LLMProvider:
    name = "base"

    def complete(
        self,
        messages: list[dict],
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """`model` overrides the configured default for this one call.

        Used to route background work to something small and cheap without
        changing what the user gets when they're actually talking to it.
        """
        raise NotImplementedError

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return [] when the provider has no embedding capability."""
        return []


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self) -> None:
        from openai import OpenAI  # imported lazily so the dep stays optional

        # base_url lets this same adapter drive Ollama, LM Studio, Groq,
        # DeepSeek or OpenRouter. Local servers ignore the key but the SDK
        # refuses to construct without one, hence the placeholder.
        self.client = OpenAI(
            api_key=settings.openai_api_key or "not-needed",
            base_url=settings.openai_base_url or None,
        )
        self.model = settings.openai_model

    @staticmethod
    def _to_openai(messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m["role"] == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": m["tool_call_id"],
                        "content": m["content"],
                    }
                )
            elif m["role"] == "assistant" and m.get("tool_calls"):
                out.append(
                    {
                        "role": "assistant",
                        "content": m.get("content") or None,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.arguments),
                                },
                            }
                            for tc in m["tool_calls"]
                        ],
                    }
                )
            else:
                out.append({"role": m["role"], "content": m.get("content", "")})
        return out

    def complete(
        self,
        messages: list[dict],
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        chosen = model or self.model
        kwargs: dict[str, Any] = {
            "model": chosen,
            "messages": self._to_openai(messages),
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            kwargs["tool_choice"] = "auto"

        resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0].message
        calls: list[ToolCall] = []
        for tc in choice.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        usage = Usage(model=chosen)
        if getattr(resp, "usage", None):
            usage.input_tokens = resp.usage.prompt_tokens or 0
            usage.output_tokens = resp.usage.completion_tokens or 0
        return LLMResponse(text=choice.content or "", tool_calls=calls, usage=usage)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = self.client.embeddings.create(
            model=settings.embedding_model, input=texts
        )
        return [d.embedding for d in resp.data]


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self) -> None:
        import anthropic

        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = settings.anthropic_model

    @staticmethod
    def _split(messages: list[dict]) -> tuple[str, list[dict]]:
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        convo: list[dict] = []
        for m in messages:
            if m["role"] == "system":
                continue
            if m["role"] == "tool":
                convo.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m["tool_call_id"],
                                "content": m["content"],
                            }
                        ],
                    }
                )
            elif m["role"] == "assistant" and m.get("tool_calls"):
                blocks: list[dict] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m["tool_calls"]:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
                convo.append({"role": "assistant", "content": blocks})
            else:
                convo.append({"role": m["role"], "content": m.get("content", "")})
        return "\n\n".join(system_parts), convo

    def complete(
        self,
        messages: list[dict],
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        chosen = model or self.model
        system, convo = self._split(messages)
        kwargs: dict[str, Any] = {
            "model": chosen,
            "max_tokens": 2048,
            "system": system,
            "messages": convo,
        }
        if tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]

        resp = self.client.messages.create(**kwargs)
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                )

        usage = Usage(model=chosen)
        if getattr(resp, "usage", None):
            usage.input_tokens = getattr(resp.usage, "input_tokens", 0) or 0
            usage.output_tokens = getattr(resp.usage, "output_tokens", 0) or 0
        return LLMResponse(text="".join(text_parts), tool_calls=calls, usage=usage)


# ---------------------------------------------------------------------------
# Mock - keeps the whole product usable with zero API keys
# ---------------------------------------------------------------------------


class MockProvider(LLMProvider):
    """Deterministic keyword router.

    Not intelligence - just enough behaviour that every screen, tool path and
    confirmation flow can be exercised offline and in tests. It is also what a
    deployment without a model key serves to real visitors, so its replies are
    written as sentences rather than dumps of tool output.
    """

    name = "mock"

    # Order matters: the first match wins. Explicit instructions ("remember
    # that…", "add a task…") come before reads, otherwise "Remember that I
    # prefer afternoon meetings" matched the calendar route on "meetings" and
    # listed events instead of saving the preference.
    #
    # Saving needs the message to *start* like an instruction and not be a
    # question: "Do you remember what I prefer?" used to be saved as a
    # preference, word for word, instead of being looked up.
    ROUTES: list[tuple[str, str, dict]] = [
        (r"\b(what do you (know|remember)|do you (know|remember)|what have you (learned|remembered))\b",
         "search_memory", {}),
        (r"^(?!.*\?\s*$)\s*(please\s+)?(remember|note that|keep in mind|i prefer)\b",
         "save_memory", {}),
        (r"\b(remind me|add (a )?task|to.?do|create (a )?task)\b", "create_task", {}),
        (r"\b(schedule|book|set up|create).{0,20}\b(meeting|event|call)\b",
         "create_event", {}),
        (r"\b(brief|briefing|my day|today.?s? (plan|schedule)|catch me up)\b",
         "get_daily_briefing", {}),
        (r"\b(inbox|unread|summar\w+ (my )?email|email summary)\b",
         "summarize_inbox", {}),
        (r"\b(urgent|important) email", "search_emails", {"query": "urgent"}),
        (r"\b(free|available|availability|open slot)\b", "find_free_time", {}),
        (r"\b(conflict|double.?book)\b", "detect_conflicts", {}),
        (r"\b(my )?(calendar|meetings|agenda)\b", "list_events", {}),
        (r"\b(my )?tasks?\b", "list_tasks", {}),
    ]

    def complete(
        self,
        messages: list[dict],
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        # If the last message is a tool result, summarise it and stop.
        if messages and messages[-1]["role"] == "tool":
            return LLMResponse(text=self._summarise_tool_output(messages))

        last_user = next(
            (m.get("content", "") for m in reversed(messages) if m["role"] == "user"), ""
        )
        available = {t.name for t in tools or []}
        text = last_user.lower()

        for pattern, tool_name, base_args in self.ROUTES:
            if tool_name in available and re.search(pattern, text):
                args = dict(base_args)
                args.update(self._extract_args(tool_name, last_user))
                return LLMResponse(
                    tool_calls=[ToolCall(id=f"mock_{tool_name}", name=tool_name, arguments=args)]
                )

        return LLMResponse(
            text=(
                "I'm running in **demo mode**: no AI model is connected yet, so I can only "
                "recognise a few kinds of request.\n\n"
                "Try: *\"what does my day look like\"*, *\"summarise my inbox\"*, "
                "*\"add a task to review the pitch deck\"*, *\"when am I free tomorrow\"*, "
                "or *\"remember that I prefer morning meetings\"*.\n\n"
                "The full assistant switches on once `ANTHROPIC_API_KEY` (or "
                "`OPENAI_API_KEY`) is set on the server."
            )
        )

    @staticmethod
    def _extract_args(tool_name: str, text: str) -> dict:
        if tool_name == "create_task":
            title = re.sub(
                r"^\s*(please\s+)?(remind me to|add a task to|add task|create a task to"
                r"|create task|todo:?|to-?do:?)\s*",
                "",
                text.strip(),
                flags=re.IGNORECASE,
            )
            priority = "urgent" if re.search(r"\b(urgent|asap)\b", text, re.I) else "medium"
            args = {"title": title[:200] or "New task", "priority": priority}
            when = MockProvider._when_phrase(text)
            if when:
                args["due_at"] = when
            return args
        if tool_name == "save_memory":
            content = re.sub(
                r"^\s*(please\s+)?(remember that|remember|note that|keep in mind that|keep in mind)\s*",
                "",
                text.strip(),
                flags=re.IGNORECASE,
            )
            return {"content": content[:500] or text[:500], "kind": "preference"}
        if tool_name == "create_event":
            # "Schedule a call with Priya tomorrow at 3pm" -> "Call with Priya",
            # tomorrow 15:00. The whole sentence used to become the title.
            kind = re.search(r"\b(meeting|event|call)\b", text, re.I)
            who = re.search(r"\bwith\s+([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)?)", text)
            title = (kind.group(1).capitalize() if kind else "Meeting") + (
                f" with {who.group(1)}" if who else ""
            )
            args = {"title": title}
            when = MockProvider._when_phrase(text)
            if when:
                args["start_at"] = when
            minutes = MockProvider._duration_minutes(text)
            if minutes:
                args["duration_minutes"] = minutes
            return args
        if tool_name == "search_memory":
            return {"query": text[:200]}
        if tool_name == "find_free_time":
            minutes = MockProvider._duration_minutes(text)
            return {"duration_minutes": minutes} if minutes else {}
        return {}

    @staticmethod
    def _when_phrase(text: str) -> str | None:
        """'tomorrow at 3pm', 'friday 10:30am', 'next week' — as written."""
        when = re.search(
            r"\b(today|tomorrow|day after tomorrow|next week|"
            r"(next )?(monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b"
            r"(\s+(at\s+)?\d{1,2}(:\d{2})?\s*(am|pm)?)?",
            text,
            re.I,
        )
        return when.group(0) if when else None

    @staticmethod
    def _duration_minutes(text: str) -> int | None:
        """'45 minutes', '1.5 hours', 'half an hour', 'an hour' -> minutes."""
        lowered = text.lower()
        if re.search(r"\bhalf an? hour\b", lowered):
            return 30
        match = re.search(r"\b(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours)\b", lowered)
        if match:
            return max(15, min(480, round(float(match.group(1)) * 60)))
        match = re.search(r"\b(\d+)\s*(m|min|mins|minute|minutes)\b", lowered)
        if match:
            return max(15, min(480, int(match.group(1))))
        if re.search(r"\ban hour\b", lowered):
            return 60
        return None

    # --- Writing tool results up ----------------------------------------

    @staticmethod
    def _zone(messages: list[dict]):
        """The user's zone, read from the system prompt the coordinator wrote."""
        from app.timeutil import zone

        system = next((m.get("content", "") for m in messages if m["role"] == "system"), "")
        match = re.search(r"\(timezone: ([^)]+)\)", system or "")
        return zone(match.group(1).strip() if match else None)

    @staticmethod
    def _when(value: Any, tz, end: Any = None) -> str:
        from datetime import datetime

        from app.timeutil import aware, fmt_clock, fmt_when

        def parse(v):
            if not v:
                return None
            try:
                return aware(datetime.fromisoformat(str(v).replace("Z", "+00:00")))
            except ValueError:
                return None

        start, stop = parse(value), parse(end)
        if not start:
            return ""
        text = fmt_when(start, tz)
        if stop:
            same_day = start.astimezone(tz).date() == stop.astimezone(tz).date()
            text += f"–{fmt_clock(stop, tz)}" if same_day else f" – {fmt_when(stop, tz)}"
        return text

    @classmethod
    def _event_line(cls, e: dict, tz) -> str:
        bits = [cls._when(e.get("start_at"), tz, e.get("end_at")), e.get("title") or "Untitled"]
        if e.get("location"):
            bits.append(e["location"])
        return "- " + " · ".join(b for b in bits if b)

    @classmethod
    def _task_line(cls, t: dict, tz) -> str:
        line = f"- {t.get('title') or 'Untitled task'}"
        extra = [t.get("priority") or ""]
        if t.get("due_at"):
            extra.append(f"due {cls._when(t['due_at'], tz)}")
        extra = [x for x in extra if x]
        return line + (f" ({', '.join(extra)})" if extra else "")

    @staticmethod
    def _email_line(e: dict) -> str:
        sender = e.get("from") or e.get("sender") or "Someone"
        sender = re.sub(r"\s*<[^>]*>\s*$", "", sender).strip() or sender
        flags = [e.get("importance")] if e.get("importance") in ("urgent", "high") else []
        if e.get("needs_reply"):
            flags.append("needs a reply")
        tail = f" ({', '.join(flags)})" if flags else ""
        return f"- {sender} — {e.get('subject') or '(no subject)'}{tail}"

    @classmethod
    def _render(cls, name: str, data: Any, tz) -> str | None:
        if isinstance(data, dict) and data.get("status") == "awaiting_approval":
            # The payload's "message" is an instruction to the model ("Tell
            # them what is pending, then stop"), and was being shown as-is.
            lines = str(data.get("preview") or "").strip().splitlines()
            what = lines[0].strip() if lines else ""  # "Send email to …", not the body
            return (
                "That needs your go-ahead first, so I've queued it"
                + (f":\n\n- {what}" if what else ".")
                + "\n\nAllow it or turn it down below."
            )
        if name == "get_daily_briefing" and isinstance(data, dict):
            parts = [f"**{data.get('greeting', 'Hello')}.** {data.get('headline', '')}".strip()]
            if data.get("meetings"):
                parts.append("**Meetings**\n" + "\n".join(cls._event_line(e, tz) for e in data["meetings"]))
            if data.get("tasks_due"):
                parts.append("**Due soon**\n" + "\n".join(cls._task_line(t, tz) for t in data["tasks_due"][:6]))
            if data.get("suggested_priorities"):
                parts.append(
                    "**What I'd do first**\n"
                    + "\n".join(f"{i}. {p}" for i, p in enumerate(data["suggested_priorities"], 1))
                )
            if data.get("inbox_summary"):
                parts.append(data["inbox_summary"])
            return "\n\n".join(parts)
        if name == "summarize_inbox" and isinstance(data, dict):
            if not data.get("total"):
                return "Your inbox is empty. Connect Google in Settings to bring your mail in."
            parts = [data.get("summary", "")]
            if data.get("highlights"):
                parts.append(
                    "**Worth a look**\n"
                    + "\n".join(
                        cls._email_line({**h, "from": h.get("sender")}) for h in data["highlights"]
                    )
                )
            return "\n\n".join(p for p in parts if p)
        if name == "find_free_time" and isinstance(data, list):
            if not data:
                return "I couldn't find a free slot that long in your working hours this week."
            return "Here's when you're free:\n\n" + "\n".join(
                f"- {cls._when(s.get('start_at'), tz, s.get('end_at'))}" for s in data
            )
        if name == "list_events" and isinstance(data, list):
            if not data:
                return "Nothing on your calendar for that period."
            return "On your calendar:\n\n" + "\n".join(cls._event_line(e, tz) for e in data[:10])
        if name == "detect_conflicts" and isinstance(data, list):
            if not data:
                return "No double-bookings in the next two weeks."
            return "These overlap:\n\n" + "\n".join(
                f"- {c['event_a'].get('title')} and {c['event_b'].get('title')} — "
                f"{c.get('overlap_minutes')} min on {cls._when(c['event_b'].get('start_at'), tz)}"
                for c in data
            )
        if name in ("list_tasks",) and isinstance(data, list):
            if not data:
                return "No open tasks."
            return "Your tasks, most pressing first:\n\n" + "\n".join(
                cls._task_line(t, tz) for t in data[:10]
            )
        if name == "search_emails" and isinstance(data, list):
            if not data:
                return "No emails match that."
            return "\n".join(cls._email_line(e) for e in data[:8])
        if name == "save_memory" and isinstance(data, dict) and data.get("content"):
            return f"Noted — I'll remember that {data['content'].rstrip('.')}."
        if name == "search_memory" and isinstance(data, list):
            if not data:
                return "I don't know anything about that yet."
            return "Here's what I know:\n\n" + "\n".join(f"- {m.get('content')}" for m in data)
        if name == "create_task" and isinstance(data, dict) and data.get("title"):
            due = f", due {cls._when(data['due_at'], tz)}" if data.get("due_at") else ""
            return f"Added **{data['title']}** to your tasks ({data.get('priority', 'medium')}{due})."
        return None

    @classmethod
    def _summarise_tool_output(cls, messages: list[dict]) -> str:
        raw = messages[-1].get("content", "")
        name = messages[-1].get("name", "tool")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return str(raw)[:1500]

        if isinstance(data, dict) and data.get("error"):
            return f"That didn't work: {data['error']}"

        try:
            rendered = cls._render(name, data, cls._zone(messages))
        except Exception:  # a formatting slip must never cost the reply
            log.exception("Mock summary for %s failed", name)
            rendered = None
        if rendered:
            return rendered

        if isinstance(data, dict) and "message" in data:
            return str(data["message"])
        if isinstance(data, list):
            if not data:
                return "Nothing to show for that."
            lines = []
            for item in data[:8]:
                if isinstance(item, dict):
                    label = (
                        item.get("title")
                        or item.get("subject")
                        or item.get("content")
                        or item.get("name")
                        or ", ".join(
                            f"{k}: {v}" for k, v in item.items()
                            if isinstance(v, (str, int, float)) and k != "id"
                        )[:160]
                    )
                    lines.append(f"- {label}")
                else:
                    lines.append(f"- {item}")
            more = f"\n\n_(+{len(data) - 8} more)_" if len(data) > 8 else ""
            return "Here's what I found:\n\n" + "\n".join(lines) + more
        if isinstance(data, dict):
            facts = [
                f"- **{k.replace('_', ' ').capitalize()}:** {v}"
                for k, v in data.items()
                if isinstance(v, (str, int, float)) and k not in ("id",) and v != ""
            ]
            if facts:
                return "\n".join(facts[:12])
        return "Done."


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_provider: LLMProvider | None = None


def get_provider(force_reload: bool = False) -> LLMProvider:
    global _provider
    if _provider is not None and not force_reload:
        return _provider

    choice = settings.resolved_provider()
    try:
        if choice == "openai":
            _provider = OpenAIProvider()
        elif choice == "anthropic":
            _provider = AnthropicProvider()
        else:
            _provider = MockProvider()
    except Exception as exc:  # missing package, bad key, etc.
        log.warning("Falling back to mock provider (%s init failed): %s", choice, exc)
        _provider = MockProvider()
    log.info("LLM provider: %s", _provider.name)
    return _provider
