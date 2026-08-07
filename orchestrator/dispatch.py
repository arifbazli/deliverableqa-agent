import operator
from typing import Annotated, TypedDict

from anthropic import AsyncAnthropicBedrock
from langgraph.graph import END, START, StateGraph

from agents import brand_format, consistency, language_tone, structure
from agents.schema import AgentFindings

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
        result = await module.check(client, state["document_context"])
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
