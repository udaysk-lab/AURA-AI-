"""A scripted model for the task-loop tests: it returns the responses it was given, in order, and
records every `messages` / `tools` it was shown."""

from __future__ import annotations

from collections.abc import Callable

from app.llm import LLMResponse, MockProvider, ToolCall, Usage


def call(name: str, id: str | None = None, **arguments) -> ToolCall:
    return ToolCall(id=id or f"call_{name}", name=name, arguments=arguments)


def reply(text: str = "", *calls: ToolCall) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=list(calls), usage=Usage(input_tokens=100, output_tokens=20, model="test"))


class ScriptedProvider:
    name = "scripted"

    def __init__(self, responses: list[LLMResponse | Callable[[list[dict], list], LLMResponse]]):
        self.responses = list(responses)
        self.seen: list[list[dict]] = []
        self.tools_seen: list[list] = []

    def complete(self, messages, tools=None, model=None):
        self.seen.append([dict(m) for m in messages])
        self.tools_seen.append(list(tools or []))
        if not self.responses:
            raise AssertionError("the scripted model was asked for more responses than it was given")
        nxt = self.responses.pop(0)
        return nxt(messages, tools) if callable(nxt) else nxt

    def embed(self, texts):
        return []

    @property
    def systems(self) -> list[str]:
        return [m[0]["content"] for m in self.seen]

    def tool_messages(self, n: int = -1) -> list[dict]:
        return [m for m in self.seen[n] if m.get("role") == "tool"]


class RecordingMock(MockProvider):
    """The keyword mock, remembering what it was shown."""

    def __init__(self):
        self.seen: list[list[dict]] = []

    def complete(self, messages, tools=None, model=None):
        self.seen.append([dict(m) for m in messages])
        out = super().complete(messages, tools, model)
        out.usage = Usage(input_tokens=50, output_tokens=10, model="test")
        return out
