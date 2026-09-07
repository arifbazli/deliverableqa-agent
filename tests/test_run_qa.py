import sys
from pathlib import Path
from unittest.mock import AsyncMock

import anthropic
import httpx
import pytest

import run_qa
from agents.schema import AgentFindings, Finding, Location


def _finding(id, section):
    return Finding(
        id=id, location=Location(page=None, section=section), severity="warning",
        category="c", description="d", evidence="e", proposed_fix="f",
    )


@pytest.fixture(autouse=True)
def _stub_client_and_parse(monkeypatch):
    # Neither of these should make a real network/AWS call in a unit test -- the
    # client is a plain object at construction time, and parsing is exercised
    # separately in tests/test_parse.py.
    monkeypatch.setattr(run_qa, "AsyncAnthropicBedrock", lambda **kw: AsyncMock())
    monkeypatch.setattr(run_qa, "parse_document_with_ocr_fallback", AsyncMock(return_value=[]))


class TestRunAgentFailureVisibility:
    async def test_all_agents_failing_raises_the_first_real_exception(self, monkeypatch, tmp_path):
        # Regression test: this must raise (not return a misleadingly clean "pass, 0
        # findings" report) and must preserve the real exception type, since
        # server.py's dedicated except branches depend on it.
        exc = RuntimeError("could not resolve credentials from session")
        monkeypatch.setattr(run_qa, "run_agents", AsyncMock(return_value=(
            [AgentFindings(agent=name, findings=[]) for name in
             ("consistency", "brand_format", "language_tone", "structure")],
            {name: exc for name in ("consistency", "brand_format", "language_tone", "structure")},
        )))

        with pytest.raises(RuntimeError, match="could not resolve credentials"):
            await run_qa.run(Path("doc.docx"), "advisory", tmp_path)

    async def test_partial_failure_attaches_agent_errors_and_still_produces_a_report(self, monkeypatch, tmp_path):
        exc = RuntimeError("simulated failure")
        monkeypatch.setattr(run_qa, "run_agents", AsyncMock(return_value=(
            [
                AgentFindings(agent="consistency", findings=[]),
                AgentFindings(agent="brand_format", findings=[_finding("brand_format:1", "Intro")]),
                AgentFindings(agent="language_tone", findings=[]),
                AgentFindings(agent="structure", findings=[]),
            ],
            {"consistency": exc},
        )))

        result = await run_qa.run(Path("doc.docx"), "advisory", tmp_path)

        assert result["agent_errors"] == {"consistency": "RuntimeError: simulated failure"}
        assert result["dashboard"]["total_findings"] == 1

    async def test_no_failures_has_no_agent_errors_key(self, monkeypatch, tmp_path):
        monkeypatch.setattr(run_qa, "run_agents", AsyncMock(return_value=(
            [AgentFindings(agent=name, findings=[]) for name in
             ("consistency", "brand_format", "language_tone", "structure")],
            {},
        )))

        result = await run_qa.run(Path("doc.docx"), "advisory", tmp_path)

        assert "agent_errors" not in result


class TestMainCliErrorHandling:
    def _write_doc(self, tmp_path):
        doc = tmp_path / "doc.docx"
        doc.write_bytes(b"not a real docx, run() is mocked in these tests")
        return doc

    def test_bedrock_connection_error_becomes_a_clean_system_exit(self, monkeypatch, tmp_path):
        doc = self._write_doc(tmp_path)
        exc = anthropic.APIConnectionError(request=httpx.Request("POST", "https://example.com"))
        monkeypatch.setattr(run_qa, "run", AsyncMock(side_effect=exc))
        monkeypatch.setattr(sys, "argv", ["run_qa.py", str(doc), "--engagement-type", "advisory"])

        with pytest.raises(SystemExit, match="Could not reach Claude on Bedrock"):
            run_qa.main()

    def test_credentials_runtime_error_becomes_a_clean_system_exit(self, monkeypatch, tmp_path):
        doc = self._write_doc(tmp_path)
        monkeypatch.setattr(run_qa, "run", AsyncMock(side_effect=RuntimeError("could not resolve credentials from session")))
        monkeypatch.setattr(sys, "argv", ["run_qa.py", str(doc), "--engagement-type", "advisory"])

        with pytest.raises(SystemExit, match="AWS credentials could not be resolved"):
            run_qa.main()

    def test_other_runtime_errors_are_not_swallowed(self, monkeypatch, tmp_path):
        doc = self._write_doc(tmp_path)
        monkeypatch.setattr(run_qa, "run", AsyncMock(side_effect=RuntimeError("something unrelated")))
        monkeypatch.setattr(sys, "argv", ["run_qa.py", str(doc), "--engagement-type", "advisory"])

        with pytest.raises(RuntimeError, match="something unrelated"):
            run_qa.main()

    def test_malformed_previous_findings_json_becomes_a_clean_system_exit(self, monkeypatch, tmp_path):
        doc = self._write_doc(tmp_path)
        previous = tmp_path / "prev.json"
        previous.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr(
            sys, "argv",
            ["run_qa.py", str(doc), "--engagement-type", "advisory", "--previous-findings", str(previous)],
        )

        with pytest.raises(SystemExit, match="not valid JSON"):
            run_qa.main()

    def test_successful_run_prints_agent_errors_warning_when_present(self, monkeypatch, tmp_path, capsys):
        doc = self._write_doc(tmp_path)
        monkeypatch.setattr(run_qa, "run", AsyncMock(return_value={
            "dashboard": {"pass_fail": "pass", "total_findings": 0, "counts_by_severity": {}},
            "detailed_report": {"sections": {}},
            "agent_errors": {"consistency": "RuntimeError: simulated failure"},
        }))
        monkeypatch.setattr(sys, "argv", ["run_qa.py", str(doc), "--engagement-type", "advisory"])

        run_qa.main()

        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "consistency" in out
