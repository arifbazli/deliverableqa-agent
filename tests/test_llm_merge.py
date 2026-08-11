from unittest.mock import AsyncMock, MagicMock

from agents.schema import AgentFindings, Finding, Location
from orchestrator.llm_merge import llm_merge_and_report
from orchestrator.merge import merge_and_report


def _finding(id, section, severity="warning", description="d"):
    return Finding(
        id=id, location=Location(page=None, section=section), severity=severity,
        category="c", description=description, evidence="e", proposed_fix="f",
    )


def _mock_client_returning_tool_use(input_dict):
    block = MagicMock()
    block.type = "tool_use"
    block.input = input_dict
    response = MagicMock()
    response.content = [block]
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=response)
    return client


def _mock_client_raising(exc):
    client = AsyncMock()
    client.messages.create = AsyncMock(side_effect=exc)
    return client


class TestLlmMergeAndReport:
    async def test_empty_findings_short_circuits_without_calling_client(self):
        client = AsyncMock()
        agent_findings = [AgentFindings(agent="consistency", findings=[])]

        result = await llm_merge_and_report(client, agent_findings)

        assert result["dashboard"]["total_findings"] == 0
        client.messages.create.assert_not_called()

    async def test_happy_path_uses_llm_output_and_shapes_report(self):
        agent_findings = [
            AgentFindings(agent="consistency", findings=[_finding("c1", "Intro", "critical")]),
            AgentFindings(agent="structure", findings=[_finding("s1", "Risks", "warning")]),
        ]
        client = _mock_client_returning_tool_use({
            "findings": [
                {"id": "c1", "location": {"page": None, "section": "Intro"}, "severity": "critical",
                 "category": "c", "description": "d", "evidence": "e", "proposed_fix": "f", "merged_from": ["dropped"]},
            ]
        })

        result = await llm_merge_and_report(client, agent_findings)

        assert result["dashboard"]["total_findings"] == 1
        assert result["detailed_report"]["sections"]["Intro"][0]["merged_from"] == ["dropped"]

    async def test_restores_agent_by_id_since_llm_output_never_carries_it(self):
        # The tool schema excludes Finding.agent (see agents/schema.py), so the LLM's
        # merged findings never carry it -- llm_merge_and_report() must restore it by
        # matching the finding id back against the original agent_findings.
        agent_findings = [
            AgentFindings(agent="consistency", findings=[_finding("c1", "Intro", "critical")]),
            AgentFindings(agent="structure", findings=[_finding("s1", "Risks", "warning")]),
        ]
        client = _mock_client_returning_tool_use({
            "findings": [
                {"id": "c1", "location": {"page": None, "section": "Intro"}, "severity": "critical",
                 "category": "c", "description": "d", "evidence": "e", "proposed_fix": "f"},
                {"id": "s1", "location": {"page": None, "section": "Risks"}, "severity": "warning",
                 "category": "c", "description": "d", "evidence": "e", "proposed_fix": "f"},
            ]
        })

        result = await llm_merge_and_report(client, agent_findings)

        findings_by_id = {
            f["id"]: f["agent"]
            for findings in result["detailed_report"]["sections"].values()
            for f in findings
        }
        assert findings_by_id == {"c1": "consistency", "s1": "structure"}

    async def test_calls_with_correct_model_and_forced_tool_choice(self):
        agent_findings = [AgentFindings(agent="consistency", findings=[_finding("c1", "Intro")])]
        client = _mock_client_returning_tool_use({"findings": []})

        await llm_merge_and_report(client, agent_findings)

        call_kwargs = client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "global.anthropic.claude-sonnet-5"
        assert call_kwargs["tool_choice"] == {"type": "tool", "name": "report_merged_findings"}

    async def test_falls_back_to_deterministic_merge_on_api_error(self):
        agent_findings = [
            AgentFindings(agent="consistency", findings=[_finding("c1", "Intro", "critical")]),
            AgentFindings(agent="structure", findings=[_finding("s1", "Risks", "warning")]),
        ]
        client = _mock_client_raising(RuntimeError("simulated failure"))

        result = await llm_merge_and_report(client, agent_findings)

        # Falls back to merge_and_report()'s deterministic result -- both original
        # findings survive since they don't overlap by the dedupe() rules either.
        assert result["dashboard"]["total_findings"] == 2

    async def test_falls_back_when_no_tool_use_block_in_response(self):
        text_block = MagicMock()
        text_block.type = "text"
        response = MagicMock()
        response.content = [text_block]
        response.stop_reason = "end_turn"
        client = AsyncMock()
        client.messages.create = AsyncMock(return_value=response)

        agent_findings = [AgentFindings(agent="consistency", findings=[_finding("c1", "Intro")])]

        result = await llm_merge_and_report(client, agent_findings)

        assert result["dashboard"]["total_findings"] == 1

    async def test_falls_back_when_llm_output_fails_validation(self):
        agent_findings = [AgentFindings(agent="consistency", findings=[_finding("c1", "Intro")])]
        # Missing required fields on the finding -- can't be repaired, must fall back.
        client = _mock_client_returning_tool_use({"findings": [{"id": "c1"}]})

        result = await llm_merge_and_report(client, agent_findings)

        assert result["dashboard"]["total_findings"] == 1

    async def test_recovers_stringified_findings_field_via_repair(self):
        agent_findings = [AgentFindings(agent="consistency", findings=[_finding("c1", "Intro", "critical")])]
        client = _mock_client_returning_tool_use({
            "findings": '[{"id": "c1", "location": {"page": null, "section": "Intro"}, "severity": "critical", "category": "c", "description": "d", "evidence": "e", "proposed_fix": "f"}]'
        })

        result = await llm_merge_and_report(client, agent_findings)

        assert result["dashboard"]["total_findings"] == 1

    async def test_llm_merge_catches_a_duplicate_the_deterministic_merge_structurally_cannot(self):
        # Mirrors a real finding from a live Bedrock run (audit_sample.docx) where
        # three agents independently flagged the same underlying issue -- Finding
        # 3's vagueness -- under three different section labels and completely
        # different wording. Documents WHY this module exists: dedupe()'s
        # exact-location gate can never even compare these, no matter how similar
        # the wording is, since they don't share a location.
        agent_findings = [
            AgentFindings(agent="structure", findings=[
                Finding(
                    id="s1", location=Location(page=None, section="Findings"), severity="warning",
                    category="underdeveloped_section",
                    description="Finding 3 does not state the control tested, the result, or the evidence basis.",
                    evidence="Finding 3: there were some issues with how invoices get processed.",
                    proposed_fix="Expand Finding 3 to identify the specific control tested.",
                ),
            ]),
            AgentFindings(agent="language_tone", findings=[
                Finding(
                    id="lt1", location=Location(page=None, section="Recommendations"), severity="critical",
                    category="Vague recommendation",
                    description="The recommendation for Finding 3 gives no specific action, owner, or timeline.",
                    evidence="For Finding 3, the process should be improved.",
                    proposed_fix="The AP Manager should implement a mandatory three-way match control.",
                ),
            ]),
            AgentFindings(agent="consistency", findings=[
                Finding(
                    id="c1", location=Location(page=None, section="Findings vs. Recommendations"), severity="critical",
                    category="Unsupported/Contradictory Recommendation Mapping",
                    description="Finding 3 is vague with no evidence basis, and its recommendation is equally non-specific.",
                    evidence="Finding 3: there were some issues... | Recommendation 3: the process should be improved.",
                    proposed_fix="Revise Finding 3 to state the specific control tested.",
                ),
            ]),
        ]

        # Deterministic merge: all three sit at different locations, so dedupe()'s
        # exact-location gate never compares them -- they survive untouched.
        deterministic_result = merge_and_report(agent_findings)
        assert deterministic_result["dashboard"]["total_findings"] == 3

        # LLM merge: recognizes the semantic overlap across section labels and
        # collapses all three into one synthesized finding.
        client = _mock_client_returning_tool_use({
            "findings": [
                {
                    "id": "c1",
                    "location": {"page": None, "section": "Findings vs. Recommendations"},
                    "severity": "critical",
                    "category": "Vague/unsupported finding and recommendation",
                    "description": "Finding 3 is vague, lacks a specific control and evidence basis, "
                                    "and its recommendation is equally non-actionable.",
                    "evidence": "Finding 3: there were some issues... | Recommendation 3: the process should be improved.",
                    "proposed_fix": "Revise Finding 3 to state the specific control tested, the result, "
                                    "and the evidence basis, with a specific corrective action.",
                    "merged_from": ["s1", "lt1"],
                },
            ]
        })

        llm_result = await llm_merge_and_report(client, agent_findings)

        assert llm_result["dashboard"]["total_findings"] == 1
        merged_finding = llm_result["detailed_report"]["sections"]["Findings vs. Recommendations"][0]
        assert set(merged_finding["merged_from"]) == {"s1", "lt1"}
        assert merged_finding["id"] == "c1"

    async def test_falls_back_when_llm_invents_a_finding_id_not_in_original_input(self):
        agent_findings = [AgentFindings(agent="consistency", findings=[_finding("c1", "Intro", "critical")])]
        client = _mock_client_returning_tool_use({
            "findings": [
                {"id": "invented-id", "location": {"page": None, "section": "Intro"}, "severity": "critical",
                 "category": "c", "description": "d", "evidence": "e", "proposed_fix": "f"},
            ]
        })

        result = await llm_merge_and_report(client, agent_findings)

        # Falls back to the deterministic merge -- the invented id is never trusted.
        findings_by_id = {
            f["id"]: f for findings in result["detailed_report"]["sections"].values() for f in findings
        }
        assert result["dashboard"]["total_findings"] == 1
        assert "c1" in findings_by_id
        assert "invented-id" not in findings_by_id

    async def test_location_drift_on_a_kept_finding_warns_but_does_not_fall_back(self, caplog):
        # Documents the choice made in llm_merge_and_report(): unlike an invented
        # id, a drifted location on an otherwise-untouched finding is logged, not
        # treated as disqualifying.
        agent_findings = [AgentFindings(agent="consistency", findings=[_finding("c1", "Intro", "critical")])]
        client = _mock_client_returning_tool_use({
            "findings": [
                {"id": "c1", "location": {"page": None, "section": "Somewhere Else"}, "severity": "critical",
                 "category": "c", "description": "d", "evidence": "e", "proposed_fix": "f"},
            ]
        })

        caplog.set_level("WARNING")
        result = await llm_merge_and_report(client, agent_findings)

        # Not a fallback -- the deterministic path would have kept the original
        # "Intro" location, so seeing "Somewhere Else" proves the LLM's output won.
        assert result["dashboard"]["total_findings"] == 1
        assert "Somewhere Else" in result["detailed_report"]["sections"]
        assert any("changed the location" in record.message for record in caplog.records)

    async def test_one_malformed_finding_among_many_good_ones_discards_the_entire_merge(self):
        # Fail-strict by design (see llm_merge_and_report's inline comment): pins
        # down that a single malformed finding invalidates the whole batch, even
        # though the other finding in this response was perfectly valid -- so a
        # future change to add partial recovery is a deliberate decision, not an
        # accidental regression.
        agent_findings = [
            AgentFindings(agent="consistency", findings=[
                _finding("c1", "Intro", "critical"),
                _finding("c2", "Risks", "warning"),
            ]),
        ]
        client = _mock_client_returning_tool_use({
            "findings": [
                {"id": "c1", "location": {"page": None, "section": "Intro"}, "severity": "critical",
                 "category": "c", "description": "d", "evidence": "e", "proposed_fix": "f"},
                {"id": "c2"},  # missing required fields -- malformed
            ]
        })

        result = await llm_merge_and_report(client, agent_findings)

        # Falls back to the deterministic merge of BOTH original findings -- not a
        # partial merge that kept just the one valid LLM-returned finding (c1).
        assert result["dashboard"]["total_findings"] == 2
