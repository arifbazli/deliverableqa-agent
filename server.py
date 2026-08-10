import tempfile
from pathlib import Path

import anthropic
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from run_qa import ENGAGEMENT_TYPES, REPO_ROOT, run

FINDINGS_PATH = REPO_ROOT / "output" / "findings.json"
ALLOWED_SUFFIXES = {".docx", ".pptx", ".pdf"}

app = FastAPI(title="DeliverableQA")


@app.get("/api/findings")
async def get_findings():
    if not FINDINGS_PATH.exists():
        raise HTTPException(404, "No findings yet — run run_qa.py against a deliverable first.")
    return Response(FINDINGS_PATH.read_text(encoding="utf-8"), media_type="application/json")


@app.delete("/api/findings")
async def clear_findings():
    FINDINGS_PATH.unlink(missing_ok=True)
    return {"status": "ok"}


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...), engagement_type: str = Form(...)):
    if engagement_type not in ENGAGEMENT_TYPES:
        raise HTTPException(400, f"engagement_type must be one of {sorted(ENGAGEMENT_TYPES)}")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"Unsupported file type {suffix or '(none)'} — expected .docx, .pptx, or .pdf")

    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        # One request, one synchronous run -- no job queue. The client just
        # waits for the response; real Bedrock calls take a minute or two.
        await run(tmp_path, engagement_type, REPO_ROOT / "output", document_name=file.filename)
        return {"status": "ok"}
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"Claude API error ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        raise HTTPException(502, "Could not reach Claude on Bedrock — check AWS credentials and network.")
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        tmp_path.unlink(missing_ok=True)


app.mount("/", StaticFiles(directory=REPO_ROOT / "dashboard", html=True), name="dashboard")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
