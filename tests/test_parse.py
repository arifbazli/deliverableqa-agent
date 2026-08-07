import tempfile
from pathlib import Path

import docx
import pptx
import pymupdf
import pytest

from orchestrator.parse import DocumentParseError, parse_document, render_document_context


def _write_docx(path: Path, headings_and_body: list[tuple[str | None, str]]) -> None:
    document = docx.Document()
    for heading, body in headings_and_body:
        if heading is not None:
            document.add_heading(heading, level=1)
        if body:
            document.add_paragraph(body)
    document.save(str(path))


def _write_pptx(path: Path, slides: list[list[str]]) -> None:
    presentation = pptx.Presentation()
    for texts in slides:
        if texts:
            slide = presentation.slides.add_slide(presentation.slide_layouts[1])
            slide.shapes.title.text = texts[0]
            if len(texts) > 1 and len(slide.placeholders) > 1:
                slide.placeholders[1].text_frame.text = "\n".join(texts[1:])
        else:
            presentation.slides.add_slide(presentation.slide_layouts[6])  # blank layout
    presentation.save(str(path))


def _write_pdf(path: Path, pages: list[str]) -> None:
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    document.save(str(path))
    document.close()


class TestParseDocx:
    def test_headings_split_into_sections(self, tmp_path):
        path = tmp_path / "doc.docx"
        _write_docx(path, [("Executive Summary", "Summary text."), ("Findings", "Finding text.")])

        sections = parse_document(path)

        assert [s.section for s in sections] == ["Executive Summary", "Findings"]
        assert sections[0].text == "Summary text."
        assert sections[1].text == "Finding text."

    def test_no_headings_falls_back_to_single_document_section(self, tmp_path):
        path = tmp_path / "doc.docx"
        _write_docx(path, [(None, "Just a paragraph, no headings.")])

        sections = parse_document(path)

        assert len(sections) == 1
        assert sections[0].section == "Document"
        assert "Just a paragraph" in sections[0].text

    def test_empty_document_raises(self, tmp_path):
        path = tmp_path / "doc.docx"
        _write_docx(path, [])

        with pytest.raises(DocumentParseError, match="No readable text"):
            parse_document(path)

    def test_corrupted_file_raises_clean_error(self, tmp_path):
        path = tmp_path / "doc.docx"
        path.write_bytes(b"not a real docx file")

        with pytest.raises(DocumentParseError, match="Could not open this file as a Word document"):
            parse_document(path)

    def test_pages_are_none_for_docx(self, tmp_path):
        path = tmp_path / "doc.docx"
        _write_docx(path, [("H", "body")])

        sections = parse_document(path)

        assert sections[0].page is None


class TestParsePptx:
    def test_slides_become_sections_with_page_numbers(self, tmp_path):
        path = tmp_path / "deck.pptx"
        _write_pptx(path, [["Slide One", "body one"], ["Slide Two", "body two"]])

        sections = parse_document(path)

        assert len(sections) == 2
        assert sections[0].section == "Slide 1: Slide One"
        assert sections[0].page == 1
        assert sections[1].section == "Slide 2: Slide Two"
        assert sections[1].page == 2

    def test_slide_with_no_text_shapes_gets_placeholder_section(self, tmp_path):
        path = tmp_path / "deck.pptx"
        _write_pptx(path, [[], ["Slide Two", "body"]])

        sections = parse_document(path)

        assert len(sections) == 2
        assert sections[0].section == "Slide 1"
        assert "no text content" in sections[0].text
        assert sections[1].section == "Slide 2: Slide Two"

    def test_all_slides_blank_raises(self, tmp_path):
        path = tmp_path / "deck.pptx"
        _write_pptx(path, [[], []])

        # All-blank slides still produce placeholder sections (deck has structure,
        # even if no text) — this should NOT raise, unlike a truly empty docx.
        sections = parse_document(path)
        assert len(sections) == 2

    def test_corrupted_file_raises_clean_error(self, tmp_path):
        path = tmp_path / "deck.pptx"
        path.write_bytes(b"not a real pptx file")

        with pytest.raises(DocumentParseError, match="Could not open this file as a PowerPoint deck"):
            parse_document(path)


class TestParsePdf:
    def test_pages_become_sections(self, tmp_path):
        path = tmp_path / "doc.pdf"
        _write_pdf(path, ["Page one text", "Page two text"])

        sections = parse_document(path)

        assert len(sections) == 2
        assert sections[0].section == "Page 1"
        assert sections[0].page == 1
        assert "Page one text" in sections[0].text

    def test_blank_pages_are_skipped(self, tmp_path):
        path = tmp_path / "doc.pdf"
        _write_pdf(path, ["Real content here", ""])

        sections = parse_document(path)

        assert len(sections) == 1
        assert sections[0].section == "Page 1"

    def test_all_blank_pages_raises(self, tmp_path):
        path = tmp_path / "doc.pdf"
        _write_pdf(path, ["", ""])

        with pytest.raises(DocumentParseError, match="No readable text"):
            parse_document(path)

    def test_corrupted_file_raises_clean_error(self, tmp_path):
        path = tmp_path / "doc.pdf"
        path.write_bytes(b"not a real pdf file")

        with pytest.raises(DocumentParseError, match="Could not open this file as a PDF"):
            parse_document(path)


class TestParseDocument:
    def test_unsupported_extension_raises(self, tmp_path):
        path = tmp_path / "doc.txt"
        path.write_text("hello")

        with pytest.raises(DocumentParseError, match="Unsupported file type"):
            parse_document(path)


class TestRenderDocumentContext:
    def test_includes_all_inputs(self, tmp_path):
        path = tmp_path / "doc.docx"
        _write_docx(path, [("Heading", "Body text.")])
        sections = parse_document(path)

        context = render_document_context(sections, "advisory", "checklist: yaml", "style: yaml")

        assert "engagement_type: advisory" in context
        assert "checklist: yaml" in context
        assert "style: yaml" in context
        assert "=== Heading ===" in context
        assert "Body text." in context
