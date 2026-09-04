import logging
import operator
from typing import Annotated, TypedDict

from anthropic import AsyncAnthropicBedrock
from langgraph.graph import END, START, StateGraph

from agents import brand_format, consistency, language_tone, structure
from agents.schema import AgentFindings

logger = logging.getLogger(__name__)

AGENT_MODULES = {
    "consistency": consistency,
    "brand_format": brand_format,
    "language_tone": language_tone,
    "structure": structure,
}


class QAState(TypedDict):
    document_context: str
    agent_findings: Annotated[list[AgentFindings], operator.add]
    merged: dict


def _make_node(name: str, client: AsyncAnthropicBedrock):
    module = AGENT_MODULES[name]

    async def node(state: QAState) -> dict:
        try:
            result = await module.check(client, state["document_context"])
        except Exception:
            # Without this, one agent failing (rate limit, transient Bedrock error, a
            # response that fails validation) propagates out of the whole ainvoke() and
            # discards the other 3 agents' already-completed, expensive LLM results too.
            # An empty-findings placeholder lets the run still produce a report from
            # whichever agents succeeded, at the cost of an honest gap for this one.
            logger.exception("Agent %r failed; continuing with the other agents.", name)
            result = AgentFindings(agent=name, findings=[])
        return {"agent_findings": [result]}

    return node


def build_graph(client: AsyncAnthropicBedrock):
    graph = StateGraph(QAState)
    for name in AGENT_MODULES:
        graph.add_node(name, _make_node(name, client))
        graph.add_edge(START, name)
        graph.add_edge(name, END)
    return graph.compile()


async def run_agents(client: AsyncAnthropicBedrock, document_context: str) -> list[AgentFindings]:
    compiled = build_graph(client)
    result = await compiled.ainvoke({"document_context": document_context, "agent_findings": [], "merged": {}})
    return result["agent_findings"]
