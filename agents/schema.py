import json
from pathlib import Path
from typing import Literal

from anthropic import AsyncAnthropicBedrock
from pydantic import BaseModel
from pydantic.json_schema import SkipJsonSchema

MODEL = "global.anthropic.claude-sonnet-5"
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

AgentName = Literal["orchestrator", "consistency", "brand_format", "language_tone", "structure"]
Severity = Literal["critical", "warning", "suggestion"]


class Location(BaseModel):
    page: int | None
    section: str


class Finding(BaseModel):
    id: str
    location: Location
    severity: Severity
    category: str
    description: str
    evidence: str
    proposed_fix: str
    merged_from: list[str] = []
    # Set server-side during merge (dedupe() stamps it from the owning AgentFindings
    # batch) -- excluded from the tool-use schema so agents never fill it in
    # themselves; each agent already declares its identity once on AgentFindings.agent,
    # and asking it to repeat that per-finding would just be a redundant hallucination risk.
    agent: SkipJsonSchema[AgentName | None] = None


class AgentFindings(BaseModel):
    agent: AgentName
    findings: list[Finding]


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def _inline_refs(schema: dict) -> dict:
    # On this Bedrock route, a $ref/$defs schema makes claude-sonnet-5 collapse the whole
    # nested payload into a single stringified-JSON field (~90% of calls); inlining every
    # $ref fixes it to 12/12 in testing. output_config.format / strict tool schemas aren't
    # supported here either, so this plus forced tool_choice is the reliable path.
    defs = schema.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                ref_name = node["$ref"].split("/")[-1]
                resolved = resolve(defs[ref_name])
                return {**resolved, **{k: v for k, v in node.items() if k != "$ref"}}
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


AGENT_FINDINGS_TOOL_SCHEMA = _inline_refs(AgentFindings.model_json_schema())


def _parse_leading_json(text: str):
    # Even with the flattened schema, claude-sonnet-5 on this Bedrock route sometimes appends
    # a stray trailing character (e.g. an extra "}") after an otherwise-complete JSON value —
    # plain json.loads() rejects that as "Extra data". raw_decode() parses the leading valid
    # value and reports where it ended, so trailing garbage doesn't fail the whole call.
    return json.JSONDecoder().raw_decode(text)[0]


def _repair_tool_input(raw: dict) -> dict:
    # claude-sonnet-5 on this route occasionally stringifies a nested field instead of
    # emitting it as a real JSON array/object:
    #   (a) {"findings": "{\"agent\": ..., \"findings\": [...]}"}  — whole payload nested
    #   (b) {"agent": "...", "findings": "[{...}, ...]"}           — just findings stringified,
    #       sometimes with trailing garbage after the array (see _parse_leading_json above)
    # Detect and unwrap both before validation, rather than assuming the schema fix is total.
    if "agent" not in raw and "findings" in raw and isinstance(raw["findings"], str):
        try:
            nested = _parse_leading_json(raw["findings"])
            if isinstance(nested, dict) and "agent" in nested:
                raw = nested
        except json.JSONDecodeError:
            pass
    if isinstance(raw.get("findings"), str):
        try:
            raw = {**raw, "findings": _parse_leading_json(raw["findings"])}
        except json.JSONDecodeError:
            pass
    return raw


async def transcribe_page_images(client: AsyncAnthropicBedrock, images: list[tuple[str, str]]) -> str:
    """Transcribe one or more images (base64 data, media type) as a single unit -- e.g.
    several picture shapes making up one slide -- in one Claude call, in the order given.

    No tool-use here -- unlike run_agent(), this isn't producing a structured findings
    payload, just plain transcribed text, so the forced-tool-choice/JSON-repair machinery
    above doesn't apply.
    """
    image_blocks = [
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}}
        for image_b64, media_type in images
    ]
    response = await client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                *image_blocks,
                {
                    "type": "text",
                    "text": "Transcribe all text visible in these image(s), verbatim, preserving reading "
                            "order. Respond with only the transcribed text, no commentary. If there is no "
                            "visible text, respond with an empty string.",
                },
            ],
        }],
    )
    return "".join(block.text for block in response.content if block.type == "text").strip()


async def transcribe_page_image(client: AsyncAnthropicBedrock, image_b64: str, media_type: str = "image/png") -> str:
    return await transcribe_page_images(client, [(image_b64, media_type)])


async def run_agent(client: AsyncAnthropicBedrock, agent_name: AgentName, prompt: str, document_context: str) -> AgentFindings:
    response = await client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=prompt,
        tools=[{
            "name": "report_findings",
            "description": "Report findings in the shared DeliverableQA schema.",
            "input_schema": AGENT_FINDINGS_TOOL_SCHEMA,
        }],
        tool_choice={"type": "tool", "name": "report_findings"},
        messages=[{"role": "user", "content": document_context}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return AgentFindings.model_validate(_repair_tool_input(block.input))
    raise RuntimeError(f"{agent_name}: no tool_use block in response (stop_reason={response.stop_reason})")
