"""Agent 4 — Tier 1: Deterministic Extraction.

Cheapest, highest-confidence extraction pass.  No LLM calls — only fuzzy
string matching against table row labels and regex matching against
narrative text.  An LLM is called only when a discrepancy is detected
(via common.discrepancy_resolution), and even then not by this module
directly — it delegates to Step 6a.

Logic per KPI (from the main spec):
  1. Fetch ALL sections whose section_name_canonical appears in
     taxonomy_entry.canonical_sections.
  2. Within each section, check tables first, then narrative text.
  3. Collect every CandidateValue found across all sections.
  4. 0 candidates  → leave status="not_found" (flows to Tier 2).
     1 distinct value (one or many agreeing sections) → status="found".
     >1 distinct values → Step 6a (discrepancy resolution).
"""

from __future__ import annotations

import logging
from collections import defaultdict

from common.deterministic_matching import match_narrative_text, match_table_row
from common.discrepancy_resolution import resolve_cross_section_discrepancy
from common.schemas import (
    CandidateValue,
    ExtractionAttempt,
    ExtractionLedger,
    ExtractionRecord,
    Section,
    TaxonomyEntry,
)
from deterministic_extractor.config import settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-KPI extraction
# ---------------------------------------------------------------------------

def extract_deterministic_candidates(
    taxonomy_entry: TaxonomyEntry,
    sections_by_canonical_name: dict[str, list[Section]],
    fiscal_year: str,
    fuzzy_cutoff: float,
) -> list[CandidateValue]:
    """Collect every candidate value for one KPI across all its relevant sections.

    Args:
        taxonomy_entry: The KPI being extracted.
        sections_by_canonical_name: All sections for this report, grouped by
            section_name_canonical.  More than one Section can share a canonical
            name — all are checked (Agent 2 design note).
        fiscal_year: report_metadata.fiscal_year — the target column.
        fuzzy_cutoff: Minimum WRatio score for table-row matching (0–100).

    Returns:
        List of CandidateValues, possibly empty, possibly containing
        disagreeing values from different sections.
    """
    candidates: list[CandidateValue] = []

    for canonical_name in taxonomy_entry.canonical_sections:
        relevant_sections = sections_by_canonical_name.get(canonical_name, [])
        for section in relevant_sections:
            section_candidate: CandidateValue | None = None

            # Tables first
            for table in section.tables:
                cv = match_table_row(
                    table=table,
                    kpi_name=taxonomy_entry.kpi_name,
                    aliases=taxonomy_entry.aliases,
                    fiscal_year=fiscal_year,
                    fuzzy_cutoff=fuzzy_cutoff,
                )
                if cv is not None:
                    section_candidate = cv
                    break  # first matching table wins for this section

            # Narrative fallback if no table match
            if section_candidate is None:
                cv = match_narrative_text(
                    content_markdown=section.content_markdown,
                    kpi_name=taxonomy_entry.kpi_name,
                    aliases=taxonomy_entry.aliases,
                    section_page_range=section.page_range,
                )
                if cv is not None:
                    cv = cv.model_copy(
                        update={"section_name_canonical": section.section_name_canonical}
                    )
                    section_candidate = cv

            if section_candidate is not None:
                candidates.append(section_candidate)

    return candidates


# ---------------------------------------------------------------------------
# Tier 1 orchestration
# ---------------------------------------------------------------------------

def run_deterministic_extraction(
    ledger: ExtractionLedger,
    filtered_taxonomy: list[TaxonomyEntry],
    sections: list[Section],
    fiscal_year: str,
) -> ExtractionLedger:
    """Run Tier 1 extraction over every not_found ledger entry.

    Args:
        ledger: Starting ledger — all entries status="not_found".
        filtered_taxonomy: Taxonomy rows applicable to this report
            (output of filter_applicable_taxonomy()).
        sections: Agent 2's full section list for this report.
        fiscal_year: report_metadata.fiscal_year.

    Returns:
        Updated ExtractionLedger.  Resolved entries get method="deterministic"
        and status="found".  Discrepancy entries get status="needs_human_review".
        Unresolved entries remain status="not_found" (flow to Tier 2).
    """
    # Group sections by canonical name once; cheaper than re-scanning per KPI.
    sections_by_canonical: dict[str, list[Section]] = defaultdict(list)
    for s in sections:
        sections_by_canonical[s.section_name_canonical].append(s)

    taxonomy_by_id = {e.kpi_id: e for e in filtered_taxonomy}

    for kpi_id, record in ledger.records.items():
        if record.status != "not_found":
            continue

        entry = taxonomy_by_id.get(kpi_id)
        if entry is None:
            continue

        candidates = extract_deterministic_candidates(
            taxonomy_entry=entry,
            sections_by_canonical_name=dict(sections_by_canonical),
            fiscal_year=fiscal_year,
            fuzzy_cutoff=settings.TABLE_ROW_FUZZY_CUTOFF,
        )

        if not candidates:
            record.attempts.append(
                ExtractionAttempt(
                    tier="deterministic",
                    value=None,
                    confidence=0.0,
                    outcome="not_found",
                    note="no table row or narrative match found",
                )
            )
            continue

        distinct_values = {str(c.value) for c in candidates}

        if len(distinct_values) == 1:
            # All sections agree — use the highest-confidence candidate.
            best = max(candidates, key=lambda c: c.confidence)
            record.value = best.value
            record.section = best.section_name_canonical
            record.page = best.page
            record.method = "deterministic"
            record.source_element_type = best.source_element_type
            record.footnotes = list({fn for c in candidates for fn in c.footnotes})
            record.confidence = best.confidence
            record.status = "found"
            record.attempts.append(
                ExtractionAttempt(
                    tier="deterministic",
                    value=best.value,
                    confidence=best.confidence,
                    outcome="found",
                    note=f"matched in {best.section_name_canonical} via {best.source_element_type}",
                )
            )
            log.debug(
                "Tier 1 found kpi_id=%s value=%s confidence=%.2f",
                kpi_id, best.value, best.confidence,
            )

        else:
            # Multiple disagreeing values → Step 6a
            sections_involved = [
                s for s in sections
                if s.section_name_canonical in {c.section_name_canonical for c in candidates}
            ]
            resolved = resolve_cross_section_discrepancy(
                taxonomy_entry=entry,
                candidates=candidates,
                sections_involved=sections_involved,
            )
            resolved.fiscal_year = fiscal_year
            ledger.records[kpi_id] = resolved
            log.info(
                "Tier 1 discrepancy for kpi_id=%s: values=%s → LLM chose %s",
                kpi_id, list(distinct_values), resolved.value,
            )

    return ledger
