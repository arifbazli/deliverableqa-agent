import argparse
import asyncio
import json
from pathlib import Path

from anthropic import AsyncAnthropicBedrock

from orchestrator.dispatch import run_agents
from orchestrator.merge import merge_and_report
from orchestrator.parse import parse_document, render_document_context

REPO_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = REPO_ROOT / "config"
ENGAGEMENT_TYPES = {"advisory", "audit", "tax", "consulting"}


def load_checklist(engagement_type: str) -> str:
    path = CONFIG_DIR / "checklists" / f"{engagement_type}.yaml"
    if not path.exists():
        raise ValueError(f"No checklist for engagement_type={engagement_type!r} at {path}")
    return path.read_text(encoding="utf-8")


def load_style_rules() -> str:
    return (CONFIG_DIR / "style_rules.yaml").read_text(encoding="utf-8")


async def run(document_path: Path, engagement_type: str, output_dir: Path) -> dict:
    sections = parse_document(document_path)
    checklist_yaml = load_checklist(engagement_type)
    style_rules_yaml = load_style_rules()
    document_context = render_document_context(sections, engagement_type, checklist_yaml, style_rules_yaml)

    client = AsyncAnthropicBedrock()
    agent_findings = await run_agents(client, document_context)
    result = merge_and_report(agent_findings)

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
    args = parser.parse_args()

    if not args.document.exists():
        raise SystemExit(f"Document not found: {args.document}")

    result = asyncio.run(run(args.document, args.engagement_type, args.output_dir))
    dashboard = result["dashboard"]
    print(f"Pass/fail: {dashboard['pass_fail']}")
    print(f"Total findings: {dashboard['total_findings']} ({dashboard['counts_by_severity']})")
    print(f"Written to: {args.output_dir / 'findings.json'}")


if __name__ == "__main__":
    main()
