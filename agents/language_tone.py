from anthropic import AsyncAnthropicBedrock

from agents.schema import AgentFindings, load_prompt, run_agent

PROMPT = load_prompt("language_tone")


async def check(client: AsyncAnthropicBedrock, document_context: str) -> AgentFindings:
    return await run_agent(client, "language_tone", PROMPT, document_context)
