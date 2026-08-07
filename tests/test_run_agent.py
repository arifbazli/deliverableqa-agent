from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.schema import run_agent


def _mock_client_returning(content_blocks, stop_reason="tool_use"):
    mock_response = MagicMock()
    mock_response.content = content_blocks
    mock_response.stop_reason = stop_reason
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    return mock_client


def _tool_use_block(input_dict):
    block = MagicMock()
    block.type = "tool_use"
    block.input = input_dict
    return block


class TestRunAgent:
    async def test_returns_parsed_findings_on_well_formed_response(self):
        client = _mock_client_returning([_tool_use_block({"agent": "consistency", "findings": []})])

        result = await run_agent(client, "consistency", "system prompt", "document context")

        assert result.agent == "consistency"
        assert result.findings == []

    async def test_recovers_from_stringified_findings_field(self):
        client = _mock_client_returning([
            _tool_use_block({"agent": "structure", "findings": "[]"})
        ])

        result = await run_agent(client, "structure", "system prompt", "document context")

        assert result.agent == "structure"

    async def test_calls_with_forced_tool_choice_and_correct_model(self):
        client = _mock_client_returning([_tool_use_block({"agent": "consistency", "findings": []})])

        await run_agent(client, "consistency", "the system prompt", "the document context")

        call_kwargs = client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "global.anthropic.claude-sonnet-5"
        assert call_kwargs["tool_choice"] == {"type": "tool", "name": "report_findings"}
        assert call_kwargs["system"] == "the system prompt"
        assert call_kwargs["messages"] == [{"role": "user", "content": "the document context"}]

    async def test_raises_runtime_error_when_no_tool_use_block(self):
        text_block = MagicMock()
        text_block.type = "text"
        client = _mock_client_returning([text_block], stop_reason="end_turn")

        with pytest.raises(RuntimeError, match="no tool_use block"):
            await run_agent(client, "consistency", "system prompt", "document context")
