from fastapi.testclient import TestClient

import server


def _fake_docx_bytes() -> bytes:
    # Content doesn't matter -- run() is mocked in every test that reaches it,
    # so parse_document() never actually runs against this.
    return b"fake docx content"


class TestAnalyze:
    def test_valid_request_returns_200(self, monkeypatch):
        async def _fake_run(*a, **kw):
            return {"dashboard": {"total_findings": 0}, "detailed_report": {"sections": {}}}

        monkeypatch.setattr(server, "run", _fake_run)
        client = TestClient(server.app)

        resp = client.post(
            "/api/analyze",
            files={"file": ("doc.docx", _fake_docx_bytes())},
            data={"engagement_type": "advisory"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_rejects_unknown_engagement_type(self, monkeypatch):
        async def _should_not_be_called(*a, **kw):
            raise AssertionError("run() should not be called for invalid input")

        monkeypatch.setattr(server, "run", _should_not_be_called)
        client = TestClient(server.app)

        resp = client.post(
            "/api/analyze",
            files={"file": ("doc.docx", _fake_docx_bytes())},
            data={"engagement_type": "not-a-real-type"},
        )

        assert resp.status_code == 400
        assert "engagement_type" in resp.json()["detail"]

    def test_rejects_unsupported_file_extension(self, monkeypatch):
        async def _should_not_be_called(*a, **kw):
            raise AssertionError("run() should not be called for invalid input")

        monkeypatch.setattr(server, "run", _should_not_be_called)
        client = TestClient(server.app)

        resp = client.post(
            "/api/analyze",
            files={"file": ("doc.txt", _fake_docx_bytes())},
            data={"engagement_type": "advisory"},
        )

        assert resp.status_code == 400
        assert "Unsupported file type" in resp.json()["detail"]

    def test_credentials_runtime_error_returns_a_specific_message(self, monkeypatch):
        async def _fake_run(*a, **kw):
            raise RuntimeError("could not resolve credentials from session")

        monkeypatch.setattr(server, "run", _fake_run)
        client = TestClient(server.app)

        resp = client.post(
            "/api/analyze",
            files={"file": ("doc.docx", _fake_docx_bytes())},
            data={"engagement_type": "advisory"},
        )

        assert resp.status_code == 502
        assert "AWS credentials" in resp.json()["detail"]

    def test_unexpected_exception_returns_a_message_instead_of_a_bare_500(self, monkeypatch, caplog):
        async def _fake_run(*a, **kw):
            raise KeyError("some_unexpected_key")

        monkeypatch.setattr(server, "run", _fake_run)
        client = TestClient(server.app)

        caplog.set_level("ERROR")
        resp = client.post(
            "/api/analyze",
            files={"file": ("doc.docx", _fake_docx_bytes())},
            data={"engagement_type": "advisory"},
        )

        assert resp.status_code == 500
        assert "KeyError" in resp.json()["detail"]
        assert "server.log" in resp.json()["detail"]
        assert any("Unhandled error" in record.message for record in caplog.records)


class TestClearFindings:
    def test_deletes_existing_findings_file(self, monkeypatch, tmp_path):
        findings_path = tmp_path / "findings.json"
        findings_path.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(server, "FINDINGS_PATH", findings_path)
        client = TestClient(server.app)

        resp = client.delete("/api/findings")

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        assert not findings_path.exists()

    def test_is_a_no_op_when_no_findings_file_exists(self, monkeypatch, tmp_path):
        monkeypatch.setattr(server, "FINDINGS_PATH", tmp_path / "missing.json")
        client = TestClient(server.app)

        resp = client.delete("/api/findings")

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_get_findings_returns_404_after_clear(self, monkeypatch, tmp_path):
        findings_path = tmp_path / "findings.json"
        findings_path.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(server, "FINDINGS_PATH", findings_path)
        client = TestClient(server.app)

        client.delete("/api/findings")
        resp = client.get("/api/findings")

        assert resp.status_code == 404
