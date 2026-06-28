"""
Agent 2 — Top-level orchestration pipeline.

Orchestrates:
  1. Heading-split via splitter.split_into_raw_sections()
  2. Stage A fuzzy alignment for every section
  3. Stage B batched LLM alignment for all sections Stage A missed (one call)
  4. Per-section retry (realign_section_low_confidence) for below-threshold
     results, subject to the shared retry budget
  5. Element assignment via element_assignment.assign_elements_to_sections()

Public API:
  run_section_parser(...)  — async, returns SectionParserOutput
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from section_parser.config import settings
from section_parser.alignment import (
    align_section_fuzzy,
    batch_align_sections_llm,
    realign_section_low_confidence,
)
from section_parser.element_assignment import assign_elements_to_sections
from section_parser.schemas import (
    SectionParserOutput,
    RawSection,
    SectionAlignmentResult,
)
from section_parser.splitter import split_into_raw_sections
from common.schemas import (
    ChartElement,
    FootnoteElement,
    ReportMetadata,
    TableElement,
)

log = logging.getLogger(__name__)

# Content excerpt length to pass to the LLM in Stage B and retry calls.
_CONTENT_EXCERPT_MAX_CHARS: int = 500
_RETRY_CONTENT_EXCERPT_MAX_CHARS: int = 1000


def _make_excerpt(content_markdown: str, max_chars: int) -> str:
    """Return at most *max_chars* characters of *content_markdown*."""
    return content_markdown[:max_chars]


async def run_section_parser(
    docling_document,
    report_metadata: ReportMetadata,
    narrative_markdown: str,
    tables: list[TableElement],
    charts: list[ChartElement],
    footnotes: list[FootnoteElement],
    canonical_vocabulary: list[str],
    remaining_retry_budget: int,
) -> SectionParserOutput:
    """Top-level Agent 2 orchestration.

    Args:
        docling_document: Passed through from Agent 1's converter.  None if
            Agent 1 used the PyMuPDF fallback.
        report_metadata: Metadata from Agent 1 (accepted for downstream
            consistency even if not used for branching logic here).
        narrative_markdown: Always-available flat text from Agent 1; used as
            the fallback split source when docling_document is None.
        tables, charts, footnotes: From Agent 1b (or synthetic fixtures).
        canonical_vocabulary: Output of get_canonical_section_vocabulary(),
            loaded once at startup and passed in here.
        remaining_retry_budget: How many of the report's shared 2 turns remain.
            Decremented locally for each realignment attempt; NOT tracked
            internally — the Orchestrator owns the global budget.

    Returns:
        SectionParserOutput with fully-assembled sections, retry_turns_used (consumed
        here), and has_unresolved_sections flag.
    """
    # ------------------------------------------------------------------
    # Step 1: Split document into raw sections
    # ------------------------------------------------------------------
    raw_sections: list[RawSection] = split_into_raw_sections(
        docling_document=docling_document,
        narrative_markdown=narrative_markdown,
        heading_level_cutoff=settings.SECTION_HEADING_LEVEL_CUTOFF,
    )

    if not raw_sections:
        log.warning(
            "run_section_parser: no sections found for report '%s'; "
            "returning empty SectionParserOutput.",
            report_metadata.report_id,
        )
        return SectionParserOutput(
            report_id=report_metadata.report_id,
            sections=[],
            retry_turns_used=0,
            has_unresolved_sections=False,
        )

    # ------------------------------------------------------------------
    # Step 2: Stage A — sync fuzzy alignment for every section
    # ------------------------------------------------------------------
    alignments: list[SectionAlignmentResult | None] = []
    unresolved_indices: list[int] = []  # indices into raw_sections / alignments

    for i, raw_sec in enumerate(raw_sections):
        result = align_section_fuzzy(
            section_name_raw=raw_sec.section_name_raw,
            canonical_vocabulary=canonical_vocabulary,
            fuzzy_cutoff=settings.SECTION_ALIGNMENT_FUZZY_CUTOFF,
        )
        alignments.append(result)
        if result is None:
            unresolved_indices.append(i)

    # ------------------------------------------------------------------
    # Step 3: Stage B — batched LLM call for all Stage A misses (one call)
    # ------------------------------------------------------------------
    if unresolved_indices:
        unresolved_pairs = [
            (
                raw_sections[i].section_name_raw,
                _make_excerpt(raw_sections[i].content_markdown, _CONTENT_EXCERPT_MAX_CHARS),
            )
            for i in unresolved_indices
        ]
        llm_results = await batch_align_sections_llm(
            unresolved=unresolved_pairs,
            vocabulary=canonical_vocabulary,
        )
        for i, result in zip(unresolved_indices, llm_results):
            alignments[i] = result

    # At this point alignments[i] is never None (LLM provided a result or the
    # fuzzy match succeeded).  Assert for type narrowing.
    final_alignments: list[SectionAlignmentResult] = []
    for i, a in enumerate(alignments):
        if a is None:
            # Shouldn't happen, but guard against it
            log.error(
                "Alignment for section '%s' is still None after Stage B; "
                "substituting zero-confidence OTHER.",
                raw_sections[i].section_name_raw,
            )
            final_alignments.append(
                SectionAlignmentResult(
                    section_name_canonical="OTHER",
                    confidence=0.0,
                    source="llm_fallback",
                )
            )
        else:
            final_alignments.append(a)

    # ------------------------------------------------------------------
    # Step 4: Retry below-threshold results (subject to retry budget)
    # ------------------------------------------------------------------
    threshold = settings.SECTION_ALIGNMENT_CONFIDENCE_THRESHOLD
    retry_turns_used = 0
    local_budget = remaining_retry_budget

    # Gather all sections that need retry so we can fire them concurrently
    retry_candidates = [
        i
        for i, a in enumerate(final_alignments)
        if a.confidence < threshold and local_budget > 0
    ]

    if retry_candidates:
        # Consume budget entries up to local_budget
        to_retry = retry_candidates[:local_budget]

        async def _retry_one(i: int) -> tuple[int, SectionAlignmentResult]:
            raw_sec = raw_sections[i]
            prev = final_alignments[i]
            new_result = await realign_section_low_confidence(
                section_name_raw=raw_sec.section_name_raw,
                content_excerpt=_make_excerpt(
                    raw_sec.content_markdown, _RETRY_CONTENT_EXCERPT_MAX_CHARS
                ),
                vocabulary=canonical_vocabulary,
                previous_result=prev,
            )
            # Keep new result only if confidence improved
            if new_result.confidence > prev.confidence:
                return i, new_result
            return i, prev

        retry_results = await asyncio.gather(*(_retry_one(i) for i in to_retry))
        for i, result in retry_results:
            final_alignments[i] = result
        retry_turns_used = len(to_retry)

    # ------------------------------------------------------------------
    # Step 5: Mark still-below-threshold sections as "best_guess_unresolved"
    # ------------------------------------------------------------------
    for i, a in enumerate(final_alignments):
        if a.confidence < threshold:
            final_alignments[i] = SectionAlignmentResult(
                section_name_canonical=a.section_name_canonical,
                confidence=a.confidence,
                source="best_guess_unresolved",
            )

    # ------------------------------------------------------------------
    # Step 6: Element assignment
    # ------------------------------------------------------------------
    sections = assign_elements_to_sections(
        raw_sections=raw_sections,
        alignments=final_alignments,
        tables=tables,
        charts=charts,
        footnotes=footnotes,
    )

    has_unresolved = any(
        s.alignment_source == "best_guess_unresolved" for s in sections
    )

    return SectionParserOutput(
        report_id=report_metadata.report_id,
        sections=sections,
        retry_turns_used=retry_turns_used,
        has_unresolved_sections=has_unresolved,
    )
