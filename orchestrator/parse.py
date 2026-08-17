import asyncio
import base64
from dataclasses import dataclass
from pathlib import Path

import docx
import pptx
import pymupdf
from anthropic import AsyncAnthropicBedrock

from agents.schema import transcribe_page_image

# A US Letter page at this DPI renders to ~1275x1650px, already close to
# Anthropic's own recommended long-side (~1568px) for vision cost/quality
# balance -- no extra resize step needed.
OCR_RENDER_DPI = 150
# Caps simultaneous Bedrock requests when OCR-ing a fully-scanned deliverable
# -- an 80-page document would otherwise fire 80 requests at once.
OCR_CONCURRENCY_LIMIT = 5


class DocumentParseError(ValueError):
    """Raised when a document can't be parsed or has no extractable text.

    Always safe to show the message directly to an end user (e.g. in the
    web UI's error banner) — it never leaks a library traceback.
    """


@dataclass
class Section:
    section: str
    page: int | None
    text: str


def parse_docx(path: Path) -> list[Section]:
    try:
        document = docx.Document(str(path))
    except Exception as e:
        raise DocumentParseError(f"Could not open this file as a Word document: {e}") from e

    sections: list[Section] = []
    current_heading = "Document"
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            sections.append(Section(section=current_heading, page=None, text="\n".join(buffer)))
            buffer.clear()

    for para in document.paragraphs:
        if not para.text.strip():
            continue
        if para.style.name.startswith("Heading"):
            flush()
            current_heading = para.text.strip()
        else:
            buffer.append(para.text)
    flush()
    return sections


def parse_pptx(path: Path) -> list[Section]:
    try:
        presentation = pptx.Presentation(str(path))
    except Exception as e:
        raise DocumentParseError(f"Could not open this file as a PowerPoint deck: {e}") from e

    sections: list[Section] = []
    for index, slide in enumerate(presentation.slides, start=1):
        texts = [
            shape.text_frame.text
            for shape in slide.shapes
            if shape.has_text_frame and shape.text_frame.text.strip()
        ]
        if texts:
            title = texts[0]
            body = "\n".join(texts)
            sections.append(Section(section=f"Slide {index}: {title}", page=index, text=body))
        else:
            sections.append(Section(section=f"Slide {index}", page=index, text="(no text content on this slide)"))
    return sections


def _open_pdf(path: Path) -> pymupdf.Document:
    try:
        return pymupdf.open(str(path))
    except Exception as e:
        raise DocumentParseError(f"Could not open this file as a PDF: {e}") from e


def parse_pdf(path: Path) -> list[Section]:
    document = _open_pdf(path)

    sections: list[Section] = []
    with document:
        for page_number, page in enumerate(document, start=1):
            text = page.get_text().strip()
            if text:
                sections.append(Section(section=f"Page {page_number}", page=page_number, text=text))
    return sections


async def ocr_scanned_pdf(path: Path, client: AsyncAnthropicBedrock) -> list[Section]:
    document = _open_pdf(path)
    semaphore = asyncio.Semaphore(OCR_CONCURRENCY_LIMIT)

    async def transcribe(page_number: int, page: pymupdf.Page) -> Section | None:
        image_b64 = base64.b64encode(page.get_pixmap(dpi=OCR_RENDER_DPI).tobytes("png")).decode()
        async with semaphore:
            text = await transcribe_page_image(client, image_b64)
        return Section(section=f"Page {page_number}", page=page_number, text=text) if text else None

    with document:
        results = await asyncio.gather(*(transcribe(n, page) for n, page in enumerate(document, start=1)))

    sections = [s for s in results if s is not None]
    if not sections:
        raise DocumentParseError(
            "No readable text was found in this document, even after attempting OCR via "
            "Claude vision. It may be blank, corrupted, or too low-resolution to read."
        )
    return sections


def parse_document(path: Path) -> list[Section]:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        sections = parse_docx(path)
    elif suffix == ".pptx":
        sections = parse_pptx(path)
    elif suffix == ".pdf":
        sections = parse_pdf(path)
    else:
        raise DocumentParseError(f"Unsupported file type {suffix!r} — expected .docx, .pptx, or .pdf")

    if not sections:
        raise DocumentParseError(
            "No readable text was found in this document. If it's a scanned PDF or an "
            "image-based deck, this pipeline can't extract text from it (OCR isn't supported)."
        )
    return sections


async def parse_document_with_ocr_fallback(path: Path, client: AsyncAnthropicBedrock) -> list[Section]:
    """Like parse_document(), but falls back to Claude-vision OCR for a PDF with no
    extractable text instead of raising immediately -- see ocr_scanned_pdf()."""
    try:
        return parse_document(path)
    except DocumentParseError:
        if path.suffix.lower() != ".pdf":
            raise
        return await ocr_scanned_pdf(path, client)


def render_document_context(sections: list[Section], engagement_type: str, checklist_yaml: str, style_rules_yaml: str) -> str:
    section_blocks = "\n\n".join(f"=== {s.section} ===\n{s.text}" for s in sections)
    return (
        f"engagement_type: {engagement_type}\n\n"
        f"--- checklist config ---\n{checklist_yaml}\n\n"
        f"--- style rules config ---\n{style_rules_yaml}\n\n"
        f"--- document sections ---\n{section_blocks}\n"
    )
