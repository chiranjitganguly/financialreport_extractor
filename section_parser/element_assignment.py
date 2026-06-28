"""
Agent 2 — Element Assignment.

Slots each TableElement, ChartElement, and FootnoteElement into the Section whose
page range contains the element's page number.

Fallback strategy for unmatched pages:
  - Assign to the nearest preceding section (closest section whose end page <=
    element.page).
  - If no preceding section exists, assign to the first section.
  - If no sections exist at all, log a warning and skip.
"""

from __future__ import annotations

import logging
from typing import Optional

from common.schemas import (
    ChartElement,
    FootnoteElement,
    Section,
    TableElement,
)
from section_parser.schemas import RawSection, SectionAlignmentResult

log = logging.getLogger(__name__)


def _find_section_for_page(
    page: int,
    sections: list[RawSection],
) -> Optional[int]:
    """Return the index of the section that contains *page*, or None.

    Primary: section where page_range[0] <= page <= page_range[1].
    Fallback: nearest preceding section (largest end page <= page).
    """
    # Primary: page falls within a section's range
    for i, sec in enumerate(sections):
        start, end = sec.page_range
        if start <= page <= end:
            return i

    # Fallback: nearest preceding section
    best_idx: Optional[int] = None
    best_end = -1
    for i, sec in enumerate(sections):
        end = sec.page_range[1]
        if end <= page and end > best_end:
            best_end = end
            best_idx = i

    return best_idx


def assign_elements_to_sections(
    raw_sections: list[RawSection],
    alignments: list[SectionAlignmentResult],
    tables: list[TableElement],
    charts: list[ChartElement],
    footnotes: list[FootnoteElement],
) -> list[Section]:
    """Assemble fully-populated Section objects.

    For each table/chart/footnote, finds the section whose page_range contains
    the element's page and appends it to that section's list. Falls back to the
    nearest preceding section for unmatched pages, or the first section if there
    is no preceding section.

    Args:
        raw_sections: Output of split_into_raw_sections() — in document order.
        alignments:   One SectionAlignmentResult per raw section, same index
                      order. Produced by alignment.py; the pipeline.py caller
                      overwrites alignment_source to "best_guess_unresolved" for
                      below-threshold results before calling this function.
        tables:       From Agent 1b (or synthetic fixtures).
        charts:       From Agent 1b (or synthetic fixtures).
        footnotes:    From Agent 1b (or synthetic fixtures).

    Returns:
        list[Section] — fully assembled, matching common/schemas.py's Section
        contract exactly.
    """
    if not raw_sections:
        n = len(tables) + len(charts) + len(footnotes)
        if n > 0:
            log.warning(
                "assign_elements_to_sections: no sections available; "
                "skipping %d unassignable elements.",
                n,
            )
        return []

    # Pre-allocate per-section element buckets
    section_tables: list[list[TableElement]] = [[] for _ in raw_sections]
    section_charts: list[list[ChartElement]] = [[] for _ in raw_sections]
    section_footnotes: list[list[FootnoteElement]] = [[] for _ in raw_sections]

    def _assign(elements, buckets, label: str):
        for elem in elements:
            idx = _find_section_for_page(elem.page, raw_sections)
            if idx is None:
                # No preceding section either — fall back to first section
                idx = 0
                log.warning(
                    "%s (page %d) could not be matched to any section; "
                    "assigning to first section '%s'.",
                    label,
                    elem.page,
                    raw_sections[0].section_name_raw,
                )
            buckets[idx].append(elem)

    _assign(tables, section_tables, "TableElement")
    _assign(charts, section_charts, "ChartElement")
    _assign(footnotes, section_footnotes, "FootnoteElement")

    # Build final Section objects
    sections: list[Section] = []
    for i, (raw_sec, alignment) in enumerate(zip(raw_sections, alignments)):
        sections.append(
            Section(
                section_name_raw=raw_sec.section_name_raw,
                section_name_canonical=alignment.section_name_canonical,
                alignment_confidence=alignment.confidence,
                alignment_source=alignment.source,
                content_markdown=raw_sec.content_markdown,
                tables=section_tables[i],
                charts=section_charts[i],
                footnotes=section_footnotes[i],
                page_range=raw_sec.page_range,
            )
        )

    return sections
