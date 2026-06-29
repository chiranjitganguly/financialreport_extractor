from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from report_ingestion.schemas import DoclingConversionResult

# ---------------------------------------------------------------------------
# Backend dispatcher
#
# Set CONVERTER_BACKEND in the environment (or .env) to choose:
#   CONVERTER_BACKEND=langextract  (default) — GPT-4o-mini via langextract
#   CONVERTER_BACKEND=docling      — Docling primary + PyMuPDF fallback
#
# The Docling and PyMuPDF paths are preserved below as commented code so they
# can be restored without rewriting from scratch.
# ---------------------------------------------------------------------------


class DocumentConversionError(Exception):
    pass


# ---------------------------------------------------------------------------
# Docling singleton — used only when CONVERTER_BACKEND=docling (commented out)
# ---------------------------------------------------------------------------

# Docling and PyMuPDF are imported lazily inside their respective functions so
# that test collection succeeds even when neither library is installed.

# _converter = None  # initialised on first call when Docling backend is active
#
# def _get_converter():
#     global _converter
#     if _converter is None:
#         from docling.document_converter import DocumentConverter as _DoclingConverter
#         _converter = _DoclingConverter()
#     return _converter


# ---------------------------------------------------------------------------
# Primary conversion — dispatches on CONVERTER_BACKEND
# ---------------------------------------------------------------------------

def convert_document(file_path: str) -> DoclingConversionResult:
    """Convert a document to narrative markdown and structured metadata.

    Dispatches to the backend selected by CONVERTER_BACKEND:
      - "langextract" (default): GPT-4o-mini via langextract.
          docling_document is always None; the downstream section_parser uses
          its markdown fallback path to parse the H2-structured output.
      - "docling": Docling primary path (see commented block below).
          docling_document carries the raw DoclingDocument for the section
          splitter's primary walk path.

    Args:
        file_path: Absolute path to the uploaded report (PDF/DOCX).

    Returns:
        DoclingConversionResult containing narrative_markdown, docling_document
        (None for the langextract backend), and page_count.

    Raises:
        DocumentConversionError: if the selected backend fails unrecoverably.
            Caller (pipeline.py) catches this and calls convert_document_fallback().
    """
    from report_ingestion.config import settings

    backend = getattr(settings, "CONVERTER_BACKEND", "langextract")

    # ------------------------------------------------------------------
    # LangExtract backend (current default)
    # ------------------------------------------------------------------
    if backend == "langextract":
        from report_ingestion.langextract_converter import convert_document_langextract
        model_id = getattr(settings, "LANGEXTRACT_MODEL_ID", "gpt-4o-mini")
        narrative_markdown, page_count = convert_document_langextract(
            file_path, model_id=model_id
        )
        return DoclingConversionResult(
            narrative_markdown=narrative_markdown,
            docling_document=None,  # langextract backend produces no DoclingDocument
            page_count=page_count,
        )

    # ------------------------------------------------------------------
    # Docling backend — commented out.
    # Restore by setting CONVERTER_BACKEND=docling in the environment.
    # ------------------------------------------------------------------
    # if backend == "docling":
    #     try:
    #         from docling.datamodel.base_models import ConversionStatus
    #         result = _get_converter().convert(source=file_path)
    #     except Exception as exc:
    #         raise DocumentConversionError(
    #             f"Docling failed to convert '{file_path}': {exc}"
    #         ) from exc
    #
    #     if result.status not in (
    #         ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS
    #     ):
    #         raise DocumentConversionError(
    #             f"Docling conversion of '{file_path}' ended with status {result.status}"
    #         )
    #
    #     doc = result.document
    #     narrative_markdown = doc.export_to_markdown()
    #     page_count = len(doc.pages)
    #     return DoclingConversionResult(
    #         narrative_markdown=narrative_markdown,
    #         docling_document=doc,
    #         page_count=page_count,
    #     )

    raise DocumentConversionError(
        f"Unknown CONVERTER_BACKEND '{backend}'. "
        "Valid values: 'langextract' (default), 'docling'."
    )


# ---------------------------------------------------------------------------
# Fallback conversion — PyMuPDF raw text
#
# This path is called by pipeline.py when convert_document() raises
# DocumentConversionError. It is relevant only for the Docling backend
# (where Docling itself can fail). The langextract backend handles its own
# internal fallback (raw PyMuPDF text) inside langextract_converter.py, so
# this function is not normally reached when CONVERTER_BACKEND=langextract.
# ---------------------------------------------------------------------------

def convert_document_fallback(file_path: str) -> str:
    """PyMuPDF raw-text fallback, called when convert_document() raises.

    Returns raw unstructured text with <!-- page N --> markers but no
    guaranteed heading or layout structure. Sufficient for the classifiers
    (text only), but section splitting degrades to regex/keyword mode.

    Note: with CONVERTER_BACKEND=langextract this function is not normally
    reached — langextract_converter.py already returns raw text when the LLM
    call fails, so convert_document() does not raise.
    """
    # PyMuPDF fallback — active for both backends as a last resort.
    # The Docling-specific fallback comments are kept below for reference.
    import fitz  # PyMuPDF

    # Previously used only when Docling raised DocumentConversionError:
    # try:
    #     from docling.datamodel.base_models import ConversionStatus
    #     result = _get_converter().convert(source=file_path)
    # except Exception as exc:
    #     raise DocumentConversionError(...)
    # ... (see git history for full Docling fallback logic)

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


# ---------------------------------------------------------------------------
# Shared utility — used by all four classifiers (backend-independent)
# ---------------------------------------------------------------------------

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
