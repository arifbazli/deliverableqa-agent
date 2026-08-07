from difflib import SequenceMatcher

from agents.schema import AgentFindings, Finding

SEVERITY_RANK = {"critical": 0, "warning": 1, "suggestion": 2}
# Within one run, agents describe the same real issue in noticeably similar language
# (they're looking at the same document at the same time) — 0.6 keeps distinct
# same-location findings from merging while still catching true overlaps.
DUPLICATE_OVERLAP_THRESHOLD = 0.6


def _same_location(a: Finding, b: Finding) -> bool:
    return a.location.section == b.location.section and a.location.page == b.location.page


def _text_overlap(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def dedupe(agent_findings: list[AgentFindings]) -> list[Finding]:
    all_findings = [f for af in agent_findings for f in af.findings]
    dropped_indices: set[int] = set()
    # merged_from[i] collects the ids absorbed into all_findings[i], keyed by index
    # (not id) so a later duplicate that becomes the new "winner" still accumulates
    # everything absorbed by the earlier index it replaced.
    merged_from: dict[int, list[str]] = {}

    for i, current in enumerate(all_findings):
        if i in dropped_indices:
            continue
        winner_index = i
        for j in range(i + 1, len(all_findings)):
            if j in dropped_indices:
                continue
            other = all_findings[j]
            winner = all_findings[winner_index]
            if not _same_location(winner, other):
                continue
            if _text_overlap(winner.description, other.description) <= DUPLICATE_OVERLAP_THRESHOLD:
                continue
            if len(other.description) > len(winner.description):
                dropped_indices.add(winner_index)
                merged_from.setdefault(j, []).extend(merged_from.pop(winner_index, []))
                merged_from[j].append(winner.id)
                winner_index = j
            else:
                dropped_indices.add(j)
                merged_from.setdefault(winner_index, []).append(other.id)

    kept: list[Finding] = []
    for i, finding in enumerate(all_findings):
        if i in dropped_indices:
            continue
        absorbed = merged_from.get(i)
        if absorbed:
            finding = finding.model_copy(update={"merged_from": finding.merged_from + absorbed})
        kept.append(finding)
    return kept


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
