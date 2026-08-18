import json
import logging
from typing import Literal

from anthropic import AsyncAnthropicBedrock
from pydantic import BaseModel

from agents.schema import MODEL, _inline_refs, _parse_leading_json, load_prompt
from orchestrator.merge import compute_delta

logger = logging.getLogger(__name__)

DELTA_MAX_TOKENS = 8000


class DeltaMatch(BaseModel):
    old_id: str
    new_id: str
    confidence: Literal["high", "medium", "low"]
    reasoning: str


class DeltaResolved(BaseModel):
    old_id: str
    reasoning: str


class LlmDeltaResult(BaseModel):
    matches: list[DeltaMatch]
    resolved: list[DeltaResolved]


LLM_DELTA_TOOL_SCHEMA = _inline_refs(LlmDeltaResult.model_json_schema())


def _repair_delta_tool_input(raw: dict) -> dict:
    # Same class of failure documented in agents/schema.py's _repair_tool_input and
    # llm_merge.py's _repair_merge_tool_input: this Bedrock route sometimes stringifies
    # a nested array field instead of emitting it as real JSON.
    for key in ("matches", "resolved"):
        if isinstance(raw.get(key), str):
            try:
                raw = {**raw, key: _parse_leading_json(raw[key])}
            except json.JSONDecodeError:
                pass
    return raw


def _render_findings_for_delta(findings: list[dict]) -> str:
    fields = ("id", "location", "severity", "category", "description", "evidence", "proposed_fix")
    trimmed = [{k: f[k] for k in fields} for f in findings]
    return json.dumps(trimmed, indent=2)


async def llm_compute_delta(client: AsyncAnthropicBedrock, previous_report: dict, current_report: dict) -> dict:
    """LLM-driven refinement of compute_delta(): runs the deterministic delta first,
    then asks Claude to semantically re-examine ONLY the leftovers -- findings that
    didn't structurally match -- to catch one that was both reworded AND relabeled to
    a new section between runs (compute_delta()'s location+text-overlap match can
    never see this, since both signals changed at once).

    Falls back to the plain deterministic result on any failure -- an API error, a
    malformed response, or a validation failure -- so a flaky LLM call never blocks
    producing a delta at all, and never partially-trusts a broken one either.
    """
    det = compute_delta(previous_report, current_report)
    old_candidates = det["resolved"]
    new_candidates = det["new"]
    if not old_candidates or not new_candidates:
        return det

    valid_old_ids = {f["id"] for f in old_candidates}
    valid_new_ids = {f["id"] for f in new_candidates}
    old_by_id = {f["id"]: f for f in old_candidates}
    new_by_id = {f["id"]: f for f in new_candidates}

    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=DELTA_MAX_TOKENS,
            system=load_prompt("delta_match"),
            tools=[{
                "name": "report_delta_matches",
                "description": "Report which old and new findings are the same underlying issue.",
                "input_schema": LLM_DELTA_TOOL_SCHEMA,
            }],
            tool_choice={"type": "tool", "name": "report_delta_matches"},
            messages=[{
                "role": "user",
                "content": (
                    f"OLD_FINDINGS:\n{_render_findings_for_delta(old_candidates)}\n\n"
                    f"NEW_FINDINGS:\n{_render_findings_for_delta(new_candidates)}"
                ),
            }],
        )
        for block in response.content:
            if block.type == "tool_use":
                # Fail-strict by design, same rationale as llm_merge_and_report(): a
                # single bad match (an invented id, a double-claimed new_id, an old_id
                # silently dropped) discards the WHOLE refinement rather than applying
                # the matches that did validate -- the deterministic result is always
                # structurally safe (it can only miss a match, never fabricate one), so
                # falling all the way back to it costs nothing worse than one extra
                # Bedrock call.
                parsed = LlmDeltaResult.model_validate(_repair_delta_tool_input(block.input))

                invented_old = [m.old_id for m in parsed.matches if m.old_id not in valid_old_ids]
                invented_old += [r.old_id for r in parsed.resolved if r.old_id not in valid_old_ids]
                invented_new = [m.new_id for m in parsed.matches if m.new_id not in valid_new_ids]
                if invented_old or invented_new:
                    raise ValueError(
                        f"LLM delta match returned id(s) not in the original input: "
                        f"old={invented_old} new={invented_new}"
                    )

                seen_new_ids = [m.new_id for m in parsed.matches]
                if len(seen_new_ids) != len(set(seen_new_ids)):
                    raise ValueError("LLM delta match reused the same new_id across multiple matches")

                accounted_old_ids = [m.old_id for m in parsed.matches] + [r.old_id for r in parsed.resolved]
                if sorted(accounted_old_ids) != sorted(valid_old_ids):
                    raise ValueError(
                        "LLM delta match didn't place every old_id in exactly one of matches/resolved"
                    )

                still_open = list(det["still_open"])
                matched_new_ids: set[str] = set()
                for m in parsed.matches:
                    logger.info(
                        "LLM delta match: old=%r new=%r confidence=%s reasoning=%s",
                        m.old_id, m.new_id, m.confidence, m.reasoning,
                    )
                    still_open.append(new_by_id[m.new_id])
                    matched_new_ids.add(m.new_id)

                resolved = [old_by_id[r.old_id] for r in parsed.resolved]
                new = [f for f in det["new"] if f["id"] not in matched_new_ids]

                return {
                    "resolved": resolved,
                    "still_open": still_open,
                    "new": new,
                    "counts": {
                        "resolved": len(resolved),
                        "still_open": len(still_open),
                        "new": len(new),
                    },
                }
        raise RuntimeError(f"llm delta match: no tool_use block in response (stop_reason={response.stop_reason})")
    except Exception:
        logger.warning("LLM-driven delta matching failed; falling back to deterministic delta.", exc_info=True)
        return det
