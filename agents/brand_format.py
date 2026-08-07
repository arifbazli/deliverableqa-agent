from anthropic import AsyncAnthropicBedrock

from agents.schema import AgentFindings, load_prompt, run_agent

PROMPT = load_prompt("brand_format")


async def check(client: AsyncAnthropicBedrock, document_context: str) -> AgentFindings:
    return await run_agent(client, "brand_format", PROMPT, document_context)
