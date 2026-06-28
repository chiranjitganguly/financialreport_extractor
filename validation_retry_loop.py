"""Validator ⇄ Retry Controller loop — top-level orchestration for Agents 7 and 8.

The loop structure:
  1. run_tally_checks (Agent 7) — if any KPI fails a rule, flag it
  2. If flagged KPIs remain and budget allows, run_retry_turn (Agent 8)
  3. Repeat until no flags or budget exhausted
  4. Terminal cleanup: flagged → validation_failed, not_found → not_found_after_retries
  5. Final single-pass checks (low-confidence passthrough, footnote materiality)

The retry budget is SHARED with Agent 2's section-alignment retries — it is not
a fresh counter.  The caller (Orchestrator, when built) threads in however many
turns remain after Agent 2 consumed some.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from common.schemas import (
    ExtractionAttempt,
    ExtractionLedger,
    FootnoteElement,
    Section,
    TaxonomyEntry,
    ValidationRule,
)
from validator.tally import run_tally_checks
from validator.final_review import run_footnote_materiality_check, run_low_confidence_passthrough
from retry_controller.retry import run_retry_turn

log = logging.getLogger(__name__)


class ValidationRetryOutput(BaseModel):
    ledger: ExtractionLedger
    turns_used: int


def run_validation_retry_loop(
    ledger: ExtractionLedger,
    rules: list[ValidationRule],
    taxonomy_by_id: dict[str, TaxonomyEntry],
    sections_by_canonical_name: dict[str, list[Section]],
    footnotes_by_id: dict[str, FootnoteElement],
    report_id: str,
    fiscal_year: str,
    remaining_retry_budget: int,
    confidence_threshold: float,
    material_keywords: list[str],
) -> ValidationRetryOutput:
    """Run the Validator ⇄ Retry Controller loop, then final review passes.

    Args:
        ledger: ExtractionLedger from run_extraction_pipeline().
        rules: Loaded ValidationRules (may be empty if no rules configured).
        taxonomy_by_id: Full taxonomy keyed by kpi_id.
        sections_by_canonical_name: All report sections keyed by canonical name.
        footnotes_by_id: Report footnotes keyed by footnote marker.
        report_id: Report identifier (threaded through to retry_kpi).
        fiscal_year: Target fiscal year.
        remaining_retry_budget: Turns left in the SHARED budget after Agent 2
            already consumed some (typically 0, 1, or 2).
        confidence_threshold: Records with confidence < this are flagged after
            the loop (from common.config.EXTRACTION_CONFIDENCE_THRESHOLD).
        material_keywords: Keywords triggering footnote_caveat review.

    Returns:
        ValidationRetryOutput with the final ledger and turns_used count.
    """
    turns_used = 0

    # ------------------------------------------------------------------
    # Tally check + retry loop
    # ------------------------------------------------------------------
    while True:
        ledger, flagged_kpi_ids = run_tally_checks(ledger, rules)

        if not flagged_kpi_ids:
            log.info(
                "Validation loop: no tally failures — loop complete after %d turn(s).",
                turns_used,
            )
            break

        if turns_used >= remaining_retry_budget:
            log.info(
                "Validation loop: %d KPI(s) still flagged but retry budget exhausted "
                "(%d/%d turns used).",
                len(flagged_kpi_ids), turns_used, remaining_retry_budget,
            )
            break

        log.info(
            "Validation loop turn %d: retrying %d flagged KPI(s): %s",
            turns_used + 1, len(flagged_kpi_ids), flagged_kpi_ids,
        )
        ledger = run_retry_turn(
            ledger=ledger,
            flagged_kpi_ids=flagged_kpi_ids,
            taxonomy_by_id=taxonomy_by_id,
            sections_by_canonical_name=sections_by_canonical_name,
            report_id=report_id,
            fiscal_year=fiscal_year,
        )
        turns_used += 1

    # ------------------------------------------------------------------
    # Terminal cleanup — anything still flagged or not_found after the loop
    # ------------------------------------------------------------------
    for kpi_id, record in ledger.records.items():
        if record.status == "flagged":
            record.status = "needs_human_review"
            record.review_reason = "validation_failed"
            record.attempts.append(
                ExtractionAttempt(
                    tier=record.method or "llm",
                    value=record.value,
                    confidence=record.confidence,
                    outcome="flagged",
                    note="Retry budget exhausted with tally rule still failing.",
                )
            )
            log.info(
                "kpi_id=%s: still flagged after retry loop → validation_failed.",
                kpi_id,
            )
        elif record.status == "not_found":
            record.status = "needs_human_review"
            record.review_reason = "not_found_after_retries"
            record.attempts.append(
                ExtractionAttempt(
                    tier="llm",
                    value=None,
                    confidence=0.0,
                    outcome="not_found",
                    note="Not found after all extraction passes and retries.",
                )
            )
            log.debug("kpi_id=%s: not_found → not_found_after_retries.", kpi_id)

    # ------------------------------------------------------------------
    # Final single-pass checks — only on genuinely "found" records
    # ------------------------------------------------------------------
    ledger = run_low_confidence_passthrough(ledger, confidence_threshold)
    ledger = run_footnote_materiality_check(ledger, footnotes_by_id, material_keywords)

    return ValidationRetryOutput(ledger=ledger, turns_used=turns_used)
