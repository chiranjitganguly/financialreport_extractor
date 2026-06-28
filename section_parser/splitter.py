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

# Known financial section title patterns for keyword-scan fallback.
# Each entry: (regex_pattern, canonical_section_name)
# Ordered most-specific first so greedy overlaps don't misclassify.
_FINANCIAL_SECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:standalone\s+|consolidated\s+)?statement\s+of\s+profit\s+(?:and|&)\s+loss\b", re.IGNORECASE), "Statement of Profit and Loss"),
    (re.compile(r"\b(?:standalone\s+|consolidated\s+)?(?:balance\s+sheet|statement\s+of\s+financial\s+position)\b", re.IGNORECASE), "Balance Sheet"),
    (re.compile(r"\b(?:standalone\s+|consolidated\s+)?(?:statement\s+of\s+cash\s+flow|cash\s+flow\s+statement)\b", re.IGNORECASE), "Statement of Cash Flows"),
    (re.compile(r"\b(?:standalone\s+|consolidated\s+)?(?:notes\s+to\s+(?:the\s+)?(?:standalone\s+|consolidated\s+)?financial\s+statements?)\b", re.IGNORECASE), "Notes to Financial Statements"),
    (re.compile(r"\bmanagement(?:'?s?)?\s+discussion\s+(?:and\s+analysis|&\s+analysis)\b", re.IGNORECASE), "Management Discussion and Analysis"),
    (re.compile(r"\bfinancial\s+highlights?\b", re.IGNORECASE), "Financial Highlights"),
]


def _looks_like_body_text(heading: str) -> bool:
    """Return True when a detected heading looks like a body-text fragment.

    PyMuPDF sometimes detects bold footnote text as H1 headings.  Real
    section headings are short (< 80 chars), mostly title-case, and do not
    start with lowercase connective words.
    """
    if len(heading) > 80:
        return True
    words = heading.split()
    if not words:
        return True
    # Starts with a lowercase word that is not a meaningful title start
    if words[0][0].islower():
        return True
    # More than half the words are lowercase → sentence fragment
    lower_words = sum(1 for w in words if w[0].islower() and len(w) > 2)
    if lower_words > len(words) // 2 and len(words) > 3:
        return True
    return False


def _split_by_keyword_scan(narrative_markdown: str) -> list[RawSection]:
    """Last-resort section splitter used when regex heading detection yields
    only body-text fragments (e.g. PyMuPDF bold-font false positives).

    Scans the document for known financial section title patterns and uses
    their positions as section boundaries.  Sections between unrecognised
    stretches are grouped under 'OTHER'.
    """
    # Find all matches across all patterns, record (pos, title_text, match_end)
    hits: list[tuple[int, str, int]] = []
    for pattern, section_name in _FINANCIAL_SECTION_PATTERNS:
        for m in pattern.finditer(narrative_markdown):
            # Avoid flagging occurrences inside longer sentences (check that the
            # match starts at a "line boundary" — within 120 chars of a newline).
            preceding = narrative_markdown[max(0, m.start() - 120):m.start()]
            if "\n" not in preceding and len(preceding) > 80:
                continue  # mid-sentence mention, not a heading
            hits.append((m.start(), section_name, m.end()))

    if not hits:
        # Nothing found — return entire document as one section
        page = _infer_page_from_markdown(narrative_markdown)
        return [RawSection(section_name_raw="Document", content_markdown=narrative_markdown.strip(), page_range=(page, page))]

    # Sort by position; deduplicate overlapping hits (keep earliest per position cluster)
    hits.sort(key=lambda x: x[0])
    deduped: list[tuple[int, str, int]] = []
    for pos, name, end in hits:
        if deduped and pos - deduped[-1][0] < 50:
            continue  # too close to previous hit, skip
        deduped.append((pos, name, end))

    # Build sections from the hit boundaries
    sections: list[RawSection] = []
    for i, (pos, name, end) in enumerate(deduped):
        next_pos = deduped[i + 1][0] if i + 1 < len(deduped) else len(narrative_markdown)
        body = narrative_markdown[pos:next_pos].strip()
        slice_text = narrative_markdown[pos:next_pos]
        page_numbers = [int(p) for p in _PAGE_MARKER_RE.findall(slice_text)]
        if page_numbers:
            page_range = (min(page_numbers), max(page_numbers))
        else:
            prev = sections[-1].page_range[1] if sections else 1
            page_range = (prev, prev)
        sections.append(RawSection(section_name_raw=name, content_markdown=body, page_range=page_range))

    return sections


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
    """Parse sections from flat markdown using regex heading detection.

    When detected headings all look like body-text fragments (PyMuPDF
    bold-font false positives), falls back to keyword scanning for known
    financial section title patterns.
    """
    # Match headings whose level (number of leading #) <= heading_level_cutoff
    pattern = re.compile(
        r"^(#{1," + str(heading_level_cutoff) + r"})\s+(.+)$", re.MULTILINE
    )

    splits: list[tuple[str, int]] = []  # (heading_text, match_start_pos)
    for m in pattern.finditer(narrative_markdown):
        splits.append((m.group(2).strip(), m.start()))

    if not splits:
        # No headings found at all — try keyword scan before giving up
        log.warning("No markdown headings found — attempting keyword-scan fallback.")
        kw_sections = _split_by_keyword_scan(narrative_markdown)
        if len(kw_sections) > 1:
            return kw_sections
        page = _infer_page_from_markdown(narrative_markdown)
        return [
            RawSection(
                section_name_raw="Document",
                content_markdown=narrative_markdown.strip(),
                page_range=(page, page),
            )
        ]

    # Check whether all detected headings look like body-text fragments.
    body_text_count = sum(1 for heading, _ in splits if _looks_like_body_text(heading))
    if body_text_count == len(splits):
        log.warning(
            "All %d detected headings look like body text (PyMuPDF bold false-positive) "
            "— falling back to keyword-scan for financial section titles.",
            len(splits),
        )
        kw_sections = _split_by_keyword_scan(narrative_markdown)
        if len(kw_sections) > 1:
            return kw_sections
        # Keyword scan also found nothing useful — use the body-text headings as-is
        # (section alignment will map them to OTHER / LLM fallback).
        log.warning("Keyword scan also found no financial section titles; using raw headings.")

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
    # Threshold above which Docling is considered to have over-split the document
    # into micro-sections (e.g. individual table rows / footnote fragments).
    _DOCLING_OVERSPLIT_THRESHOLD = 300

    if docling_document is not None and _DOCLING_AVAILABLE:
        try:
            sections = _split_docling(docling_document, heading_level_cutoff)
            if sections:
                if len(sections) > _DOCLING_OVERSPLIT_THRESHOLD:
                    # Docling produced too many micro-sections — likely because the
                    # document has many fine-grained headings (note sub-headings,
                    # table row labels promoted to headings by Docling's bold-font
                    # detector). In this case the financial statement sections in
                    # the Docling walk are often ToC entries with empty content
                    # while the narrative_markdown export has them as proper H2
                    # sections with the actual table data included.
                    #
                    # Check whether the narrative_markdown has any section whose
                    # raw name directly matches a key financial section title.
                    # If it does, prefer narrative_markdown since it places the
                    # real balance sheet / P&L / cash flow data under those headings.
                    log.warning(
                        "Docling produced %d sections (> %d threshold) — checking "
                        "narrative_markdown for better financial section boundaries.",
                        len(sections),
                        _DOCLING_OVERSPLIT_THRESHOLD,
                    )
                    md_sections = _split_markdown_fallback(narrative_markdown, heading_level_cutoff)
                    # Check if markdown sections include key financial headings
                    # that appear verbatim (not just as ToC titles) with real content
                    financial_kws = (
                        "balance sheet", "profit and loss", "cash flow statement",
                        "statement of cash", "statement of profit",
                    )
                    md_financial = [
                        s for s in md_sections
                        if any(kw in s.section_name_raw.lower() for kw in financial_kws)
                        and len(s.content_markdown) > 200  # real content, not a ToC stub
                    ]
                    if md_financial:
                        log.info(
                            "narrative_markdown has %d financial sections with real content "
                            "— using markdown path (%d total sections).",
                            len(md_financial), len(md_sections),
                        )
                        return md_sections
                    log.warning(
                        "narrative_markdown has no substantial financial sections; "
                        "keeping Docling result (%d sections).",
                        len(sections),
                    )
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
