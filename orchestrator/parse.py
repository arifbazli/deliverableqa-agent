from dataclasses import dataclass
from pathlib import Path

import docx
import pptx
import pymupdf


@dataclass
class Section:
    section: str
    page: int | None
    text: str


def parse_docx(path: Path) -> list[Section]:
    document = docx.Document(str(path))
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
    presentation = pptx.Presentation(str(path))
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
    return sections


def parse_pdf(path: Path) -> list[Section]:
    sections: list[Section] = []
    with pymupdf.open(str(path)) as document:
        for page_number, page in enumerate(document, start=1):
            text = page.get_text().strip()
            if text:
                sections.append(Section(section=f"Page {page_number}", page=page_number, text=text))
    return sections


def parse_document(path: Path) -> list[Section]:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return parse_docx(path)
    if suffix == ".pptx":
        return parse_pptx(path)
    if suffix == ".pdf":
        return parse_pdf(path)
    raise ValueError(f"Unsupported file type: {suffix}")


def render_document_context(sections: list[Section], engagement_type: str, checklist_yaml: str, style_rules_yaml: str) -> str:
    section_blocks = "\n\n".join(f"=== {s.section} ===\n{s.text}" for s in sections)
    return (
        f"engagement_type: {engagement_type}\n\n"
        f"--- checklist config ---\n{checklist_yaml}\n\n"
        f"--- style rules config ---\n{style_rules_yaml}\n\n"
        f"--- document sections ---\n{section_blocks}\n"
    )
