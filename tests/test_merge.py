from difflib import SequenceMatcher

from agents.schema import AgentFindings, Finding, Location
from orchestrator.merge import (
    build_dashboard,
    build_detailed_report,
    compute_delta,
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


class TestComputeDelta:
    def _report_from(self, findings):
        return merge_and_report([AgentFindings(agent="consistency", findings=findings)])

    def test_identical_reports_are_all_still_open(self):
        findings = [_finding("a", "Intro", description="Same issue every time.")]
        report = self._report_from(findings)

        delta = compute_delta(report, report)

        assert delta["counts"] == {"resolved": 0, "still_open": 1, "new": 0}

    def test_finding_present_in_previous_but_not_current_is_resolved(self):
        previous = self._report_from([_finding("a", "Intro", description="This will be fixed.")])
        current = self._report_from([])

        delta = compute_delta(previous, current)

        assert delta["counts"] == {"resolved": 1, "still_open": 0, "new": 0}
        assert delta["resolved"][0]["id"] == "a"

    def test_finding_present_in_current_but_not_previous_is_new(self):
        previous = self._report_from([])
        current = self._report_from([_finding("a", "Intro", description="A newly introduced problem.")])

        delta = compute_delta(previous, current)

        assert delta["counts"] == {"resolved": 0, "still_open": 0, "new": 1}
        assert delta["new"][0]["id"] == "a"

    def test_matches_by_location_and_similarity_not_by_id(self):
        # Re-runs generate fresh ids for the same underlying finding — the delta
        # must match on location + description similarity, never on id equality.
        previous = self._report_from([
            _finding("old-id-1", "Exec Summary", description="Numbers don't reconcile between sections here.")
        ])
        current = self._report_from([
            _finding("new-id-99", "Exec Summary", description="Numbers don't reconcile between the two sections here.")
        ])

        delta = compute_delta(previous, current)

        assert delta["counts"] == {"resolved": 0, "still_open": 1, "new": 0}
        assert delta["still_open"][0]["id"] == "new-id-99"

    def test_mixed_resolved_open_and_new(self):
        previous = self._report_from([
            _finding("a", "Intro", description="Issue A description text here."),
            _finding("b", "Risks", description="Issue B description text here."),
        ])
        current = self._report_from([
            _finding("a2", "Intro", description="Issue A description text here still."),  # matches a
            _finding("c", "Structure", description="Brand new issue C never seen before."),  # new
            # Issue B is gone -> resolved
        ])

        delta = compute_delta(previous, current)

        assert delta["counts"] == {"resolved": 1, "still_open": 1, "new": 1}
        assert delta["resolved"][0]["id"] == "b"
        assert delta["still_open"][0]["id"] == "a2"
        assert delta["new"][0]["id"] == "c"

    def test_does_not_double_match_two_current_findings_to_one_previous(self):
        # Two *different-location* current findings (so merge_and_report's own
        # dedupe won't collapse them) that both happen to be similar enough to
        # match the single previous finding. compute_delta must consume the
        # previous finding at most once -- the second current finding has to
        # come out as "new", not get silently treated as still_open twice.
        previous = self._report_from([
            _finding("a", "Intro", description="Duplicate-ish issue text one two three."),
        ])
        current = self._report_from([
            _finding("b", "Intro", description="Duplicate-ish issue text one two three four."),
            _finding("c", "Risks", description="Duplicate-ish issue text one two three four five."),
        ])

        delta = compute_delta(previous, current)

        assert delta["counts"]["resolved"] == 0
        assert delta["counts"]["still_open"] == 1
        assert delta["counts"]["new"] == 1
        # exactly one of b/c is matched; the other is new -- never both matched
        matched_id = delta["still_open"][0]["id"]
        new_id = delta["new"][0]["id"]
        assert {matched_id, new_id} == {"b", "c"}

    def test_empty_previous_and_current(self):
        empty = self._report_from([])

        delta = compute_delta(empty, empty)

        assert delta["counts"] == {"resolved": 0, "still_open": 0, "new": 0}

    def test_uses_a_lower_threshold_than_within_run_dedupe(self):
        # Real two-pass Bedrock output showed cross-run rewording drifts further than
        # within-run rewording -- this exact pair (an actual disclaimer finding from
        # two independent runs against the same document) sits between
        # DELTA_OVERLAP_THRESHOLD (0.45) and DUPLICATE_OVERLAP_THRESHOLD (0.6), so it
        # must match in compute_delta even though the equivalent dedupe() case would not.
        desc_a = (
            "No engagement scope disclaimer is present in the document. The ruleset "
            "requires an engagement scope disclaimer to be included somewhere in the deliverable."
        )
        desc_b = (
            "No confidentiality notice is present anywhere in the document. The rules "
            "require a confidentiality notice containing the word confidential."
        )
        ratio = SequenceMatcher(None, desc_a, desc_b).ratio()
        assert 0.45 < ratio <= 0.6, f"fixture drifted out of the intended threshold gap: {ratio}"

        previous = self._report_from([_finding("a", "Document-wide", description=desc_a)])
        current = self._report_from([_finding("b", "Document-wide", description=desc_b)])

        delta = compute_delta(previous, current)

        assert delta["counts"] == {"resolved": 0, "still_open": 1, "new": 0}
