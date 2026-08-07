from difflib import SequenceMatcher

from agents.schema import AgentFindings, Finding

SEVERITY_RANK = {"critical": 0, "warning": 1, "suggestion": 2}
DUPLICATE_OVERLAP_THRESHOLD = 0.6


def _same_location(a: Finding, b: Finding) -> bool:
    return a.location.section == b.location.section and a.location.page == b.location.page


def _text_overlap(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def dedupe(agent_findings: list[AgentFindings]) -> list[Finding]:
    all_findings = [f for af in agent_findings for f in af.findings]
    kept: list[Finding] = []
    dropped_ids: set[str] = set()

    for i, current in enumerate(all_findings):
        if current.id in dropped_ids:
            continue
        merged_from: list[str] = []
        for other in all_findings[i + 1:]:
            if other.id in dropped_ids:
                continue
            if _same_location(current, other) and _text_overlap(current.description, other.description) > DUPLICATE_OVERLAP_THRESHOLD:
                if len(other.description) > len(current.description):
                    dropped_ids.add(current.id)
                    merged_from.append(current.id)
                    current = other
                else:
                    dropped_ids.add(other.id)
                    merged_from.append(other.id)
        if merged_from:
            current = current.model_copy(update={"merged_from": current.merged_from + merged_from})
        kept.append(current)

    return [f for f in kept if f.id not in dropped_ids]


def sort_findings(findings: list[Finding]) -> list[Finding]:
    def sort_key(f: Finding):
        page = f.location.page if f.location.page is not None else 10**9
        return (SEVERITY_RANK.get(f.severity, 99), page, f.location.section)

    return sorted(findings, key=sort_key)


def build_dashboard(findings: list[Finding], agent_findings: list[AgentFindings]) -> dict:
    severity_counts = {"critical": 0, "warning": 0, "suggestion": 0}
    agent_counts: dict[str, int] = {af.agent: len(af.findings) for af in agent_findings}
    for f in findings:
        severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1

    critical = [f for f in findings if f.severity == "critical"]
    return {
        "counts_by_severity": severity_counts,
        "counts_by_agent": agent_counts,
        "top_critical": [f.model_dump() for f in critical[:5]],
        "pass_fail": "fail" if critical else "pass",
        "total_findings": len(findings),
    }


def build_detailed_report(findings: list[Finding]) -> dict:
    grouped: dict[str, list[dict]] = {}
    for f in findings:
        grouped.setdefault(f.location.section, []).append(f.model_dump())
    return {"sections": grouped}


def merge_and_report(agent_findings: list[AgentFindings]) -> dict:
    deduped = dedupe(agent_findings)
    sorted_findings = sort_findings(deduped)
    return {
        "dashboard": build_dashboard(sorted_findings, agent_findings),
        "detailed_report": build_detailed_report(sorted_findings),
    }
