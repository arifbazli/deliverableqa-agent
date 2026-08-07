import asyncio
import json
import tempfile
import uuid
from pathlib import Path
from typing import Literal

import anthropic
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auth import get_expected_token
from run_qa import ENGAGEMENT_TYPES, REPO_ROOT, run

ALLOWED_SUFFIXES = {".docx", ".pptx", ".pdf"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# In-memory job store: fine for a single local process with no restart-durability
# requirement. Jobs are never pruned within a process lifetime — acceptable for a
# local single-user tool that gets restarted between sessions, not for a
# long-running multi-day server process.
JOBS: dict[str, "Job"] = {}

app = FastAPI(title="DeliverableQA")
security = HTTPBearer(auto_error=False)


class Job(BaseModel):
    status: Literal["processing", "done", "error"] = "processing"
    result: dict | None = None
    error: str | None = None


async def require_auth(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> None:
    expected = get_expected_token()
    if expected is None:
        return  # auth disabled — DELIVERABLEQA_TOKEN not set
    if credentials is None or credentials.credentials != expected:
        raise HTTPException(401, "Missing or invalid bearer token")


def _api_error_message(e: Exception) -> tuple[int, str]:
    if isinstance(e, anthropic.APIStatusError):
        return 502, f"Claude API error ({e.status_code}): {e.message}"
    if isinstance(e, anthropic.APIConnectionError):
        return 502, "Could not reach Claude on Bedrock — check AWS credentials and network."
    if isinstance(e, ValueError):
        return 400, str(e)
    return 500, f"Unexpected error: {e}"


async def _process_job(job_id: str, tmp_path: Path, engagement_type: str, previous_report: dict | None, use_llm_merge: bool) -> None:
    try:
        result = await run(tmp_path, engagement_type, REPO_ROOT / "output", previous_report, use_llm_merge)
        JOBS[job_id] = Job(status="done", result=result)
    except Exception as e:
        _, message = _api_error_message(e)
        JOBS[job_id] = Job(status="error", error=message)
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/api/analyze", dependencies=[Depends(require_auth)])
async def analyze(
    file: UploadFile = File(...),
    engagement_type: str = Form(...),
    previous_findings: UploadFile | None = File(None),
    llm_merge: bool = Form(False),
):
    if engagement_type not in ENGAGEMENT_TYPES:
        raise HTTPException(400, f"engagement_type must be one of {sorted(ENGAGEMENT_TYPES)}")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"Unsupported file type {suffix or '(none)'} — expected .docx, .pptx, or .pdf")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")

    previous_report = None
    if previous_findings is not None:
        try:
            previous_report = json.loads(await previous_findings.read())
        except json.JSONDecodeError:
            raise HTTPException(400, "previous_findings is not valid JSON")

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    job_id = str(uuid.uuid4())
    JOBS[job_id] = Job(status="processing")
    asyncio.create_task(_process_job(job_id, tmp_path, engagement_type, previous_report, llm_merge))

    return {"job_id": job_id, "status": "processing"}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
async def get_job(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job id")
    return job


app.mount("/", StaticFiles(directory=REPO_ROOT / "dashboard", html=True), name="dashboard")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
