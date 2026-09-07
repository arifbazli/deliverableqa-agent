from unittest.mock import AsyncMock

from agents.schema import AgentFindings, Finding, Location
from orchestrator import dispatch


def _finding(id, section):
    return Finding(
        id=id, location=Location(page=None, section=section), severity="warning",
        category="c", description="d", evidence="e", proposed_fix="f",
    )


class TestRunAgents:
    async def test_all_agents_succeed(self, monkeypatch):
        for name, module in dispatch.AGENT_MODULES.items():
            monkeypatch.setattr(
                module, "check",
                AsyncMock(return_value=AgentFindings(agent=name, findings=[_finding(f"{name}-1", "Intro")])),
            )

        agent_findings, agent_errors = await dispatch.run_agents(AsyncMock(), "document context")

        assert {af.agent for af in agent_findings} == set(dispatch.AGENT_MODULES.keys())
        assert all(len(af.findings) == 1 for af in agent_findings)
        assert agent_errors == {}

    async def test_one_agent_failing_does_not_discard_the_others(self, monkeypatch):
        # Regression test: previously, one agent's check() raising propagated out of
        # run_agents() entirely, discarding the other 3 agents' successful (and
        # expensive) results along with it.
        monkeypatch.setattr(
            dispatch.consistency, "check",
            AsyncMock(side_effect=RuntimeError("simulated Bedrock failure")),
        )
        for name in ("brand_format", "language_tone", "structure"):
            module = dispatch.AGENT_MODULES[name]
            monkeypatch.setattr(
                module, "check",
                AsyncMock(return_value=AgentFindings(agent=name, findings=[_finding(f"{name}-1", "Intro")])),
            )

        agent_findings, agent_errors = await dispatch.run_agents(AsyncMock(), "document context")

        by_agent = {af.agent: af for af in agent_findings}
        assert set(by_agent) == {"consistency", "brand_format", "language_tone", "structure"}
        assert by_agent["consistency"].findings == []
        assert len(by_agent["brand_format"].findings) == 1
        assert len(by_agent["language_tone"].findings) == 1
        assert len(by_agent["structure"].findings) == 1

    async def test_failure_is_reported_via_agent_errors_not_just_logged(self, monkeypatch, caplog):
        # A failure must be visible to the caller (agent_errors), not only logged --
        # otherwise a total failure (all 4 agents down) would be indistinguishable
        # from a genuinely clean report anywhere downstream.
        exc = RuntimeError("simulated Bedrock failure")
        monkeypatch.setattr(dispatch.consistency, "check", AsyncMock(side_effect=exc))
        for name in ("brand_format", "language_tone", "structure"):
            module = dispatch.AGENT_MODULES[name]
            monkeypatch.setattr(module, "check", AsyncMock(return_value=AgentFindings(agent=name, findings=[])))

        caplog.set_level("ERROR")
        agent_findings, agent_errors = await dispatch.run_agents(AsyncMock(), "document context")

        assert agent_errors == {"consistency": exc}
        assert any("consistency" in record.message and "failed" in record.message for record in caplog.records)

    async def test_all_agents_failing_reports_every_error(self, monkeypatch):
        errors = {name: RuntimeError(f"{name} simulated failure") for name in dispatch.AGENT_MODULES}
        for name, module in dispatch.AGENT_MODULES.items():
            monkeypatch.setattr(module, "check", AsyncMock(side_effect=errors[name]))

        agent_findings, agent_errors = await dispatch.run_agents(AsyncMock(), "document context")

        assert len(agent_errors) == len(agent_findings) == 4
        assert agent_errors == errors
        assert all(af.findings == [] for af in agent_findings)
