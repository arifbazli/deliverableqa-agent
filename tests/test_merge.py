from agents.schema import AgentFindings, Finding, Location
from orchestrator.merge import (
    build_dashboard,
    build_detailed_report,
    dedupe,
    merge_and_report,
    sort_findings,
)


def _finding(id, section, severity="warning", description="A finding.", page=None, **kwargs):
    return Finding(
        id=id,
        location=Location(page=page, section=section),
        severity=severity,
        category=kwargs.get("category", "test"),
        description=description,
        evidence=kwargs.get("evidence", "evidence text"),
        proposed_fix=kwargs.get("proposed_fix", "fix text"),
    )


class TestDedupe:
    def test_no_overlap_keeps_all(self):
        af = [
            AgentFindings(agent="consistency", findings=[_finding("c1", "Intro", description="Numbers don't match.")]),
            AgentFindings(agent="structure", findings=[_finding("s1", "Risks", description="Missing section.")]),
        ]

        result = dedupe(af)

        assert {f.id for f in result} == {"c1", "s1"}

    def test_same_location_similar_description_merges(self):
        af = [
            AgentFindings(agent="consistency", findings=[
                _finding("c1", "Exec Summary", description="Executive summary states 18% but findings say 12%.")
            ]),
            AgentFindings(agent="language_tone", findings=[
                _finding("lt1", "Exec Summary", description="Executive summary states 18% growth but findings state 12% growth here.")
            ]),
        ]

        result = dedupe(af)

        assert len(result) == 1
        assert result[0].id == "lt1"  # longer description wins
        assert "c1" in result[0].merged_from

    def test_same_location_different_description_keeps_both(self):
        af = [
            AgentFindings(agent="consistency", findings=[
                _finding("c1", "Exec Summary", description="Numeric mismatch between sections.")
            ]),
            AgentFindings(agent="brand_format", findings=[
                _finding("bf1", "Exec Summary", description="Missing confidentiality disclaimer entirely.")
            ]),
        ]

        result = dedupe(af)

        assert {f.id for f in result} == {"c1", "bf1"}

    def test_different_location_same_description_keeps_both(self):
        af = [
            AgentFindings(agent="consistency", findings=[
                _finding("c1", "Exec Summary", description="Same wording repeated verbatim here for the test.")
            ]),
            AgentFindings(agent="consistency", findings=[
                _finding("c2", "Findings", description="Same wording repeated verbatim here for the test.")
            ]),
        ]

        result = dedupe(af)

        assert {f.id for f in result} == {"c1", "c2"}

    def test_page_is_part_of_location_identity(self):
        af = [
            AgentFindings(agent="consistency", findings=[
                _finding("c1", "Slide 1", page=1, description="Some overlapping finding description text here.")
            ]),
            AgentFindings(agent="consistency", findings=[
                _finding("c2", "Slide 1", page=2, description="Some overlapping finding description text here.")
            ]),
        ]

        result = dedupe(af)

        assert {f.id for f in result} == {"c1", "c2"}

    def test_empty_input(self):
        assert dedupe([]) == []

    def test_three_way_duplicate_chain_merges_into_one(self):
        # Regression test: when the *later* item in a pair is the "winner" (longer
        # description), earlier dedupe() bookkeeping tracked drops by id and never
        # marked the winner's own index as dropped-when-replaced, so a 3rd duplicate
        # comparing against an already-replaced "current" could let the same winner
        # id get appended to `kept` more than once.
        af = [
            AgentFindings(agent="consistency", findings=[
                _finding("c1", "Exec Summary", description="Executive summary states 18% but findings say 12%.")
            ]),
            AgentFindings(agent="language_tone", findings=[
                _finding("lt1", "Exec Summary", description="Executive summary states 18% growth but findings state 12% growth here.")
            ]),
            AgentFindings(agent="structure", findings=[
                _finding("s1", "Exec Summary", description="Executive summary states 18% growth but findings section states 12% growth here too.")
            ]),
        ]

        result = dedupe(af)

        assert len(result) == 1
        winner = result[0]
        assert winner.id == "s1"  # longest description wins
        assert set(winner.merged_from) == {"c1", "lt1"}


class TestSortFindings:
    def test_sorts_by_severity_then_page_then_section(self):
        findings = [
            _finding("a", "B", severity="warning", page=2),
            _finding("b", "A", severity="critical", page=1),
            _finding("c", "A", severity="warning", page=1),
            _finding("d", "A", severity="suggestion", page=1),
        ]

        result = sort_findings(findings)

        assert [f.id for f in result] == ["b", "c", "a", "d"]

    def test_null_page_sorts_last_within_severity(self):
        findings = [
            _finding("a", "X", severity="warning", page=None),
            _finding("b", "X", severity="warning", page=5),
        ]

        result = sort_findings(findings)

        assert [f.id for f in result] == ["b", "a"]


class TestBuildDashboard:
    def test_counts_and_pass_fail(self):
        findings = [
            _finding("a", "X", severity="critical"),
            _finding("b", "X", severity="warning"),
            _finding("c", "X", severity="warning"),
        ]
        agent_findings = [
            AgentFindings(agent="consistency", findings=[findings[0]]),
            AgentFindings(agent="structure", findings=findings[1:]),
        ]

        dashboard = build_dashboard(findings, agent_findings)

        assert dashboard["counts_by_severity"] == {"critical": 1, "warning": 2, "suggestion": 0}
        assert dashboard["counts_by_agent"] == {"consistency": 1, "structure": 2}
        assert dashboard["pass_fail"] == "fail"
        assert dashboard["total_findings"] == 3

    def test_pass_when_no_critical(self):
        findings = [_finding("a", "X", severity="warning")]
        dashboard = build_dashboard(findings, [AgentFindings(agent="structure", findings=findings)])

        assert dashboard["pass_fail"] == "pass"

    def test_top_critical_capped_at_five(self):
        findings = [_finding(f"c{i}", "X", severity="critical") for i in range(8)]
        dashboard = build_dashboard(findings, [AgentFindings(agent="structure", findings=findings)])

        assert len(dashboard["top_critical"]) == 5


class TestBuildDetailedReport:
    def test_groups_by_section(self):
        findings = [
            _finding("a", "Intro"),
            _finding("b", "Intro"),
            _finding("c", "Risks"),
        ]

        report = build_detailed_report(findings)

        assert set(report["sections"].keys()) == {"Intro", "Risks"}
        assert len(report["sections"]["Intro"]) == 2
        assert len(report["sections"]["Risks"]) == 1


class TestMergeAndReport:
    def test_end_to_end_shape(self):
        af = [
            AgentFindings(agent="consistency", findings=[_finding("c1", "Intro", severity="critical")]),
            AgentFindings(agent="structure", findings=[_finding("s1", "Risks", severity="warning")]),
        ]

        result = merge_and_report(af)

        assert "dashboard" in result and "detailed_report" in result
        assert result["dashboard"]["total_findings"] == 2
        assert result["dashboard"]["pass_fail"] == "fail"
