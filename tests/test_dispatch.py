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

        result = await dispatch.run_agents(AsyncMock(), "document context")

        assert {af.agent for af in result} == set(dispatch.AGENT_MODULES.keys())
        assert all(len(af.findings) == 1 for af in result)

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

        result = await dispatch.run_agents(AsyncMock(), "document context")

        by_agent = {af.agent: af for af in result}
        assert set(by_agent) == {"consistency", "brand_format", "language_tone", "structure"}
        assert by_agent["consistency"].findings == []
        assert len(by_agent["brand_format"].findings) == 1
        assert len(by_agent["language_tone"].findings) == 1
        assert len(by_agent["structure"].findings) == 1

    async def test_failure_is_logged_instead_of_silently_swallowed(self, monkeypatch, caplog):
        monkeypatch.setattr(
            dispatch.consistency, "check",
            AsyncMock(side_effect=RuntimeError("simulated Bedrock failure")),
        )
        for name in ("brand_format", "language_tone", "structure"):
            module = dispatch.AGENT_MODULES[name]
            monkeypatch.setattr(module, "check", AsyncMock(return_value=AgentFindings(agent=name, findings=[])))

        caplog.set_level("ERROR")
        await dispatch.run_agents(AsyncMock(), "document context")

        assert any("consistency" in record.message and "failed" in record.message for record in caplog.records)
