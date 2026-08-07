import json
import logging

from anthropic import AsyncAnthropicBedrock
from pydantic import BaseModel

from agents.schema import AgentFindings, Finding, MODEL, _inline_refs, _parse_leading_json, load_prompt
from orchestrator.merge import build_dashboard, build_detailed_report, merge_and_report, sort_findings

logger = logging.getLogger(__name__)

MERGE_MAX_TOKENS = 16000


class MergedFindings(BaseModel):
    findings: list[Finding]


MERGED_FINDINGS_TOOL_SCHEMA = _inline_refs(MergedFindings.model_json_schema())


def _repair_merge_tool_input(raw: dict) -> dict:
    # Same class of failure documented in agents/schema.py's _repair_tool_input:
    # this Bedrock route sometimes stringifies a nested array field instead of
    # emitting it as real JSON.
    if isinstance(raw.get("findings"), str):
        try:
            raw = {**raw, "findings": _parse_leading_json(raw["findings"])}
        except json.JSONDecodeError:
            pass
    return raw


def _render_findings_for_merge(agent_findings: list[AgentFindings]) -> str:
    blocks = []
    for af in agent_findings:
        for f in af.findings:
            blocks.append(
                f"agent: {af.agent}\n"
                f"id: {f.id}\n"
                f"location: page={f.location.page} section={f.location.section!r}\n"
                f"severity: {f.severity}\n"
                f"category: {f.category}\n"
                f"description: {f.description}\n"
                f"evidence: {f.evidence}\n"
                f"proposed_fix: {f.proposed_fix}\n"
            )
    return "\n---\n".join(blocks)


async def llm_merge_and_report(client: AsyncAnthropicBedrock, agent_findings: list[AgentFindings]) -> dict:
    """LLM-driven equivalent of merge_and_report(), using prompts/orchestrator.md's
    PHASE 2 merge instructions to catch semantic duplicates the deterministic
    dedupe() would miss (findings worded very differently by two agents).

    Falls back to the deterministic merge_and_report() on any failure — a Bedrock
    error, a malformed response that survives the JSON-repair pass, or a validation
    error — so a flaky LLM merge call never blocks producing a report at all.
    """
    all_findings = [f for af in agent_findings for f in af.findings]
    if not all_findings:
        return merge_and_report(agent_findings)

    prompt = load_prompt("orchestrator")
    rendered = _render_findings_for_merge(agent_findings)

    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MERGE_MAX_TOKENS,
            system=(
                f"{prompt}\n\n"
                "You are being called for PHASE 2 (Merge & rank) only. You will receive "
                "the raw findings from all four specialist agents below. Deduplicate "
                "semantically-overlapping findings (same underlying issue, even if worded "
                "very differently or filed under different section labels by different "
                "agents), keeping the more specific/complete description and recording "
                "every dropped duplicate's id in that finding's merged_from field. Do not "
                "invent, soften, or drop any non-duplicate finding. Do not sort or group "
                "the output yourself — return the deduplicated flat list only; sorting and "
                "dashboard/report shaping happen after your output, in code."
            ),
            tools=[{
                "name": "report_merged_findings",
                "description": "Report the deduplicated list of findings.",
                "input_schema": MERGED_FINDINGS_TOOL_SCHEMA,
            }],
            tool_choice={"type": "tool", "name": "report_merged_findings"},
            messages=[{"role": "user", "content": f"Raw findings from all 4 agents:\n\n{rendered}"}],
        )
        for block in response.content:
            if block.type == "tool_use":
                merged = MergedFindings.model_validate(_repair_merge_tool_input(block.input))
                sorted_findings = sort_findings(merged.findings)
                return {
                    "dashboard": build_dashboard(sorted_findings, agent_findings),
                    "detailed_report": build_detailed_report(sorted_findings),
                }
        raise RuntimeError(f"llm merge: no tool_use block in response (stop_reason={response.stop_reason})")
    except Exception:
        logger.warning("LLM-driven merge failed; falling back to deterministic merge.", exc_info=True)
        return merge_and_report(agent_findings)
