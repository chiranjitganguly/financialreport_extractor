"""Step 6a — Cross-Section Discrepancy Resolution.

Callable from any tier (1, 2, or 3) the moment it detects that the same
KPI produced disagreeing values from different sections.  One GPT-4o
structured-output call determines the authoritative value; all other
candidates are preserved in conflicting_values for the human reviewer.

This step does NOT consume the shared 2-turn retry budget — it is an
inline, one-shot escalation, terminal on first detection.
"""

from __future__ import annotations

import logging
from typing import Literal, Optional

from pydantic import BaseModel

from common.config import settings as common_settings
from common.llm_client import get_llm_client
from common.prompts import DISCREPANCY_RESOLUTION_PROMPT
from common.schemas import (
    CandidateValue,
    ConflictingValue,
    ExtractionAttempt,
    ExtractionRecord,
    Section,
    TaxonomyEntry,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured output schema for the LLM call
# ---------------------------------------------------------------------------

class _DiscrepancyResolutionResult(BaseModel):
    chosen_value: str
    chosen_section: str
    chosen_source_element_type: Literal["text", "table_cell", "chart"]
    confidence: float
    reasoning: str


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def _format_candidates(candidates: list[CandidateValue]) -> str:
    lines = []
    for i, c in enumerate(candidates, 1):
        lines.append(
            f"{i}. Section: {c.section_name_canonical}  |  "
            f"Value: {c.value}  |  "
            f"Source: {c.source_element_type}  |  "
            f"Page: {c.page}"
        )
    return "\n".join(lines)


def _format_sections_context(sections: list[Section]) -> str:
    parts = []
    for s in sections:
        header = f"=== {s.section_name_canonical} (pages {s.page_range[0]}–{s.page_range[1]}) ==="
        # Include first 1 500 chars of narrative + table summary to stay within token budget
        narrative_excerpt = s.content_markdown[:1500].strip()
        table_summary = ""
        if s.tables:
            table_summary = f"\n[{len(s.tables)} table(s) in this section]"
        parts.append(f"{header}\n{narrative_excerpt}{table_summary}")
    return "\n\n".join(parts)


def resolve_cross_section_discrepancy(
    taxonomy_entry: TaxonomyEntry,
    candidates: list[CandidateValue],
    sections_involved: list[Section],
) -> ExtractionRecord:
    """Resolve a KPI where multiple sections produced disagreeing values.

    Args:
        taxonomy_entry: The KPI in question.
        candidates: 2+ CandidateValues with disagreeing values, one per source
            section (collected by whichever tier detected the disagreement).
        sections_involved: The full Section objects so the LLM gets real
            context, not just the bare numbers.

    Returns:
        ExtractionRecord with:
        - value = the LLM's chosen authoritative value
        - method = "llm"
        - status = "needs_human_review"
        - review_reason = "section_discrepancy"
        - conflicting_values = every candidate NOT chosen
        - attempts = one AttemptRecord recording this call

    Does NOT consume the shared 2-turn retry budget (per main spec §6a).
    """
    llm = get_llm_client(model=common_settings.DISCREPANCY_LLM_MODEL, provider=common_settings.DISCREPANCY_LLM_PROVIDER)
    chain = (
        DISCREPANCY_RESOLUTION_PROMPT
        | llm.with_structured_output(_DiscrepancyResolutionResult).with_retry()
    )

    candidates_text = _format_candidates(candidates)
    sections_context = _format_sections_context(sections_involved)

    try:
        result: _DiscrepancyResolutionResult = chain.invoke(
            {
                "kpi_name": taxonomy_entry.kpi_name,
                "definition": taxonomy_entry.definition,
                "fiscal_year": candidates[0].section_name_canonical,  # included in context
                "candidates_text": candidates_text,
                "sections_context": sections_context,
            }
        )
    except Exception:
        log.exception(
            "Discrepancy resolution LLM call failed for kpi_id=%s; "
            "falling back to first candidate.",
            taxonomy_entry.kpi_id,
        )
        result = _DiscrepancyResolutionResult(
            chosen_value=str(candidates[0].value),
            chosen_section=candidates[0].section_name_canonical,
            chosen_source_element_type=candidates[0].source_element_type,
            confidence=0.3,
            reasoning="LLM call failed; defaulted to first candidate.",
        )

    # The chosen candidate
    chosen_value_str = result.chosen_value
    conflicting: list[ConflictingValue] = [
        ConflictingValue(
            section=c.section_name_canonical,
            value=c.value,
            method="deterministic",   # whichever tier triggered this
            source_element_type=c.source_element_type,
        )
        for c in candidates
        if str(c.value) != chosen_value_str
    ]

    attempt = ExtractionAttempt(
        tier="llm",
        value=chosen_value_str,
        confidence=result.confidence,
        outcome="flagged",
        note=f"cross-section discrepancy resolution: {result.reasoning}",
    )

    return ExtractionRecord(
        kpi_id=taxonomy_entry.kpi_id,
        value=chosen_value_str,
        fiscal_year="",  # caller must stamp this from report_metadata
        section=result.chosen_section,
        page=next(
            (c.page for c in candidates if c.section_name_canonical == result.chosen_section),
            None,
        ),
        method="llm",
        source_element_type=result.chosen_source_element_type,
        footnotes=[fn for c in candidates for fn in c.footnotes],
        confidence=result.confidence,
        status="needs_human_review",
        review_reason="section_discrepancy",
        conflicting_values=conflicting,
        attempts=[attempt],
    )
