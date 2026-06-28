"""Agent 9 — Consolidation.

Partitions the final ExtractionLedger into:
  - resolved: records with status=="found" → ResolvedKPIOutput
  - needs_review: records with status=="needs_human_review" → NeedsReviewKPIOutput

Also writes the final XML report to data/output/report/ when report_metadata
and output_dir are supplied.

Defensive handling: records still "flagged" or "not_found" at this point
indicate a wiring bug upstream (run_validation_retry_loop wasn't called to
completion).  Log a warning and place them in needs_review rather than
dropping them silently.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from common.schemas import ConflictingValue, ExtractionAttempt, ExtractionLedger, ReportMetadata
from common.token_tracker import TokenUsageTracker

log = logging.getLogger(__name__)


class ResolvedKPIOutput(BaseModel):
    kpi_id: str
    kpi_name: Optional[str] = None
    value: Optional[str] = None
    section: Optional[str] = None
    page: Optional[int] = None
    method: Optional[str] = None
    source_element_type: Optional[str] = None
    confidence: float
    footnotes: list[str] = []
    alias_used: Optional[str] = None


class NeedsReviewKPIOutput(BaseModel):
    kpi_id: str
    kpi_name: Optional[str] = None
    value: Optional[str] = None
    section: Optional[str] = None
    page: Optional[int] = None
    method: Optional[str] = None
    source_element_type: Optional[str] = None
    confidence: float
    review_reason: Optional[str] = None
    footnotes: list[str] = []
    conflicting_values: list[ConflictingValue] = []
    attempts: list[ExtractionAttempt] = []
    alias_used: Optional[str] = None


class FinalReportOutput(BaseModel):
    report_id: str
    fiscal_year: str
    resolved: list[ResolvedKPIOutput] = []
    needs_review: list[NeedsReviewKPIOutput] = []


def run_consolidation(
    ledger: ExtractionLedger,
    report_id: str,
    fiscal_year: str,
    report_metadata: Optional[ReportMetadata] = None,
    token_tracker: Optional[TokenUsageTracker] = None,
    output_dir: Optional[str] = None,
) -> FinalReportOutput:
    """Partition the final ledger into resolved and needs-review buckets.

    Args:
        ledger: ExtractionLedger after run_validation_retry_loop().
        report_id: Report identifier.
        fiscal_year: Target fiscal year (stamped onto the output).

    Returns:
        FinalReportOutput with resolved and needs_review lists.
    """
    resolved: list[ResolvedKPIOutput] = []
    needs_review: list[NeedsReviewKPIOutput] = []

    for kpi_id, record in ledger.records.items():
        value_str = str(record.value) if record.value is not None else None

        if record.status == "found":
            resolved.append(
                ResolvedKPIOutput(
                    kpi_id=kpi_id,
                    kpi_name=record.kpi_name,
                    value=value_str,
                    section=record.section,
                    page=record.page,
                    method=record.method,
                    source_element_type=record.source_element_type,
                    confidence=record.confidence,
                    footnotes=record.footnotes,
                    alias_used=record.alias_used,
                )
            )

        elif record.status == "needs_human_review":
            needs_review.append(
                NeedsReviewKPIOutput(
                    kpi_id=kpi_id,
                    kpi_name=record.kpi_name,
                    value=value_str,
                    section=record.section,
                    page=record.page,
                    method=record.method,
                    source_element_type=record.source_element_type,
                    confidence=record.confidence,
                    review_reason=record.review_reason,
                    footnotes=record.footnotes,
                    conflicting_values=record.conflicting_values,
                    attempts=record.attempts,
                    alias_used=record.alias_used,
                )
            )

        else:
            # Defensive: flagged or not_found — upstream wiring bug.
            log.warning(
                "run_consolidation: kpi_id=%s has unexpected status=%s — "
                "placing in needs_review as wiring guard.",
                kpi_id, record.status,
            )
            needs_review.append(
                NeedsReviewKPIOutput(
                    kpi_id=kpi_id,
                    kpi_name=record.kpi_name,
                    value=value_str,
                    section=record.section,
                    page=record.page,
                    method=record.method,
                    source_element_type=record.source_element_type,
                    confidence=record.confidence,
                    review_reason=record.review_reason or record.status,
                    footnotes=record.footnotes,
                    conflicting_values=record.conflicting_values,
                    attempts=record.attempts,
                    alias_used=record.alias_used,
                )
            )

    log.info(
        "Consolidation complete for report_id=%s: resolved=%d needs_review=%d",
        report_id, len(resolved), len(needs_review),
    )

    final = FinalReportOutput(
        report_id=report_id,
        fiscal_year=fiscal_year,
        resolved=resolved,
        needs_review=needs_review,
    )

    # Write XML report when caller supplies the necessary context.
    if report_metadata is not None and output_dir is not None:
        from consolidation.xml_writer import write_consolidation_xml
        xml_path = write_consolidation_xml(
            final_output=final,
            report_metadata=report_metadata,
            token_tracker=token_tracker,
            output_dir=output_dir,
        )
        log.info("Final XML report written → %s", xml_path)

    return final
