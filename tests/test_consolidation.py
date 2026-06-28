"""Unit tests for consolidation/pipeline.py."""

import pytest

from common.schemas import (
    ConflictingValue,
    ExtractionAttempt,
    ExtractionLedger,
    ExtractionRecord,
)
from consolidation.pipeline import FinalReportOutput, run_consolidation


def _record(kpi_id, value=None, status="found", confidence=0.9, review_reason=None, method="llm"):
    r = ExtractionRecord(
        kpi_id=kpi_id,
        value=value,
        fiscal_year="FY2024",
        status=status,
        method=method,
        confidence=confidence,
    )
    r.review_reason = review_reason
    return r


def _ledger(*records):
    return ExtractionLedger(records={r.kpi_id: r for r in records})


# ---------------------------------------------------------------------------
# Basic partitioning
# ---------------------------------------------------------------------------

def test_found_record_goes_to_resolved():
    ledger = _ledger(_record("K1", value="500", status="found"))
    out = run_consolidation(ledger, "rpt1", "FY2024")
    assert len(out.resolved) == 1
    assert len(out.needs_review) == 0
    assert out.resolved[0].kpi_id == "K1"
    assert out.resolved[0].value == "500"


def test_needs_human_review_goes_to_needs_review():
    ledger = _ledger(
        _record("K1", value="300", status="needs_human_review", review_reason="low_confidence")
    )
    out = run_consolidation(ledger, "rpt1", "FY2024")
    assert len(out.needs_review) == 1
    assert out.needs_review[0].review_reason == "low_confidence"


def test_mixed_records_split_correctly():
    ledger = _ledger(
        _record("K1", value="100", status="found"),
        _record("K2", value="200", status="needs_human_review", review_reason="validation_failed"),
        _record("K3", value="300", status="found"),
    )
    out = run_consolidation(ledger, "rpt1", "FY2024")
    assert len(out.resolved) == 2
    assert len(out.needs_review) == 1
    assert out.needs_review[0].kpi_id == "K2"


def test_report_metadata_on_output():
    ledger = _ledger(_record("K1", value="100"))
    out = run_consolidation(ledger, "rpt42", "FY2023")
    assert out.report_id == "rpt42"
    assert out.fiscal_year == "FY2023"


# ---------------------------------------------------------------------------
# Defensive handling for unexpected statuses
# ---------------------------------------------------------------------------

def test_flagged_record_goes_to_needs_review_as_guard(caplog):
    ledger = _ledger(_record("K1", status="flagged"))
    out = run_consolidation(ledger, "rpt1", "FY2024")
    assert len(out.needs_review) == 1
    assert out.needs_review[0].kpi_id == "K1"
    assert "wiring guard" in caplog.text


def test_not_found_record_goes_to_needs_review_as_guard(caplog):
    ledger = _ledger(_record("K1", status="not_found"))
    out = run_consolidation(ledger, "rpt1", "FY2024")
    assert len(out.needs_review) == 1
    assert "wiring guard" in caplog.text


# ---------------------------------------------------------------------------
# Empty ledger
# ---------------------------------------------------------------------------

def test_empty_ledger():
    ledger = ExtractionLedger(records={})
    out = run_consolidation(ledger, "rpt1", "FY2024")
    assert isinstance(out, FinalReportOutput)
    assert out.resolved == []
    assert out.needs_review == []


# ---------------------------------------------------------------------------
# Conflicting values are preserved in needs_review
# ---------------------------------------------------------------------------

def test_conflicting_values_preserved():
    r = _record("K1", value="100", status="needs_human_review", review_reason="section_discrepancy")
    r.conflicting_values = [
        ConflictingValue(
            section="Balance Sheet",
            value="105",
            method="deterministic",
            source_element_type="table_cell",
        )
    ]
    ledger = _ledger(r)
    out = run_consolidation(ledger, "rpt1", "FY2024")
    assert len(out.needs_review[0].conflicting_values) == 1
    assert out.needs_review[0].conflicting_values[0].value == "105"
