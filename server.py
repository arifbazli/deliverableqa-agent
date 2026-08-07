import tempfile
from pathlib import Path

import anthropic
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from run_qa import ENGAGEMENT_TYPES, REPO_ROOT, run

ALLOWED_SUFFIXES = {".docx", ".pptx", ".pdf"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

app = FastAPI(title="DeliverableQA")


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...), engagement_type: str = Form(...)):
    if engagement_type not in ENGAGEMENT_TYPES:
        raise HTTPException(400, f"engagement_type must be one of {sorted(ENGAGEMENT_TYPES)}")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"Unsupported file type {suffix or '(none)'} — expected .docx, .pptx, or .pdf")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        result = await run(tmp_path, engagement_type, REPO_ROOT / "output")
        return JSONResponse(result)
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"Claude API error ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        raise HTTPException(502, "Could not reach Claude on Bedrock — check AWS credentials and network.")
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


app.mount("/", StaticFiles(directory=REPO_ROOT / "dashboard", html=True), name="dashboard")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
