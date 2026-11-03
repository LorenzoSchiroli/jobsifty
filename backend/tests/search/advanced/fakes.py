"""A chat model that plays back a script, for agent tests."""
import uuid
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda


def tool_call(name: str, args: dict, id: str | None = None) -> AIMessage:
    """An assistant message with a single tool call."""
    return AIMessage(content="", tool_calls=[
        {"name": name, "args": args, "id": id or f"call-{uuid.uuid4().hex[:8]}", "type": "tool_call"}
    ])


class ScriptedChatModel(BaseChatModel):
    """Answers each call with the next `script` entry (structured output: the next `structured` one).

    An Exception entry is raised instead of returned. Every prompt received is kept in `calls`.
    """

    # Any, not AIMessage | Exception: pydantic would try to coerce an Exception into an AIMessage.
    script: list[Any] = []  # AIMessage | Exception
    structured: list[Any] = []  # BaseModel | Exception
    calls: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _next(self, queue: list, messages: list[BaseMessage]):
        self.calls.append(list(messages))
        if not queue:
            raise AssertionError("ScriptedChatModel: script exhausted")
        entry = queue.pop(0)
        if isinstance(entry, Exception):
            raise entry
        return entry

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(self.script, messages))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return self._generate(messages, stop=stop, **kwargs)

    def bind_tools(self, tools, **kwargs) -> "ScriptedChatModel":
        return self

    def with_structured_output(self, schema, **kwargs) -> Any:
        def answer(prompt):
            messages = prompt.to_messages() if hasattr(prompt, "to_messages") else list(prompt)
            return self._next(self.structured, messages)

        async def aanswer(prompt):
            return answer(prompt)

        return RunnableLambda(answer, afunc=aanswer)
