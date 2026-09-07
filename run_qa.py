import argparse
import asyncio
import json
from pathlib import Path

import anthropic
import pydantic
from anthropic import AsyncAnthropicBedrock

from orchestrator.dispatch import run_agents
from orchestrator.llm_delta import llm_compute_delta
from orchestrator.llm_merge import llm_merge_and_report
from orchestrator.merge import compute_delta, merge_and_report
from orchestrator.parse import parse_document_with_ocr_fallback, render_document_context

REPO_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = REPO_ROOT / "config"
ENGAGEMENT_TYPES = {"advisory", "audit", "tax", "consulting"}
# A single hung agent call previously could leave a live demo looking frozen for up
# to this long before anything surfaced -- 5 minutes is still generous for a real
# Bedrock call against a large document, and shrinks that worst case substantially.
BEDROCK_TIMEOUT_SECONDS = 5 * 60


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

    agent_findings, agent_errors = await run_agents(client, document_context)
    if agent_errors and len(agent_errors) == len(agent_findings):
        # Every agent failed -- producing a report here would render as a misleadingly
        # clean "pass, 0 findings" indistinguishable from a genuinely clean document.
        # Re-raise the first failure's real exception (not a generic wrapper) so
        # server.py's dedicated except branches (credentials, Bedrock API errors,
        # schema validation) still respond with the right, specific error.
        raise next(iter(agent_errors.values()))

    if use_llm_merge:
        result = await llm_merge_and_report(client, agent_findings)
    else:
        result = merge_and_report(agent_findings)

    if agent_errors:
        # Partial failure: the report below only reflects whichever agents actually
        # succeeded. Surfacing this (rather than only logging it server-side) is the
        # difference between an honest partial report and a silent coverage gap.
        result["agent_errors"] = {name: f"{type(exc).__name__}: {exc}" for name, exc in agent_errors.items()}

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
        try:
            previous_report = json.loads(args.previous_findings.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"--previous-findings file is not valid JSON: {e}")

    # Mirrors server.py's exception handling so a CLI run fails as cleanly as a web
    # upload does, instead of a raw traceback -- previously the only difference
    # between the two paths was which one had actionable error messages.
    try:
        result = asyncio.run(run(
            args.document, args.engagement_type, args.output_dir, previous_report,
            use_llm_merge=args.llm_merge, use_llm_delta=args.llm_delta,
        ))
    except anthropic.APIStatusError as e:
        raise SystemExit(f"Claude API error ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        raise SystemExit("Could not reach Claude on Bedrock — check AWS credentials and network.")
    except pydantic.ValidationError as e:
        raise SystemExit(f"Claude returned a response that didn't match the expected findings schema: {e}")
    except ValueError as e:
        raise SystemExit(str(e))
    except RuntimeError as e:
        if "credentials" in str(e).lower():
            raise SystemExit(
                "AWS credentials could not be resolved — check they're set and still valid "
                "(env vars, ~/.aws/credentials, or an active SSO session)."
            )
        raise

    dashboard = result["dashboard"]
    print(f"Pass/fail: {dashboard['pass_fail']}")
    print(f"Total findings: {dashboard['total_findings']} ({dashboard['counts_by_severity']})")
    if "agent_errors" in result:
        print(f"WARNING: {len(result['agent_errors'])} agent(s) failed and returned no findings: {list(result['agent_errors'].keys())}")
    if "delta" in result:
        counts = result["delta"]["counts"]
        print(f"Delta vs previous run: {counts['resolved']} resolved, {counts['still_open']} still open, {counts['new']} new")
    print(f"Written to: {args.output_dir / 'findings.json'}")
    print("View it at http://127.0.0.1:8000 (start the server with: uv run server.py)")


if __name__ == "__main__":
    main()
