"""Agent 8 — Retry Controller: retry execution.

Dispatches a single flagged/not_found KPI to the appropriate extraction tier
and updates the record.  The retry budget is enforced by the caller
(validation_retry_loop.py), not here.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from common.schemas import (
    CandidateValue,
    ExtractionAttempt,
    ExtractionLedger,
    ExtractionRecord,
    Section,
    TaxonomyEntry,
)
from llm_extractor.extraction import (
    Tier3KPIRequest,
    Tier3ExtractionResult,
    run_llm_extraction_retry_for_kpi,
)
from retry_controller.routing import determine_retry_tier

log = logging.getLogger(__name__)


def _last_failure_note(record: ExtractionRecord) -> str:
    """Extract the most recent failure note from record.attempts for the prompt."""
    for attempt in reversed(record.attempts):
        if attempt.note:
            return attempt.note
    return "Not resolved in prior extraction passes."


def _apply_tier3_result(
    record: ExtractionRecord,
    result: Tier3ExtractionResult,
    section_canonical: str,
    retry_label: str,
) -> ExtractionRecord:
    """Write a successful Tier 3 retry result back onto the record."""
    record.value = result.value
    record.section = section_canonical
    record.page = result.page
    record.method = "llm"
    record.source_element_type = result.source_element_type or "text"
    record.footnotes = result.footnote_ids
    record.confidence = result.confidence
    record.status = "found"
    record.review_reason = None
    record.attempts.append(
        ExtractionAttempt(
            tier="llm",
            value=result.value,
            confidence=result.confidence,
            outcome="found",
            note=f"Retry ({retry_label}) found value in {section_canonical}",
        )
    )
    return record


def retry_kpi(
    record: ExtractionRecord,
    taxonomy_entry: TaxonomyEntry,
    sections_by_canonical_name: dict[str, list[Section]],
    report_id: str,
    fiscal_year: str,
) -> ExtractionRecord:
    """Re-run extraction for one flagged or never-found KPI.

    Dispatches on determine_retry_tier() to pick the right extraction path,
    then updates the record in place and returns it.  If the retry still
    produces nothing, the record is left as "flagged" or "not_found" with
    an attempt appended explaining the retry was tried.

    Args:
        record: The ExtractionRecord to retry.
        taxonomy_entry: Taxonomy metadata for this KPI.
        sections_by_canonical_name: All available sections keyed by canonical name.
        report_id: Report identifier (used by semantic retrieval).
        fiscal_year: Target fiscal year.

    Returns:
        Updated ExtractionRecord.
    """
    tier = determine_retry_tier(record)
    kpi_id = taxonomy_entry.kpi_id
    log.info("Retry kpi_id=%s via %s", kpi_id, tier)

    kpi_request = Tier3KPIRequest(
        kpi_id=kpi_id,
        kpi_name=taxonomy_entry.kpi_name,
        aliases=taxonomy_entry.aliases,
        definition=taxonomy_entry.definition,
    )

    # ------------------------------------------------------------------
    # tier1_to_tier2: escalate from deterministic → semantic retrieval
    # ------------------------------------------------------------------
    if tier == "tier1_to_tier2":
        from semantic_retriever.config import settings as sem_settings
        from semantic_retriever.retrieval import extract_semantic_candidates
        from common.discrepancy_resolution import resolve_cross_section_discrepancy

        candidates: list[CandidateValue] = extract_semantic_candidates(
            taxonomy_entry=taxonomy_entry,
            report_id=report_id,
            fiscal_year=fiscal_year,
            top_k=sem_settings.SEMANTIC_TOP_K,
        )

        if not candidates:
            record.attempts.append(
                ExtractionAttempt(
                    tier="semantic",
                    value=None,
                    confidence=0.0,
                    outcome="not_found",
                    note="Retry (tier1_to_tier2): semantic retrieval found nothing.",
                )
            )
            record.status = "not_found"
            return record

        distinct = {str(c.value) for c in candidates}
        if len(distinct) == 1:
            best = max(candidates, key=lambda c: c.confidence)
            record.value = best.value
            record.section = best.section_name_canonical
            record.page = best.page
            record.method = "semantic"
            record.source_element_type = best.source_element_type
            record.footnotes = list({fn for c in candidates for fn in c.footnotes})
            record.confidence = best.confidence
            record.status = "found"
            record.review_reason = None
            record.attempts.append(
                ExtractionAttempt(
                    tier="semantic",
                    value=best.value,
                    confidence=best.confidence,
                    outcome="found",
                    note="Retry (tier1_to_tier2): semantic retrieval resolved.",
                )
            )
        else:
            sections_involved = [
                s for s in (
                    sec for secs in sections_by_canonical_name.values() for sec in secs
                )
                if s.section_name_canonical in {c.section_name_canonical for c in candidates}
            ]
            resolved = resolve_cross_section_discrepancy(
                taxonomy_entry=taxonomy_entry,
                candidates=candidates,
                sections_involved=sections_involved,
            )
            resolved.fiscal_year = fiscal_year
            # Merge attempts so the history is preserved
            resolved.attempts = record.attempts + resolved.attempts
            return resolved

        return record

    # ------------------------------------------------------------------
    # tier2_to_tier3 / tier3_recheck / tier3_broader — all use Tier 3
    # ------------------------------------------------------------------
    if tier == "tier2_to_tier3":
        validator_note = (
            "Semantic retrieval found a candidate but it failed validation — "
            "re-examine the section carefully for the correct value."
        )
    elif tier == "tier3_recheck":
        validator_note = _last_failure_note(record)
    else:  # tier3_broader
        validator_note = (
            "Not found in prior extraction passes — examine the full section "
            "thoroughly, including all tables, footnotes, and chart descriptions."
        )

    # Try all relevant sections in order; stop on first hit.
    found_result: Tier3ExtractionResult | None = None
    found_section: str | None = None

    for canonical_name in taxonomy_entry.canonical_sections:
        sections = sections_by_canonical_name.get(canonical_name, [])
        for section in sections:
            result = run_llm_extraction_retry_for_kpi(
                section=section,
                kpi_request=kpi_request,
                validator_note=validator_note,
            )
            if result.found and result.value is not None:
                found_result = result
                found_section = canonical_name
                break
        if found_result:
            break

    if found_result and found_section:
        return _apply_tier3_result(record, found_result, found_section, tier)

    # Retry produced nothing.
    record.attempts.append(
        ExtractionAttempt(
            tier="llm",
            value=None,
            confidence=0.0,
            outcome="not_found",
            note=f"Retry ({tier}): LLM still could not find value.",
        )
    )
    # Keep current status (flagged or not_found) — loop decides what to do next.
    return record


def run_retry_turn(
    ledger: ExtractionLedger,
    flagged_kpi_ids: list[str],
    taxonomy_by_id: dict[str, TaxonomyEntry],
    sections_by_canonical_name: dict[str, list[Section]],
    report_id: str,
    fiscal_year: str,
) -> ExtractionLedger:
    """Run one retry turn: re-attempt extraction for every currently-flagged KPI.

    Per the spec: all flagged KPIs are retried together in each turn.

    Args:
        ledger: Current ExtractionLedger.
        flagged_kpi_ids: KPI IDs to retry (from run_tally_checks).
        taxonomy_by_id: Full taxonomy keyed by kpi_id.
        sections_by_canonical_name: All report sections.
        report_id: Report identifier.
        fiscal_year: Target fiscal year.

    Returns:
        Updated ExtractionLedger.
    """
    for kpi_id in flagged_kpi_ids:
        entry = taxonomy_by_id.get(kpi_id)
        if entry is None:
            log.warning("retry_turn: no taxonomy entry for kpi_id=%s; skipping.", kpi_id)
            continue
        record = ledger.records.get(kpi_id)
        if record is None:
            log.warning("retry_turn: no ledger record for kpi_id=%s; skipping.", kpi_id)
            continue
        ledger.records[kpi_id] = retry_kpi(
            record=record,
            taxonomy_entry=entry,
            sections_by_canonical_name=sections_by_canonical_name,
            report_id=report_id,
            fiscal_year=fiscal_year,
        )

    return ledger
