from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from report_ingestion.schemas import DoclingConversionResult

# Docling and PyMuPDF are imported lazily inside their respective functions so
# that test collection succeeds even when neither library is installed in the
# local environment (both are mocked in tests/test_pipeline.py).

_converter = None  # initialised on first call to convert_document()


def _get_converter():
    global _converter
    if _converter is None:
        from docling.document_converter import DocumentConverter as _DoclingConverter
        _converter = _DoclingConverter()
    return _converter


class DocumentConversionError(Exception):
    pass


def convert_document(file_path: str) -> DoclingConversionResult:
    """Primary document conversion via Docling.

    Args:
        file_path: Absolute path to the uploaded report (PDF/DOCX).

    Returns:
        DoclingConversionResult containing the narrative markdown, the raw
        DoclingDocument (passed through to Agent 1b unchanged), and the page count.

    Raises:
        DocumentConversionError: If Docling cannot process the file at all (corrupt
            file, unsupported format). Caller (pipeline.py) catches this and falls
            back to convert_document_fallback().
    """
    from docling.datamodel.base_models import ConversionStatus

    try:
        result = _get_converter().convert(source=file_path)
    except Exception as exc:
        raise DocumentConversionError(
            f"Docling failed to convert '{file_path}': {exc}"
        ) from exc

    if result.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
        raise DocumentConversionError(
            f"Docling conversion of '{file_path}' ended with status {result.status}"
        )

    doc = result.document
    narrative_markdown = doc.export_to_markdown()
    page_count = len(doc.pages)

    return DoclingConversionResult(
        narrative_markdown=narrative_markdown,
        docling_document=doc,
        page_count=page_count,
    )


def convert_document_fallback(file_path: str) -> str:
    """PyMuPDF raw-text fallback, used only when convert_document() raises.

    Args:
        file_path: Same input file passed to convert_document().

    Returns:
        Raw unstructured text with page breaks marked but no guaranteed
        heading/layout structure. Sufficient for the classifiers (text only),
        but NOT sufficient for Agent 2's section splitting — flag this report
        for a quality check if this path is taken, since downstream sections
        will be degraded.
    """
    import fitz

    try:
        pdf = fitz.open(file_path)
    except Exception as exc:
        raise DocumentConversionError(
            f"PyMuPDF failed to open '{file_path}': {exc}"
        ) from exc

    pages: list[str] = []
    for i, page in enumerate(pdf, start=1):
        pages.append(f"<!-- page {i} -->\n{page.get_text()}")
    pdf.close()

    return "\f".join(pages)


def get_classification_excerpt(narrative_markdown: str, max_chars: int = 6000) -> str:
    """Return a truncated excerpt of the narrative for use by all classifiers.

    Covers the cover page, title block, and typically the start of the business
    description / notes section for most report layouts. All four classifiers
    call this utility so every caller truncates identically.

    Args:
        narrative_markdown: Full converted narrative text from convert_document().
        max_chars: Maximum character length of the excerpt (default 6000, ~1500
            tokens). Sourced from config.settings.CLASSIFICATION_EXCERPT_MAX_CHARS
            — never hardcode the value here.

    Returns:
        The first ``max_chars`` characters of the narrative, truncated at a
        paragraph boundary where possible to avoid cutting mid-sentence.
    """
    if len(narrative_markdown) <= max_chars:
        return narrative_markdown

    truncated = narrative_markdown[:max_chars]
    last_para_break = truncated.rfind("\n\n")
    if last_para_break > 0:
        return truncated[:last_para_break]
    return truncated
