import json
import logging
import tempfile
from pathlib import Path

import anthropic
import pydantic
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from run_qa import ENGAGEMENT_TYPES, REPO_ROOT, run

logger = logging.getLogger(__name__)

FINDINGS_PATH = REPO_ROOT / "output" / "findings.json"
ALLOWED_SUFFIXES = {".docx", ".pptx", ".pdf"}
MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024  # matches the dashboard's own advisory cap
# multipart/form-data POST is a CORS-safelisted request -- the browser sends it
# cross-origin with no preflight, and a plain HTML <form> can trigger it with zero
# script access at all. Requiring this custom header forces a preflight for a
# fetch()-based attempt (which fails with no CORS-allow headers configured) and is
# never sendable by a native <form> submission in the first place, so it blocks both
# attack shapes without needing full auth for what's meant to be a single-user local tool.
DASHBOARD_CLIENT_HEADER = "X-DeliverableQA-Client"

app = FastAPI(title="DeliverableQA")


@app.get("/api/findings")
async def get_findings():
    if not FINDINGS_PATH.exists():
        raise HTTPException(404, "No findings yet — run run_qa.py against a deliverable first.")
    raw = FINDINGS_PATH.read_text(encoding="utf-8")
    try:
        json.loads(raw)
    except json.JSONDecodeError as e:
        # An interrupted write (e.g. a crash mid-write_text()) can leave findings.json
        # truncated. Without this check, the corrupted text is served as-is and the
        # dashboard's res.json() throws a raw SyntaxError that its error handling
        # doesn't recognize, instead of the app's normal clean error banner.
        logger.exception("findings.json is not valid JSON")
        raise HTTPException(500, f"Stored findings are corrupted and could not be read: {e}")
    return Response(raw, media_type="application/json")


@app.delete("/api/findings")
async def clear_findings():
    FINDINGS_PATH.unlink(missing_ok=True)
    return {"status": "ok"}


@app.post("/api/analyze")
async def analyze(request: Request, file: UploadFile = File(...), engagement_type: str = Form(...)):
    if request.headers.get(DASHBOARD_CLIENT_HEADER) != "dashboard":
        raise HTTPException(403, "This endpoint is only callable from the DeliverableQA dashboard.")

    if engagement_type not in ENGAGEMENT_TYPES:
        raise HTTPException(400, f"engagement_type must be one of {sorted(ENGAGEMENT_TYPES)}")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"Unsupported file type {suffix or '(none)'} — expected .docx, .pptx, or .pdf")

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            413,
            f"File is too large ({len(content) / 1024 / 1024:.1f}MB) — "
            f"the limit is {MAX_UPLOAD_SIZE_BYTES // 1024 // 1024}MB.",
        )
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        # One request, one synchronous run -- no job queue. The client just
        # waits for the response; real Bedrock calls take a minute or two.
        # use_llm_merge=True: the deterministic dedupe() only catches duplicates at
        # the exact same location with >60% text overlap -- the web dashboard is the
        # primary demo path, so it should get the same semantic-duplicate catching the
        # CLI's --llm-merge flag already offers, not leave visible near-duplicates in
        # a live run. Falls back to the deterministic merge automatically on any
        # failure, so this never makes a request fail that would otherwise succeed.
        await run(tmp_path, engagement_type, REPO_ROOT / "output", document_name=file.filename, use_llm_merge=True)
        return {"status": "ok"}
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"Claude API error ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        raise HTTPException(502, "Could not reach Claude on Bedrock — check AWS credentials and network.")
    except pydantic.ValidationError as e:
        # pydantic.ValidationError subclasses ValueError, so without this branch ahead
        # of the one below, a genuine LLM-formatting fault (an agent's response that
        # survived _repair_tool_input but still fails schema validation) would get
        # reported to the uploader as their own bad request (400) instead of the
        # server/LLM fault it actually is.
        logger.exception("Agent response failed schema validation")
        raise HTTPException(502, f"Claude returned a response that didn't match the expected findings schema: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        # Anything not already handled above (e.g. AWS credentials expiring
        # mid-session) used to surface as a bare HTTP 500 with no detail --
        # the dashboard just showed "Server returned HTTP 500" and the only
        # way to find out why was to already know to check server.log.
        if isinstance(e, RuntimeError) and "credentials" in str(e).lower():
            raise HTTPException(
                502,
                "AWS credentials could not be resolved — check they're set and still valid "
                "(env vars, ~/.aws/credentials, or an active SSO session).",
            )
        logger.exception("Unhandled error during analyze()")
        raise HTTPException(500, f"Analysis failed ({type(e).__name__}): {e} — see server.log for the full traceback.")
    finally:
        tmp_path.unlink(missing_ok=True)


app.mount("/", StaticFiles(directory=REPO_ROOT / "dashboard", html=True), name="dashboard")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
