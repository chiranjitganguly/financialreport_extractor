"""
Agent 2 — Section Splitter.

Splits a document into RawSection objects by walking heading structure.

Primary path:  DoclingDocument  (structured heading + page provenance)
Fallback path: narrative_markdown string  (regex heading detection)
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from section_parser.schemas import RawSection

log = logging.getLogger(__name__)

# Docling imports are guarded so the module can be imported in environments
# where Docling is not installed (e.g. lightweight unit-test containers).
try:
    from docling_core.types.doc.document import (
        SectionHeaderItem,
        TextItem,
        TitleItem,
    )
    from docling.datamodel.base_models import DocItemLabel

    _DOCLING_AVAILABLE = True
except ImportError:  # pragma: no cover
    _DOCLING_AVAILABLE = False


# ---------------------------------------------------------------------------
# Regex-based markdown fallback (used when docling_document is None)
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_PAGE_MARKER_RE = re.compile(r"<!--\s*page\s+(\d+)\s*-->", re.IGNORECASE)


def _infer_page_from_markdown(text: str, default: int = 1) -> int:
    """Return the last page marker seen before / within `text`."""
    markers = _PAGE_MARKER_RE.findall(text)
    if markers:
        return int(markers[-1])
    return default


def _split_markdown_fallback(
    narrative_markdown: str,
    heading_level_cutoff: int,
) -> list[RawSection]:
    """Parse sections from flat markdown using regex heading detection."""
    # Match headings whose level (number of leading #) <= heading_level_cutoff
    pattern = re.compile(
        r"^(#{1," + str(heading_level_cutoff) + r"})\s+(.+)$", re.MULTILINE
    )

    splits: list[tuple[str, int]] = []  # (heading_text, match_start_pos)
    for m in pattern.finditer(narrative_markdown):
        splits.append((m.group(2).strip(), m.start()))

    if not splits:
        # No headings found — treat the whole document as one unnamed section
        page = _infer_page_from_markdown(narrative_markdown)
        return [
            RawSection(
                section_name_raw="Document",
                content_markdown=narrative_markdown.strip(),
                page_range=(page, page),
            )
        ]

    sections: list[RawSection] = []
    for i, (heading, start_pos) in enumerate(splits):
        end_pos = splits[i + 1][1] if i + 1 < len(splits) else len(narrative_markdown)
        body = narrative_markdown[start_pos:end_pos].strip()

        # Infer pages from <!-- page N --> markers within this slice
        slice_text = narrative_markdown[start_pos:end_pos]
        page_numbers = [int(p) for p in _PAGE_MARKER_RE.findall(slice_text)]
        if page_numbers:
            page_range = (min(page_numbers), max(page_numbers))
        else:
            prev_page = (
                sections[-1].page_range[1] if sections else 1
            )
            page_range = (prev_page, prev_page)

        sections.append(
            RawSection(
                section_name_raw=heading,
                content_markdown=body,
                page_range=page_range,
            )
        )

    return sections


# ---------------------------------------------------------------------------
# Docling primary path
# ---------------------------------------------------------------------------

_CONTENT_LABELS: set = set()  # populated lazily after import check


def _get_content_labels():
    global _CONTENT_LABELS
    if not _CONTENT_LABELS and _DOCLING_AVAILABLE:
        _CONTENT_LABELS = {
            DocItemLabel.TEXT,
            DocItemLabel.PARAGRAPH,
            DocItemLabel.LIST_ITEM,
            DocItemLabel.CAPTION,
            DocItemLabel.FOOTNOTE,
            DocItemLabel.FORMULA,
        }
    return _CONTENT_LABELS


def _is_section_start(item, heading_level_cutoff: int) -> bool:
    """Return True if *item* starts a new top-level section."""
    if not _DOCLING_AVAILABLE:
        return False
    if isinstance(item, TitleItem):
        return True
    if isinstance(item, SectionHeaderItem):
        return item.level <= heading_level_cutoff
    return False


def _get_page(item) -> Optional[int]:
    """Extract page number from a Docling item's provenance."""
    try:
        if item.prov:
            return item.prov[0].page_no
    except (AttributeError, IndexError):
        pass
    return None


def _split_docling(docling_document, heading_level_cutoff: int) -> list[RawSection]:
    """Primary path: walk DoclingDocument.iterate_items()."""
    sections: list[RawSection] = []
    current_heading: Optional[str] = None
    current_lines: list[str] = []
    current_pages: list[int] = []

    content_labels = _get_content_labels()

    for item, _tree_depth in docling_document.iterate_items():
        if _is_section_start(item, heading_level_cutoff):
            # Flush accumulated content into a RawSection
            if current_heading is not None:
                page_range = (
                    (min(current_pages), max(current_pages))
                    if current_pages
                    else (1, 1)
                )
                sections.append(
                    RawSection(
                        section_name_raw=current_heading,
                        content_markdown="\n\n".join(current_lines),
                        page_range=page_range,
                    )
                )
            current_heading = item.text.strip()
            current_lines = []
            current_pages = []
            page = _get_page(item)
            if page is not None:
                current_pages.append(page)

        elif current_heading is not None:
            # Accumulate content items under the current heading
            label = getattr(item, "label", None)
            if label in content_labels:
                text = getattr(item, "text", None)
                if text:
                    current_lines.append(text.strip())
                page = _get_page(item)
                if page is not None:
                    current_pages.append(page)

    # Flush last section
    if current_heading is not None:
        page_range = (
            (min(current_pages), max(current_pages)) if current_pages else (1, 1)
        )
        sections.append(
            RawSection(
                section_name_raw=current_heading,
                content_markdown="\n\n".join(current_lines),
                page_range=page_range,
            )
        )

    return sections


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def split_into_raw_sections(
    docling_document,
    narrative_markdown: str,
    heading_level_cutoff: int = 2,
) -> list[RawSection]:
    """Split a document into RawSection objects.

    Primary path (when *docling_document* is not None): walks the structured
    DoclingDocument, grouping content under TitleItem or SectionHeaderItem
    elements where ``item.level <= heading_level_cutoff``.

    Fallback path (when *docling_document* is None, i.e. Agent 1 used PyMuPDF):
    parses *narrative_markdown* with a regex heading detector, inferring page
    numbers from ``<!-- page N -->`` markers embedded by the fallback converter.

    Args:
        docling_document: DoclingDocument from Agent 1's converter, or None if
            the PyMuPDF fallback was used.
        narrative_markdown: Always-available flat text representation.  Used as
            the fallback when docling_document is None.
        heading_level_cutoff: Maximum heading level (1-based) that triggers a
            new section.  H1/H2 create sections (cutoff=2); H3+ are treated as
            sub-headings within the current section.

    Returns:
        list[RawSection] in document order with page_range derived from
        provenance (Docling path) or ``<!-- page N -->`` markers (fallback path).
        Defaults to ``(1, 1)`` if no provenance is available.
    """
    if docling_document is not None and _DOCLING_AVAILABLE:
        try:
            sections = _split_docling(docling_document, heading_level_cutoff)
            if sections:
                return sections
            # If Docling walk yielded nothing, fall through to markdown
            log.warning(
                "Docling walk returned no sections; falling back to markdown parser."
            )
        except Exception as exc:  # pragma: no cover
            log.warning(
                "Docling section split failed (%s); falling back to markdown parser.",
                exc,
            )

    return _split_markdown_fallback(narrative_markdown, heading_level_cutoff)
