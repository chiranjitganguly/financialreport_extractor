"""Agent 6 — Tier 3: LLM Extraction.

Last resort after Tiers 1 and 2 failed.  Batches ALL still-unresolved KPIs
that share a canonical section into ONE GPT-4o structured-output call per
section — one API call per section, not one per KPI.  This is the spec's
explicit batching decision and it's critical for cost control.

Key differences from Tiers 1/2:
  - Batching is BY SECTION, not by KPI — the collection loop is inverted.
  - method="llm" on resolved entries.
  - Low-confidence results (below LOW_CONFIDENCE_THRESHOLD) route to
    needs_human_review with review_reason="low_confidence" rather than "found".
  - Truly not-found KPIs (LLM said found=False across every relevant section)
    stay status="not_found" — this is the terminal state; there are no further
    tiers after this one.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Literal, Optional

from pydantic import BaseModel

from llm_extractor.config import settings
from llm_extractor.prompts import TIER3_EXTRACTION_PROMPT
from common.discrepancy_resolution import resolve_cross_section_discrepancy
from common.llm_client import get_llm_client
from common.schemas import (
    CandidateValue,
    ExtractionAttempt,
    ExtractionLedger,
    FootnoteElement,
    Section,
    TableElement,
    TaxonomyEntry,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / result schemas (Tier 3 local; not part of the wire contract)
# ---------------------------------------------------------------------------

class Tier3KPIRequest(BaseModel):
    kpi_id: str
    kpi_name: str
    aliases: list[str]
    definition: str


class Tier3ExtractionResult(BaseModel):
    kpi_id: str
    found: bool
    value: Optional[str] = None  # LLM returns strings; callers can parse to numeric
    page: Optional[int] = None
    source_element_type: Optional[Literal["text", "table_cell", "chart"]] = None
    footnote_ids: list[str] = []
    confidence: float = 0.0


class _Tier3BatchOutput(BaseModel):
    """Wrapper so the structured-output schema is a top-level object, not a list."""

    extractions: list[Tier3ExtractionResult]


# ---------------------------------------------------------------------------
# Section context assembly
# ---------------------------------------------------------------------------

def _table_to_markdown(table: TableElement) -> str:
    """Convert a TableElement to a compact markdown table.

    Token-efficient relative to JSON; GPT-4o reads markdown tables accurately.
    """
    if not table.rows:
        return ""

    # Collect column labels in first-appearance order
    seen_cols: set[str] = set()
    ordered_cols: list[str] = []
    for row in table.rows:
        for cell in row.cells:
            if cell.column_label not in seen_cols:
                seen_cols.add(cell.column_label)
                ordered_cols.append(cell.column_label)

    caption = table.caption or "Table"
    header = "| Item | " + " | ".join(ordered_cols) + " |"
    separator = "|------|" + "|".join(["------"] * len(ordered_cols)) + "|"

    md_rows = [f"**{caption}** (page {table.page})", header, separator]
    for row in table.rows:
        cells_by_col = {c.column_label: str(c.value) for c in row.cells}
        values = [cells_by_col.get(col, "") for col in ordered_cols]
        md_rows.append("| " + row.row_label + " | " + " | ".join(values) + " |")

    return "\n".join(md_rows)


def _footnotes_to_text(footnotes: list[FootnoteElement]) -> str:
    if not footnotes:
        return ""
    lines = ["**Footnotes:**"]
    for fn in footnotes:
        lines.append(f"[{fn.marker}] {fn.text}")
    return "\n".join(lines)


def build_section_context_for_prompt(section: Section) -> str:
    """Assemble a section into a prompt-ready context string.

    Includes narrative text, tables (markdown format), footnotes, and chart
    interpretations.  Charts carry interpretation text only — vision input is
    deferred until Agent 1b is built (section.charts is always [] until then).

    Args:
        section: Full Section from Agent 2.

    Returns:
        str: Context block ready to insert into the Tier 3 prompt.
    """
    parts: list[str] = []

    if section.content_markdown.strip():
        parts.append(section.content_markdown.strip())

    for table in section.tables:
        md = _table_to_markdown(table)
        if md:
            parts.append(md)

    fn_text = _footnotes_to_text(section.footnotes)
    if fn_text:
        parts.append(fn_text)

    for chart in section.charts:
        if chart.interpretation.strip():
            caption = chart.caption or "Chart"
            parts.append(f"**{caption} (page {chart.page})**\n{chart.interpretation}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Prompt formatting helpers
# ---------------------------------------------------------------------------

def _format_kpi_list(requests: list[Tier3KPIRequest]) -> str:
    """Format the KPI list for the human message — one numbered block per KPI."""
    lines: list[str] = []
    for i, req in enumerate(requests, 1):
        aliases_str = ", ".join(req.aliases) if req.aliases else "none"
        lines.append(
            f"{i}. KPI ID: {req.kpi_id}\n"
            f"   Name: {req.kpi_name}\n"
            f"   Aliases: {aliases_str}\n"
            f"   Definition: {req.definition}"
        )
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Per-section LLM call
# ---------------------------------------------------------------------------

def run_llm_extraction_for_section(
    section: Section,
    kpi_requests: list[Tier3KPIRequest],
    fiscal_year: str,
) -> list[Tier3ExtractionResult]:
    """Run one batched GPT-4o call to extract all requested KPIs from one section.

    Args:
        section: The section to extract from.
        kpi_requests: Every KPI that needs to be tried against this section.
        fiscal_year: Target fiscal year (e.g. "FY2025").

    Returns:
        One Tier3ExtractionResult per request (same order, matching by kpi_id).
        Falls back to all-not-found if the LLM call fails.
    """
    if not kpi_requests:
        return []

    context = build_section_context_for_prompt(section)
    kpi_list = _format_kpi_list(kpi_requests)

    llm = get_llm_client()
    chain = (
        TIER3_EXTRACTION_PROMPT
        | llm.with_structured_output(_Tier3BatchOutput).with_retry()
    )

    try:
        result: _Tier3BatchOutput = chain.invoke(
            {
                "fiscal_year": fiscal_year,
                "section_name": section.section_name_canonical,
                "section_context": context,
                "kpi_list": kpi_list,
            }
        )
        return result.extractions
    except Exception:
        log.exception(
            "Tier 3 LLM call failed for section=%s; treating all KPIs as not_found.",
            section.section_name_canonical,
        )
        return [
            Tier3ExtractionResult(kpi_id=req.kpi_id, found=False)
            for req in kpi_requests
        ]


# ---------------------------------------------------------------------------
# Tier 3 orchestration
# ---------------------------------------------------------------------------

def run_llm_extraction(
    ledger: ExtractionLedger,
    filtered_taxonomy: list[TaxonomyEntry],
    sections: list[Section],
    fiscal_year: str,
) -> ExtractionLedger:
    """Run Tier 3 LLM extraction for all still-unresolved KPIs.

    Batches BY SECTION (inverted from Tier 1/2): all not_found KPIs that share
    a canonical section are sent together in one LLM call per section.

    Args:
        ledger: ExtractionLedger after Tiers 1 and 2.
        filtered_taxonomy: Taxonomy entries applicable to this report.
        sections: All sections from Agent 2.
        fiscal_year: Target fiscal year.

    Returns:
        Updated ExtractionLedger.  Entries remain not_found if the LLM found
        nothing — no further tiers follow; this is the terminal extraction step.
    """
    # Index sections by canonical name
    sections_by_canonical: defaultdict[str, list[Section]] = defaultdict(list)
    for s in sections:
        sections_by_canonical[s.section_name_canonical].append(s)

    taxonomy_by_id = {e.kpi_id: e for e in filtered_taxonomy}

    # Collect still-unresolved entries
    not_found_entries = [
        taxonomy_by_id[kpi_id]
        for kpi_id, record in ledger.records.items()
        if record.status == "not_found" and kpi_id in taxonomy_by_id
    ]

    if not not_found_entries:
        return ledger

    # Build section_canonical → [Tier3KPIRequest] groups.
    # A KPI appears in every section group whose canonical name is in its
    # canonical_sections AND that has at least one Section object available.
    section_to_requests: defaultdict[str, list[Tier3KPIRequest]] = defaultdict(list)
    for entry in not_found_entries:
        for canonical_name in entry.canonical_sections:
            if canonical_name in sections_by_canonical:
                section_to_requests[canonical_name].append(
                    Tier3KPIRequest(
                        kpi_id=entry.kpi_id,
                        kpi_name=entry.kpi_name,
                        aliases=entry.aliases,
                        definition=entry.definition,
                    )
                )

    # Track which kpi_ids were tried (had at least one matching section).
    attempted_kpi_ids: set[str] = {
        req.kpi_id
        for reqs in section_to_requests.values()
        for req in reqs
    }

    # Run LLM per section, collect CandidateValues keyed by kpi_id.
    kpi_candidates: defaultdict[str, list[CandidateValue]] = defaultdict(list)

    # Track KPIs found during this Tier 3 run so they are removed from
    # subsequent sections' prompts — only truly unresolved KPIs are sent to
    # the LLM at each step.
    resolved_in_loop: set[str] = set()

    for canonical_name, requests in section_to_requests.items():
        # Exclude KPIs already resolved by a previous canonical section's call.
        active_requests = [r for r in requests if r.kpi_id not in resolved_in_loop]
        if not active_requests:
            log.debug(
                "All KPIs for section %s already resolved — skipping LLM call",
                canonical_name,
            )
            continue

        for section in sections_by_canonical[canonical_name]:
            # Re-filter inside the physical-section loop in case a sibling
            # section object (same canonical name) found something first.
            section_requests = [r for r in active_requests if r.kpi_id not in resolved_in_loop]
            if not section_requests:
                continue

            results = run_llm_extraction_for_section(section, section_requests, fiscal_year)
            results_by_kpi = {r.kpi_id: r for r in results}

            for req in section_requests:
                r = results_by_kpi.get(req.kpi_id)
                if r and r.found and r.value is not None:
                    kpi_candidates[req.kpi_id].append(
                        CandidateValue(
                            value=r.value,
                            section_name_canonical=canonical_name,
                            page=r.page,
                            source_element_type=r.source_element_type or "text",
                            footnotes=r.footnote_ids,
                            confidence=r.confidence,
                        )
                    )
                    resolved_in_loop.add(req.kpi_id)

    # Per-KPI resolution — same 0/1/>1-distinct-values branching as Tiers 1/2.
    for kpi_id, candidates in kpi_candidates.items():
        record = ledger.records[kpi_id]
        entry = taxonomy_by_id[kpi_id]

        if not candidates:
            # Attempted but LLM returned found=False everywhere.
            record.attempts.append(
                ExtractionAttempt(
                    tier="llm",
                    value=None,
                    confidence=0.0,
                    outcome="not_found",
                    note="LLM found nothing in any canonical section",
                )
            )
            continue

        distinct_values = {str(c.value) for c in candidates}

        if len(distinct_values) == 1:
            best = max(candidates, key=lambda c: c.confidence)

            if best.confidence < settings.LOW_CONFIDENCE_THRESHOLD:
                record.value = best.value
                record.section = best.section_name_canonical
                record.page = best.page
                record.method = "llm"
                record.source_element_type = best.source_element_type
                record.footnotes = list({fn for c in candidates for fn in c.footnotes})
                record.confidence = best.confidence
                record.status = "needs_human_review"
                record.review_reason = "low_confidence"
                record.attempts.append(
                    ExtractionAttempt(
                        tier="llm",
                        value=best.value,
                        confidence=best.confidence,
                        outcome="flagged",
                        note=f"low confidence: {best.confidence:.2f} < {settings.LOW_CONFIDENCE_THRESHOLD}",
                    )
                )
                log.info(
                    "Tier 3 low-confidence kpi_id=%s value=%s confidence=%.2f",
                    kpi_id, best.value, best.confidence,
                )
            else:
                record.value = best.value
                record.section = best.section_name_canonical
                record.page = best.page
                record.method = "llm"
                record.source_element_type = best.source_element_type
                record.footnotes = list({fn for c in candidates for fn in c.footnotes})
                record.confidence = best.confidence
                record.status = "found"
                record.attempts.append(
                    ExtractionAttempt(
                        tier="llm",
                        value=best.value,
                        confidence=best.confidence,
                        outcome="found",
                        note=f"LLM matched in {best.section_name_canonical}",
                    )
                )
                log.debug(
                    "Tier 3 found kpi_id=%s value=%s confidence=%.2f",
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
                "Tier 3 discrepancy for kpi_id=%s: values=%s → LLM chose %s",
                kpi_id, list(distinct_values), resolved.value,
            )

    # Record attempts for tried-but-not-found KPIs that had 0 candidates
    # (kpi_candidates key exists only when at least one result was found=True).
    for kpi_id in attempted_kpi_ids:
        record = ledger.records[kpi_id]
        if record.status == "not_found" and not any(
            a.tier == "llm" for a in record.attempts
        ):
            record.attempts.append(
                ExtractionAttempt(
                    tier="llm",
                    value=None,
                    confidence=0.0,
                    outcome="not_found",
                    note="LLM returned found=False in all canonical sections",
                )
            )

    return ledger
