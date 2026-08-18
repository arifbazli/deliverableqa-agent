import argparse
import asyncio
import json
from pathlib import Path

from anthropic import AsyncAnthropicBedrock

from orchestrator.dispatch import run_agents
from orchestrator.llm_delta import llm_compute_delta
from orchestrator.llm_merge import llm_merge_and_report
from orchestrator.merge import compute_delta, merge_and_report
from orchestrator.parse import parse_document_with_ocr_fallback, render_document_context

REPO_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = REPO_ROOT / "config"
ENGAGEMENT_TYPES = {"advisory", "audit", "tax", "consulting"}
# Generous ceiling for a single Bedrock call -- the SDK's own default (10 min)
# is already high, but a synchronous web upload against a large document has
# no other backstop, so give it more headroom rather than risk a timeout mid-run.
BEDROCK_TIMEOUT_SECONDS = 20 * 60


def load_checklist(engagement_type: str) -> str:
    path = CONFIG_DIR / "checklists" / f"{engagement_type}.yaml"
    if not path.exists():
        raise ValueError(f"No checklist for engagement_type={engagement_type!r} at {path}")
    return path.read_text(encoding="utf-8")


def load_style_rules() -> str:
    return (CONFIG_DIR / "style_rules.yaml").read_text(encoding="utf-8")


async def run(
    document_path: Path,
    engagement_type: str,
    output_dir: Path,
    previous_report: dict | None = None,
    use_llm_merge: bool = False,
    use_llm_delta: bool = False,
    document_name: str | None = None,
) -> dict:
    client = AsyncAnthropicBedrock(timeout=BEDROCK_TIMEOUT_SECONDS)
    sections = await parse_document_with_ocr_fallback(document_path, client)
    checklist_yaml = load_checklist(engagement_type)
    style_rules_yaml = load_style_rules()
    document_context = render_document_context(sections, engagement_type, checklist_yaml, style_rules_yaml)

    agent_findings = await run_agents(client, document_context)
    if use_llm_merge:
        result = await llm_merge_and_report(client, agent_findings)
    else:
        result = merge_and_report(agent_findings)

    if previous_report is not None:
        if use_llm_delta:
            result["delta"] = await llm_compute_delta(client, previous_report, result)
        else:
            result["delta"] = compute_delta(previous_report, result)

    result["document_name"] = document_name or document_path.name
    result["engagement_type"] = engagement_type

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "findings.json"
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the DeliverableQA pipeline against a deliverable.")
    parser.add_argument("document", type=Path, help="Path to the .docx/.pptx/.pdf deliverable")
    parser.add_argument(
        "--engagement-type",
        required=True,
        choices=sorted(ENGAGEMENT_TYPES),
        help="Engagement type — selects the checklist to apply",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "output",
        help="Directory to write findings.json into (default: ./output)",
    )
    parser.add_argument(
        "--previous-findings",
        type=Path,
        default=None,
        help="Path to a prior findings.json for the same document — adds a 'delta' "
             "section (resolved/still_open/new) comparing this run against it",
    )
    parser.add_argument(
        "--llm-merge",
        action="store_true",
        help="Use an LLM call (prompts/orchestrator.md) to merge/dedupe findings instead "
             "of the default deterministic merge — catches semantic duplicates worded very "
             "differently across agents, at the cost of one extra Bedrock call. Falls back "
             "to the deterministic merge automatically if the LLM call fails.",
    )
    parser.add_argument(
        "--llm-delta",
        action="store_true",
        help="Use an LLM call (prompts/delta_match.md) to semantically re-examine only the "
             "findings the deterministic delta couldn't match (--previous-findings) — catches "
             "a finding that's both reworded and relabeled to a new section between runs, "
             "which location+text-similarity matching structurally cannot see. No-op (no "
             "extra Bedrock call) when the deterministic delta already matched everything. "
             "Falls back to the deterministic delta automatically if the LLM call fails.",
    )
    args = parser.parse_args()

    if not args.document.exists():
        raise SystemExit(f"Document not found: {args.document}")

    previous_report = None
    if args.previous_findings is not None:
        if not args.previous_findings.exists():
            raise SystemExit(f"--previous-findings file not found: {args.previous_findings}")
        previous_report = json.loads(args.previous_findings.read_text(encoding="utf-8"))

    result = asyncio.run(run(
        args.document, args.engagement_type, args.output_dir, previous_report,
        use_llm_merge=args.llm_merge, use_llm_delta=args.llm_delta,
    ))
    dashboard = result["dashboard"]
    print(f"Pass/fail: {dashboard['pass_fail']}")
    print(f"Total findings: {dashboard['total_findings']} ({dashboard['counts_by_severity']})")
    if "delta" in result:
        counts = result["delta"]["counts"]
        print(f"Delta vs previous run: {counts['resolved']} resolved, {counts['still_open']} still open, {counts['new']} new")
    print(f"Written to: {args.output_dir / 'findings.json'}")
    print("View it at http://127.0.0.1:8000 (start the server with: uv run server.py)")


if __name__ == "__main__":
    main()
