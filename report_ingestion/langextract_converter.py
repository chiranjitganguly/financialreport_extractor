"""
LangExtract-based document section extractor (Agent 1 backend).

Replaces the Docling primary path. PDF text is first extracted with PyMuPDF
(raw text only — no layout analysis), then langextract uses GPT-4o-mini to
identify and return the document's major sections as structured markdown.

This module is imported only when CONVERTER_BACKEND == "langextract".
To switch back to Docling, set CONVERTER_BACKEND=docling in the environment.
"""

from __future__ import annotations

import logging
import textwrap

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LangExtract prompt + few-shot examples
# ---------------------------------------------------------------------------

_SECTION_EXTRACTION_PROMPT = textwrap.dedent("""\
    You are processing a financial report (annual report, quarterly report, or
    regulatory filing). Extract all major sections from the document text.

    For each section, identify the heading exactly as it appears and the full body
    content beneath it. Preserve the original document order. Do not paraphrase or
    summarise — extract exact text. Each section must not overlap with another.

    Sections to look for include (but are not limited to):
    - Cover / Title Page
    - Directors' Report / Board's Report
    - Management Discussion and Analysis
    - Corporate Governance Report
    - Auditors' Report / Independent Auditor's Report
    - Financial Highlights / Five-Year Financial Summary
    - Balance Sheet / Statement of Financial Position
    - Statement of Profit and Loss / Income Statement
    - Statement of Cash Flows / Cash Flow Statement
    - Statement of Changes in Equity
    - Notes to Financial Statements / Notes to Accounts / Schedules
    - Segment Information
    - Related Party Disclosures
    - Risk Management Report

    Extract each section as a separate entity with class "section".
    The first line of the extraction text must be the section heading exactly as
    written in the document. Do not include table of contents entries as sections.
""")


def _build_examples():
    """Build few-shot examples for langextract (lazy import to keep module loadable)."""
    import langextract as lx

    return [
        lx.data.ExampleData(
            text=(
                "DIRECTORS' REPORT\n"
                "Dear Members,\n"
                "Your Directors are pleased to present the Annual Report for FY 2024.\n\n"
                "MANAGEMENT DISCUSSION AND ANALYSIS\n"
                "Industry Overview\n"
                "The automotive sector grew by 12% during the year under review."
            ),
            extractions=[
                lx.data.Extraction(
                    extraction_class="section",
                    extraction_text=(
                        "DIRECTORS' REPORT\n"
                        "Dear Members,\n"
                        "Your Directors are pleased to present the Annual Report for FY 2024."
                    ),
                    attributes={"heading": "DIRECTORS' REPORT"},
                ),
                lx.data.Extraction(
                    extraction_class="section",
                    extraction_text=(
                        "MANAGEMENT DISCUSSION AND ANALYSIS\n"
                        "Industry Overview\n"
                        "The automotive sector grew by 12% during the year under review."
                    ),
                    attributes={"heading": "MANAGEMENT DISCUSSION AND ANALYSIS"},
                ),
            ],
        )
    ]


# ---------------------------------------------------------------------------
# PDF text extraction (PyMuPDF — raw text layer only, no layout analysis)
# ---------------------------------------------------------------------------

def _extract_raw_text_from_pdf(file_path: str) -> tuple[str, int]:
    """Extract raw text from a PDF using PyMuPDF.

    Each page is prefixed with a <!-- page N --> marker so downstream section
    parsers can infer page provenance from the flat text.

    Args:
        file_path: Absolute path to the PDF.

    Returns:
        (text_with_page_markers, page_count)

    Raises:
        DocumentConversionError: if PyMuPDF cannot open the file.
    """
    # Imported here to keep the circular-import surface small (converter.py
    # defines DocumentConversionError, but langextract_converter.py is a peer).
    from report_ingestion.converter import DocumentConversionError
    import fitz  # PyMuPDF

    try:
        pdf = fitz.open(file_path)
    except Exception as exc:
        raise DocumentConversionError(
            f"PyMuPDF could not open '{file_path}': {exc}"
        ) from exc

    pages: list[str] = []
    for i, page in enumerate(pdf, start=1):
        # Strip null bytes — PostgreSQL JSONB rejects  and PyMuPDF
        # occasionally emits them for certain embedded font encodings.
        text = page.get_text().replace("\x00", "")
        pages.append(f"<!-- page {i} -->\n{text}")
    page_count = len(pdf)
    pdf.close()

    # Belt-and-suspenders: strip any remaining null bytes from the assembled
    # string. Python's compile() and PostgreSQL JSONB both reject \x00, and
    # langextract uses compile() internally when building its extraction schema.
    raw = "\f".join(pages).replace("\x00", "")
    return raw, page_count


# ---------------------------------------------------------------------------
# LangExtract result → structured markdown
# ---------------------------------------------------------------------------

def _extractions_to_markdown(extractions: list) -> str:
    """Convert langextract Extraction objects into structured markdown.

    Each section becomes an H2 heading (first line of extraction_text, or the
    "heading" attribute if populated) followed by the body content. The
    <!-- page N --> markers from the raw text are preserved inside the body so
    the downstream section_parser's markdown fallback can infer page numbers.

    Args:
        extractions: List of langextract Extraction objects.

    Returns:
        Markdown string with one H2 per section in document order.
    """
    lines: list[str] = []

    for ext in extractions:
        text: str = (getattr(ext, "extraction_text", "") or "").replace("\x00", "")
        if not text.strip():
            continue

        # Prefer the LLM-populated "heading" attribute; fall back to first line.
        attrs = getattr(ext, "attributes", {}) or {}
        heading = (attrs.get("heading") or "").replace("\x00", "").strip()

        raw_lines = text.split("\n")
        if not heading:
            heading = raw_lines[0].strip()
        body_lines = raw_lines[1:] if len(raw_lines) > 1 else []

        lines.append(f"## {heading}")
        lines.append("")
        if body_lines:
            lines.append("\n".join(body_lines))
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def convert_document_langextract(
    file_path: str,
    model_id: str = "gpt-4o-mini",
) -> tuple[str, int]:
    """Extract document sections via langextract + GPT-4o-mini.

    Workflow:
      1. Read PDF text with PyMuPDF (raw extraction only — no layout analysis).
      2. Pass text to langextract with the section-extraction prompt.
      3. Convert the resulting Extraction objects to H2-structured markdown.

    Internal fallback: if langextract fails (API error, empty result), returns
    the raw PyMuPDF text so the downstream section_parser's regex path can still
    attempt section detection. DocumentConversionError is only raised if PyMuPDF
    cannot read the file at all.

    Args:
        file_path: Absolute path to the uploaded PDF/DOCX report.
        model_id: LLM model to use (default "gpt-4o-mini").

    Returns:
        (narrative_markdown, page_count) where narrative_markdown is either
        H2-structured markdown (happy path) or flat page-marked text (fallback).
        page_count is derived from the PDF page count.

    Raises:
        DocumentConversionError: only if PyMuPDF cannot open the file.
    """
    import langextract as lx

    log.info("LangExtract: extracting raw text from '%s'.", file_path)
    raw_text, page_count = _extract_raw_text_from_pdf(file_path)

    if not raw_text.strip():
        log.warning(
            "LangExtract: no text could be extracted from '%s'; returning empty document.",
            file_path,
        )
        return "", page_count

    log.info(
        "LangExtract: sending %d chars to %s for section extraction.",
        len(raw_text),
        model_id,
    )

    try:
        result = lx.extract(
            text_or_documents=raw_text,
            prompt_description=_SECTION_EXTRACTION_PROMPT,
            examples=_build_examples(),
            model_id=model_id,
        )
    except Exception as exc:
        log.error(
            "LangExtract extraction failed for '%s': %s — falling back to raw PyMuPDF text.",
            file_path,
            exc,
        )
        return raw_text, page_count

    # result may be a single AnnotatedDocument or an iterable thereof;
    # normalise to a flat list of Extraction objects.
    extractions: list = []
    if hasattr(result, "extractions"):
        extractions = result.extractions or []
    elif hasattr(result, "__iter__"):
        for doc in result:
            if hasattr(doc, "extractions"):
                extractions.extend(doc.extractions or [])

    if not extractions:
        log.warning(
            "LangExtract returned no extractions for '%s' — falling back to raw PyMuPDF text.",
            file_path,
        )
        return raw_text, page_count

    log.info("LangExtract: extracted %d section(s) from '%s'.", len(extractions), file_path)
    narrative_markdown = _extractions_to_markdown(extractions)
    return narrative_markdown, page_count
