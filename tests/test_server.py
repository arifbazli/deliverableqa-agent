import asyncio

import pytest
from fastapi.testclient import TestClient

import auth
import server


@pytest.fixture
def client():
    server.JOBS.clear()
    return TestClient(server.app)


def _fake_docx_bytes() -> bytes:
    # Content doesn't matter -- run() is mocked in every test that reaches it,
    # so parse_document() never actually runs against this.
    return b"fake docx content"


class TestAnalyzeValidation:
    """These reject before a job is ever created, so run() must never be mocked
    or called for them -- validated via monkeypatching run() to raise if invoked."""

    def test_rejects_unknown_engagement_type(self, client, monkeypatch):
        async def _should_not_be_called(*a, **kw):
            raise AssertionError("run() should not be called for invalid input")

        monkeypatch.setattr(server, "run", _should_not_be_called)

        resp = client.post(
            "/api/analyze",
            files={"file": ("doc.docx", _fake_docx_bytes())},
            data={"engagement_type": "not-a-real-type"},
        )

        assert resp.status_code == 400
        assert "engagement_type" in resp.json()["detail"]

    def test_rejects_unsupported_file_extension(self, client, monkeypatch):
        async def _should_not_be_called(*a, **kw):
            raise AssertionError("run() should not be called for invalid input")

        monkeypatch.setattr(server, "run", _should_not_be_called)

        resp = client.post(
            "/api/analyze",
            files={"file": ("doc.txt", _fake_docx_bytes())},
            data={"engagement_type": "advisory"},
        )

        assert resp.status_code == 400
        assert "Unsupported file type" in resp.json()["detail"]

    def test_rejects_file_over_size_limit(self, client, monkeypatch):
        async def _should_not_be_called(*a, **kw):
            raise AssertionError("run() should not be called for invalid input")

        monkeypatch.setattr(server, "run", _should_not_be_called)

        oversized = b"x" * (server.MAX_UPLOAD_BYTES + 1)
        resp = client.post(
            "/api/analyze",
            files={"file": ("doc.docx", oversized)},
            data={"engagement_type": "advisory"},
        )

        assert resp.status_code == 400
        assert "exceeds" in resp.json()["detail"]

    def test_rejects_invalid_previous_findings_json(self, client, monkeypatch):
        async def _should_not_be_called(*a, **kw):
            raise AssertionError("run() should not be called for invalid input")

        monkeypatch.setattr(server, "run", _should_not_be_called)

        resp = client.post(
            "/api/analyze",
            files={
                "file": ("doc.docx", _fake_docx_bytes()),
                "previous_findings": ("prev.json", b"not valid json"),
            },
            data={"engagement_type": "advisory"},
        )

        assert resp.status_code == 400
        assert "not valid JSON" in resp.json()["detail"]


class TestAnalyzeJobLifecycle:
    def test_returns_job_id_immediately_without_blocking(self, client, monkeypatch):
        never_resolves = asyncio.Event()

        async def _blocks_forever(*a, **kw):
            await never_resolves.wait()
            return {}

        monkeypatch.setattr(server, "run", _blocks_forever)

        resp = client.post(
            "/api/analyze",
            files={"file": ("doc.docx", _fake_docx_bytes())},
            data={"engagement_type": "advisory"},
        )

        # The request must return immediately with a job id, even though the
        # mocked run() never resolves -- this is the whole point of the queue.
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "processing"
        assert "job_id" in body

        never_resolves.set()  # let the background task finish so it doesn't leak

    def test_job_transitions_to_done_with_result(self, client, monkeypatch):
        expected_result = {"dashboard": {"total_findings": 3}, "detailed_report": {"sections": {}}}

        async def _fake_run(*a, **kw):
            return expected_result

        monkeypatch.setattr(server, "run", _fake_run)

        resp = client.post(
            "/api/analyze",
            files={"file": ("doc.docx", _fake_docx_bytes())},
            data={"engagement_type": "advisory"},
        )
        job_id = resp.json()["job_id"]

        # TestClient runs the app synchronously per-request but the background
        # task is scheduled via asyncio.create_task -- give the event loop a tick.
        import time
        for _ in range(20):
            status_resp = client.get(f"/api/jobs/{job_id}")
            if status_resp.json()["status"] != "processing":
                break
            time.sleep(0.05)

        body = status_resp.json()
        assert body["status"] == "done"
        assert body["result"] == expected_result

    def test_job_transitions_to_error_on_run_failure(self, client, monkeypatch):
        async def _fake_run(*a, **kw):
            raise ValueError("No readable text was found in this document.")

        monkeypatch.setattr(server, "run", _fake_run)

        resp = client.post(
            "/api/analyze",
            files={"file": ("doc.docx", _fake_docx_bytes())},
            data={"engagement_type": "advisory"},
        )
        job_id = resp.json()["job_id"]

        import time
        for _ in range(20):
            status_resp = client.get(f"/api/jobs/{job_id}")
            if status_resp.json()["status"] != "processing":
                break
            time.sleep(0.05)

        body = status_resp.json()
        assert body["status"] == "error"
        assert "No readable text" in body["error"]

    def test_unknown_job_id_returns_404(self, client):
        resp = client.get("/api/jobs/00000000-0000-0000-0000-000000000000")

        assert resp.status_code == 404

    def test_concurrent_jobs_get_distinct_ids_and_independent_results(self, client, monkeypatch):
        call_count = {"n": 0}

        async def _fake_run(document_path, engagement_type, output_dir, previous_report=None, use_llm_merge=False):
            call_count["n"] += 1
            return {"dashboard": {"total_findings": call_count["n"]}, "detailed_report": {"sections": {}}}

        monkeypatch.setattr(server, "run", _fake_run)

        resp1 = client.post("/api/analyze", files={"file": ("a.docx", _fake_docx_bytes())}, data={"engagement_type": "advisory"})
        resp2 = client.post("/api/analyze", files={"file": ("b.docx", _fake_docx_bytes())}, data={"engagement_type": "audit"})

        job1, job2 = resp1.json()["job_id"], resp2.json()["job_id"]
        assert job1 != job2

        import time
        for job_id in (job1, job2):
            for _ in range(20):
                status_resp = client.get(f"/api/jobs/{job_id}")
                if status_resp.json()["status"] != "processing":
                    break
                time.sleep(0.05)


class TestAuthEnforcement:
    def test_no_auth_required_when_token_unset(self, client, monkeypatch):
        monkeypatch.delenv(auth.TOKEN_ENV_VAR, raising=False)

        async def _fake_run(*a, **kw):
            return {"dashboard": {"total_findings": 0}, "detailed_report": {"sections": {}}}

        monkeypatch.setattr(server, "run", _fake_run)

        resp = client.post(
            "/api/analyze",
            files={"file": ("doc.docx", _fake_docx_bytes())},
            data={"engagement_type": "advisory"},
        )

        assert resp.status_code == 200

    def test_rejects_missing_token_when_configured(self, client, monkeypatch):
        monkeypatch.setenv(auth.TOKEN_ENV_VAR, "expected-token")

        resp = client.post(
            "/api/analyze",
            files={"file": ("doc.docx", _fake_docx_bytes())},
            data={"engagement_type": "advisory"},
        )

        assert resp.status_code == 401

    def test_rejects_wrong_token_when_configured(self, client, monkeypatch):
        monkeypatch.setenv(auth.TOKEN_ENV_VAR, "expected-token")

        resp = client.post(
            "/api/analyze",
            files={"file": ("doc.docx", _fake_docx_bytes())},
            data={"engagement_type": "advisory"},
            headers={"Authorization": "Bearer wrong-token"},
        )

        assert resp.status_code == 401

    def test_accepts_correct_token_when_configured(self, client, monkeypatch):
        monkeypatch.setenv(auth.TOKEN_ENV_VAR, "expected-token")

        async def _fake_run(*a, **kw):
            return {"dashboard": {"total_findings": 0}, "detailed_report": {"sections": {}}}

        monkeypatch.setattr(server, "run", _fake_run)

        resp = client.post(
            "/api/analyze",
            files={"file": ("doc.docx", _fake_docx_bytes())},
            data={"engagement_type": "advisory"},
            headers={"Authorization": "Bearer expected-token"},
        )

        assert resp.status_code == 200

    def test_job_status_endpoint_also_requires_auth(self, client, monkeypatch):
        monkeypatch.setenv(auth.TOKEN_ENV_VAR, "expected-token")

        resp = client.get("/api/jobs/some-id")

        assert resp.status_code == 401
