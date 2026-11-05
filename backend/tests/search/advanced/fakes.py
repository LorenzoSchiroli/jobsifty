"""A chat model that plays back a script, for agent tests."""
import asyncio
import uuid
from dataclasses import dataclass
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


@dataclass
class Delayed:
    """A script entry answered only after `seconds` (async calls only), to test time limits."""
    seconds: float
    then: Any


class ScriptedChatModel(BaseChatModel):
    """Answers each call with the next `script` entry (structured output: the next `structured` one).

    An Exception entry is raised instead of returned, a Delayed one is answered late. Every prompt
    received is kept in `calls`; the kwargs of the last bind_tools / with_structured_output call in
    `bound_kwargs` / `structured_kwargs`.
    """

    # Any, not AIMessage | Exception: pydantic would try to coerce an Exception into an AIMessage.
    script: list[Any] = []  # AIMessage | Exception | Delayed
    structured: list[Any] = []  # BaseModel | Exception | Delayed
    calls: list[list[BaseMessage]] = []
    bound_kwargs: dict = {}
    structured_kwargs: dict = {}

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _pop(self, queue: list, messages: list[BaseMessage]):
        self.calls.append(list(messages))
        if not queue:
            raise AssertionError("ScriptedChatModel: script exhausted")
        return queue.pop(0)

    @staticmethod
    def _answer(entry):
        if isinstance(entry, Exception):
            raise entry
        return entry

    async def _apop(self, queue: list, messages: list[BaseMessage]):
        entry = self._pop(queue, messages)
        if isinstance(entry, Delayed):
            await asyncio.sleep(entry.seconds)
            entry = entry.then
        return self._answer(entry)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        message = self._answer(self._pop(self.script, messages))
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=await self._apop(self.script, messages))])

    def bind_tools(self, tools, **kwargs) -> "ScriptedChatModel":
        self.bound_kwargs = kwargs
        return self

    def with_structured_output(self, schema, **kwargs) -> Any:
        self.structured_kwargs = kwargs

        def messages_of(prompt):
            return prompt.to_messages() if hasattr(prompt, "to_messages") else list(prompt)

        def answer(prompt):
            return self._answer(self._pop(self.structured, messages_of(prompt)))

        async def aanswer(prompt):
            return await self._apop(self.structured, messages_of(prompt))

        return RunnableLambda(answer, afunc=aanswer)
