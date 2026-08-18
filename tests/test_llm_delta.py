from unittest.mock import AsyncMock, MagicMock

from agents.schema import AgentFindings, Finding, Location
from orchestrator.llm_delta import llm_compute_delta
from orchestrator.merge import compute_delta, merge_and_report


def _finding(id, section, severity="warning", description="d", evidence="e", proposed_fix="f"):
    return Finding(
        id=id, location=Location(page=None, section=section), severity=severity,
        category="c", description=description, evidence=evidence, proposed_fix=proposed_fix,
    )


def _report_from(*findings):
    agent_findings = [AgentFindings(agent="consistency", findings=list(findings))]
    return merge_and_report(agent_findings)


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


class TestLlmComputeDelta:
    async def test_short_circuits_when_nothing_unmatched(self):
        # Everything matches deterministically -- no resolved/new leftovers to refine.
        previous = _report_from(_finding("c1", "Intro"))
        current = _report_from(_finding("c2", "Intro"))
        client = AsyncMock()

        result = await llm_compute_delta(client, previous, current)

        assert result == compute_delta(previous, current)
        client.messages.create.assert_not_called()

    async def test_short_circuits_when_only_resolved_side_has_leftovers(self):
        previous = _report_from(_finding("c1", "Intro"), _finding("c2", "Body"))
        current = _report_from(_finding("c3", "Intro"))  # matches c1, nothing new
        client = AsyncMock()

        result = await llm_compute_delta(client, previous, current)

        assert result == compute_delta(previous, current)
        client.messages.create.assert_not_called()

    async def test_catches_a_finding_reworded_and_relabeled_between_runs(self):
        # The documented gap: compute_delta() matches by location + text overlap, so
        # changing BOTH between runs (reworded AND moved to a new section) means it
        # can never match -- it lands in resolved+new instead of still_open.
        previous = _report_from(_finding(
            "old1", "Findings", description="The claim about revenue growth is unsubstantiated.",
        ))
        current = _report_from(_finding(
            "new1", "Executive Summary", description="No citation supports the stated revenue growth figure.",
        ))

        deterministic = compute_delta(previous, current)
        assert [f["id"] for f in deterministic["resolved"]] == ["old1"]
        assert [f["id"] for f in deterministic["new"]] == ["new1"]
        assert deterministic["still_open"] == []

        client = _mock_client_returning_tool_use({
            "matches": [{"old_id": "old1", "new_id": "new1", "confidence": "high", "reasoning": "same claim"}],
            "resolved": [],
        })

        result = await llm_compute_delta(client, previous, current)

        assert [f["id"] for f in result["still_open"]] == ["new1"]
        assert result["resolved"] == []
        assert result["new"] == []
        assert result["counts"] == {"resolved": 0, "still_open": 1, "new": 0}

    async def test_resolved_matches_pass_through_untouched(self):
        previous = _report_from(_finding("old1", "Intro"), _finding("old2", "Body"))
        current = _report_from(_finding("new1", "Summary"))

        client = _mock_client_returning_tool_use({
            "matches": [{"old_id": "old1", "new_id": "new1", "confidence": "medium", "reasoning": "match"}],
            "resolved": [{"old_id": "old2", "reasoning": "no longer present"}],
        })

        result = await llm_compute_delta(client, previous, current)

        assert [f["id"] for f in result["resolved"]] == ["old2"]
        assert [f["id"] for f in result["still_open"]] == ["new1"]
        assert result["new"] == []

    async def test_calls_with_correct_model_and_forced_tool_choice(self):
        previous = _report_from(_finding("old1", "Intro"))
        current = _report_from(_finding("new1", "Summary"))
        client = _mock_client_returning_tool_use({
            "matches": [],
            "resolved": [{"old_id": "old1", "reasoning": "gone"}],
        })

        await llm_compute_delta(client, previous, current)

        call_kwargs = client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "global.anthropic.claude-sonnet-5"
        assert call_kwargs["tool_choice"] == {"type": "tool", "name": "report_delta_matches"}

    async def test_only_sends_deterministic_leftovers_not_already_matched_findings(self):
        previous = _report_from(_finding("c1", "Intro"), _finding("old1", "Body"))
        current = _report_from(_finding("c2", "Intro"), _finding("new1", "Summary"))
        # c1/c2 match deterministically (same location, identical default text) --
        # only old1/new1 are genuine leftovers for the LLM to see.
        client = _mock_client_returning_tool_use({
            "matches": [],
            "resolved": [{"old_id": "old1", "reasoning": "gone"}],
        })

        await llm_compute_delta(client, previous, current)

        sent_content = client.messages.create.call_args.kwargs["messages"][0]["content"]
        assert "old1" in sent_content
        assert "new1" in sent_content
        assert "c1" not in sent_content
        assert "c2" not in sent_content

    async def test_falls_back_to_deterministic_delta_on_api_error(self):
        previous = _report_from(_finding("old1", "Intro"))
        current = _report_from(_finding("new1", "Summary"))
        client = _mock_client_raising(RuntimeError("simulated failure"))

        result = await llm_compute_delta(client, previous, current)

        assert result == compute_delta(previous, current)

    async def test_falls_back_when_no_tool_use_block_in_response(self):
        text_block = MagicMock()
        text_block.type = "text"
        response = MagicMock()
        response.content = [text_block]
        response.stop_reason = "end_turn"
        client = AsyncMock()
        client.messages.create = AsyncMock(return_value=response)

        previous = _report_from(_finding("old1", "Intro"))
        current = _report_from(_finding("new1", "Summary"))

        result = await llm_compute_delta(client, previous, current)

        assert result == compute_delta(previous, current)

    async def test_falls_back_when_response_fails_validation(self):
        previous = _report_from(_finding("old1", "Intro"))
        current = _report_from(_finding("new1", "Summary"))
        client = _mock_client_returning_tool_use({"matches": [{"old_id": "old1"}], "resolved": []})  # missing fields

        result = await llm_compute_delta(client, previous, current)

        assert result == compute_delta(previous, current)

    async def test_falls_back_when_llm_invents_an_old_id(self):
        previous = _report_from(_finding("old1", "Intro"))
        current = _report_from(_finding("new1", "Summary"))
        client = _mock_client_returning_tool_use({
            "matches": [],
            "resolved": [{"old_id": "invented-id", "reasoning": "gone"}],
        })

        result = await llm_compute_delta(client, previous, current)

        assert result == compute_delta(previous, current)

    async def test_falls_back_when_llm_invents_a_new_id(self):
        previous = _report_from(_finding("old1", "Intro"))
        current = _report_from(_finding("new1", "Summary"))
        client = _mock_client_returning_tool_use({
            "matches": [{"old_id": "old1", "new_id": "invented-id", "confidence": "high", "reasoning": "x"}],
            "resolved": [],
        })

        result = await llm_compute_delta(client, previous, current)

        assert result == compute_delta(previous, current)

    async def test_falls_back_when_a_new_id_is_double_claimed(self):
        previous = _report_from(_finding("old1", "Intro"), _finding("old2", "Body"))
        current = _report_from(_finding("new1", "Summary"))
        client = _mock_client_returning_tool_use({
            "matches": [
                {"old_id": "old1", "new_id": "new1", "confidence": "high", "reasoning": "x"},
                {"old_id": "old2", "new_id": "new1", "confidence": "low", "reasoning": "y"},
            ],
            "resolved": [],
        })

        result = await llm_compute_delta(client, previous, current)

        assert result == compute_delta(previous, current)

    async def test_falls_back_when_an_old_id_is_missing_from_both_lists(self):
        previous = _report_from(_finding("old1", "Intro"), _finding("old2", "Body"))
        current = _report_from(_finding("new1", "Summary"))
        client = _mock_client_returning_tool_use({
            "matches": [{"old_id": "old1", "new_id": "new1", "confidence": "high", "reasoning": "x"}],
            "resolved": [],  # old2 accounted for nowhere
        })

        result = await llm_compute_delta(client, previous, current)

        assert result == compute_delta(previous, current)

    async def test_recovers_stringified_matches_field_via_repair(self):
        previous = _report_from(_finding("old1", "Intro"))
        current = _report_from(_finding("new1", "Summary"))
        client = _mock_client_returning_tool_use({
            "matches": '[{"old_id": "old1", "new_id": "new1", "confidence": "high", "reasoning": "x"}]',
            "resolved": [],
        })

        result = await llm_compute_delta(client, previous, current)

        assert [f["id"] for f in result["still_open"]] == ["new1"]
