from unittest.mock import AsyncMock, MagicMock

from agents.schema import AgentFindings, Finding, Location
from orchestrator.llm_merge import llm_merge_and_report


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
