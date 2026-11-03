"""The advanced-search graph: agent ⇄ tools, then finalize.

agent (LLM, tools bound) ─► tools ─► agent ...; the agent goes to finalize on a reply without
tool calls, at the turn cap or past the deadline, tools go to finalize once `finish` ran.
An LLM failure never raises: the agent step ends the run as partial, so does a failed finalize.
"""
import logging
import time

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command

from backend.search.advanced.budget import MAX_TURNS
from backend.search.advanced.context import AgentContext
from backend.search.advanced.finalize import partial_result, run_finalize
from backend.search.advanced.prompt import compact_history, system_prompt
from backend.search.advanced.state import AgentState
from backend.search.advanced.tools import TOOLS, tools_step

logger = logging.getLogger(__name__)


def _usage(message: AIMessage) -> dict:
    meta = message.usage_metadata or {}
    return {"llm_calls": 1, "input_tokens": meta.get("input_tokens", 0), "output_tokens": meta.get("output_tokens", 0)}


async def agent_node(state: AgentState, runtime: Runtime[AgentContext]) -> Command:
    ctx = runtime.context
    if state.get("turns", 0) >= MAX_TURNS or time.monotonic() > ctx.deadline:
        return Command(goto="finalize")
    llm = ctx.llm.bind_tools(TOOLS, parallel_tool_calls=False)
    try:
        message = await llm.ainvoke([SystemMessage(system_prompt(state, ctx)), *compact_history(state["messages"])])
    except Exception:
        logger.exception("advanced agent LLM call failed; the run ends partial")
        return Command(goto=END, update=partial_result(state))
    update = {"messages": [message], "turns": 1, "usage": _usage(message)}
    if message.invalid_tool_calls and not message.tool_calls:
        logger.warning("advanced agent tool call did not parse; the run ends partial: %s", message.invalid_tool_calls)
        return Command(goto=END, update={**update, **partial_result(state)})
    return Command(goto="tools" if message.tool_calls else "finalize", update=update)


async def finalize_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    try:
        result = await run_finalize(state, runtime.context)
    except Exception:
        logger.exception("advanced finalize failed; the run ends partial")
        return partial_result(state)
    # The structured output does not expose token counts: only the call is counted.
    return {**result, "usage": {"llm_calls": 1}}


def _after_tools(state: AgentState) -> str:
    return "finalize" if state.get("finished") else "agent"


def build_graph(checkpointer) -> CompiledStateGraph:
    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node("agent", agent_node, destinations=("tools", "finalize", END))
    graph.add_node("tools", tools_step)
    graph.add_node("finalize", finalize_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("tools", _after_tools, ["agent", "finalize"])
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
