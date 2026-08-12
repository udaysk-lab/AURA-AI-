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
    confirmation flow can be exercised offline and in tests.
    """

    name = "mock"

    ROUTES: list[tuple[str, str, dict]] = [
        (r"\b(brief|briefing|my day|today.?s? (plan|schedule)|catch me up)\b",
         "get_daily_briefing", {}),
        (r"\b(inbox|unread|summar\w+ (my )?email|email summary)\b",
         "summarize_inbox", {}),
        (r"\b(urgent|important) email", "search_emails", {"query": "urgent"}),
        (r"\b(free|available|availability|open slot)\b", "find_free_time", {}),
        (r"\b(conflict|double.?book)\b", "detect_conflicts", {}),
        (r"\b(schedule|book|set up|create).{0,20}\b(meeting|event|call)\b",
         "create_event", {}),
        (r"\b(my )?(calendar|meetings|agenda)\b", "list_events", {}),
        (r"\b(remind me|add (a )?task|to.?do|create (a )?task)\b",
         "create_task", {}),
        (r"\b(my )?tasks?\b", "list_tasks", {}),
        (r"\b(remember|note that|keep in mind|i prefer)\b", "save_memory", {}),
        (r"\b(what do you (know|remember))\b", "search_memory", {}),
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
                "I'm running in **demo mode** — no model API key is configured, so I'm "
                "using a keyword router instead of real reasoning.\n\n"
                "Try: *\"what's my day look like\"*, *\"summarise my inbox\"*, "
                "*\"add a task to review the pitch deck\"*, *\"when am I free tomorrow\"*, "
                "or *\"remember that I prefer morning meetings\"*.\n\n"
                "Add `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` to `.env` to switch on the "
                "real agent."
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
            return {"title": title[:200] or "New task", "priority": priority}
        if tool_name == "save_memory":
            content = re.sub(
                r"^\s*(please\s+)?(remember that|remember|note that|keep in mind that)\s*",
                "",
                text.strip(),
                flags=re.IGNORECASE,
            )
            return {"content": content[:500] or text[:500], "kind": "preference"}
        if tool_name == "create_event":
            return {"title": text[:120].strip() or "New meeting"}
        if tool_name == "search_memory":
            return {"query": text[:200]}
        return {}

    @staticmethod
    def _summarise_tool_output(messages: list[dict]) -> str:
        raw = messages[-1].get("content", "")
        name = messages[-1].get("name", "tool")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return str(raw)[:1500]

        if isinstance(data, dict) and data.get("error"):
            return f"That didn't work: {data['error']}"
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
                        or json.dumps(item)[:120]
                    )
                    lines.append(f"- {label}")
                else:
                    lines.append(f"- {item}")
            more = f"\n\n_(+{len(data) - 8} more)_" if len(data) > 8 else ""
            return f"Here's what I found via `{name}`:\n\n" + "\n".join(lines) + more
        return f"```json\n{json.dumps(data, indent=2, default=str)[:1500]}\n```"


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
