from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.schema import run_agent, transcribe_page_image, transcribe_page_images


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


def _text_block(text):
    block = MagicMock()
    block.type = "text"
    block.text = text
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


class TestTranscribePageImage:
    async def test_returns_concatenated_text_from_response(self):
        client = _mock_client_returning([_text_block("Page one "), _text_block("content.")], stop_reason="end_turn")

        result = await transcribe_page_image(client, "base64data")

        assert result == "Page one content."

    async def test_sends_correct_image_block_and_model(self):
        client = _mock_client_returning([_text_block("text")], stop_reason="end_turn")

        await transcribe_page_image(client, "base64data", media_type="image/jpeg")

        call_kwargs = client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "global.anthropic.claude-sonnet-5"
        content = call_kwargs["messages"][0]["content"]
        image_block = next(b for b in content if b["type"] == "image")
        assert image_block["source"] == {"type": "base64", "media_type": "image/jpeg", "data": "base64data"}
        assert any(b["type"] == "text" for b in content)

    async def test_returns_empty_string_when_response_has_no_text(self):
        client = _mock_client_returning([], stop_reason="end_turn")

        result = await transcribe_page_image(client, "base64data")

        assert result == ""


class TestTranscribePageImages:
    async def test_sends_one_image_block_per_image_plus_one_text_block(self):
        client = _mock_client_returning([_text_block("text")], stop_reason="end_turn")

        await transcribe_page_images(client, [("img1", "image/png"), ("img2", "image/jpeg")])

        content = client.messages.create.call_args.kwargs["messages"][0]["content"]
        image_blocks = [b for b in content if b["type"] == "image"]
        assert [b["source"]["data"] for b in image_blocks] == ["img1", "img2"]
        assert [b["source"]["media_type"] for b in image_blocks] == ["image/png", "image/jpeg"]
        assert content[-1]["type"] == "text"

    async def test_returns_concatenated_text(self):
        client = _mock_client_returning([_text_block("a"), _text_block("b")], stop_reason="end_turn")

        result = await transcribe_page_images(client, [("img1", "image/png")])

        assert result == "ab"
