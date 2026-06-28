"""Unit tests for validation_retry_loop.py.

All LLM/extraction calls are mocked — no real API calls or DB access.
"""

from unittest.mock import MagicMock, patch

import pytest

from common.schemas import (
    ExtractionAttempt,
    ExtractionLedger,
    ExtractionRecord,
    FootnoteElement,
    Section,
    TaxonomyEntry,
    ValidationRule,
)
from validation_retry_loop import ValidationRetryOutput, run_validation_retry_loop


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _entry(kpi_id="KPI_NET"):
    return TaxonomyEntry(
        kpi_id=kpi_id,
        kpi_name="Net Income",
        definition="Net income",
        canonical_sections=["Management Discussion and Analysis"],
        applicable_industries=["All"],
        applicable_report_types=["Annual Report"],
        applicable_accounting_standards=["IFRS"],
        aliases=["Net Income"],
    )


def _found_record(kpi_id, value, method="llm", confidence=0.9):
    return ExtractionRecord(
        kpi_id=kpi_id,
        value=value,
        fiscal_year="FY2024",
        status="found",
        method=method,
        confidence=confidence,
    )


def _not_found_record(kpi_id):
    return ExtractionRecord(
        kpi_id=kpi_id,
        fiscal_year="FY2024",
        status="not_found",
    )


def _make_ledger(**records):
    return ExtractionLedger(records=records)


TALLY_RULE = ValidationRule(
    rule_id="R_NET",
    description="net = rev - exp",
    rule_type="tally",
    formula="abs(KPI_NET - (KPI_REV - KPI_EXP)) <= tolerance",
    participating_kpi_ids=["KPI_NET", "KPI_REV", "KPI_EXP"],
    tolerance=0.01,
)

SECTIONS: dict[str, list[Section]] = {}
FOOTNOTES: dict[str, FootnoteElement] = {}
TAXONOMY_BY_ID = {
    "KPI_NET": _entry("KPI_NET"),
    "KPI_REV": _entry("KPI_REV"),
    "KPI_EXP": _entry("KPI_EXP"),
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_rules_skips_loop_and_runs_final_checks():
    ledger = _make_ledger(KPI_NET=_found_record("KPI_NET", "100"))
    out = run_validation_retry_loop(
        ledger=ledger,
        rules=[],
        taxonomy_by_id=TAXONOMY_BY_ID,
        sections_by_canonical_name=SECTIONS,
        footnotes_by_id=FOOTNOTES,
        report_id="rpt1",
        fiscal_year="FY2024",
        remaining_retry_budget=2,
        confidence_threshold=0.5,
        material_keywords=[],
    )
    assert isinstance(out, ValidationRetryOutput)
    assert out.turns_used == 0
    assert out.ledger.records["KPI_NET"].status == "found"


def test_all_pass_no_retry_turn_used():
    ledger = _make_ledger(
        KPI_NET=_found_record("KPI_NET", "100"),
        KPI_REV=_found_record("KPI_REV", "300"),
        KPI_EXP=_found_record("KPI_EXP", "200"),
    )
    out = run_validation_retry_loop(
        ledger=ledger,
        rules=[TALLY_RULE],
        taxonomy_by_id=TAXONOMY_BY_ID,
        sections_by_canonical_name=SECTIONS,
        footnotes_by_id=FOOTNOTES,
        report_id="rpt1",
        fiscal_year="FY2024",
        remaining_retry_budget=2,
        confidence_threshold=0.5,
        material_keywords=[],
    )
    assert out.turns_used == 0
    for rec in out.ledger.records.values():
        assert rec.status == "found"


def test_budget_zero_skips_retry_loop():
    """If budget=0, flagged KPIs go straight to validation_failed."""
    ledger = _make_ledger(
        KPI_NET=_found_record("KPI_NET", "50"),   # wrong: 50 != 300-200=100
        KPI_REV=_found_record("KPI_REV", "300"),
        KPI_EXP=_found_record("KPI_EXP", "200"),
    )
    out = run_validation_retry_loop(
        ledger=ledger,
        rules=[TALLY_RULE],
        taxonomy_by_id=TAXONOMY_BY_ID,
        sections_by_canonical_name=SECTIONS,
        footnotes_by_id=FOOTNOTES,
        report_id="rpt1",
        fiscal_year="FY2024",
        remaining_retry_budget=0,
        confidence_threshold=0.5,
        material_keywords=[],
    )
    assert out.turns_used == 0
    # All three participants should be validation_failed
    for kpi_id in ["KPI_NET", "KPI_REV", "KPI_EXP"]:
        assert out.ledger.records[kpi_id].status == "needs_human_review"
        assert out.ledger.records[kpi_id].review_reason == "validation_failed"


@patch("validation_retry_loop.run_retry_turn")
def test_one_retry_turn_resolves_all(mock_retry_turn):
    """Retry turn fixes the flagged KPIs → loop exits after 1 turn."""
    # Initial ledger: tally fails (50 != 300-200)
    ledger = _make_ledger(
        KPI_NET=_found_record("KPI_NET", "50"),
        KPI_REV=_found_record("KPI_REV", "300"),
        KPI_EXP=_found_record("KPI_EXP", "200"),
    )

    def fixed_turn(ledger, flagged_kpi_ids, **kwargs):
        # Simulate retry fixing the value
        ledger.records["KPI_NET"].value = "100"
        ledger.records["KPI_NET"].status = "found"
        ledger.records["KPI_REV"].status = "found"
        ledger.records["KPI_EXP"].status = "found"
        return ledger

    mock_retry_turn.side_effect = fixed_turn

    out = run_validation_retry_loop(
        ledger=ledger,
        rules=[TALLY_RULE],
        taxonomy_by_id=TAXONOMY_BY_ID,
        sections_by_canonical_name=SECTIONS,
        footnotes_by_id=FOOTNOTES,
        report_id="rpt1",
        fiscal_year="FY2024",
        remaining_retry_budget=2,
        confidence_threshold=0.5,
        material_keywords=[],
    )
    assert out.turns_used == 1
    mock_retry_turn.assert_called_once()
    assert out.ledger.records["KPI_NET"].status == "found"


@patch("validation_retry_loop.run_retry_turn")
def test_retry_budget_respected(mock_retry_turn):
    """Retry loop does not exceed remaining_retry_budget turns."""
    ledger = _make_ledger(
        KPI_NET=_found_record("KPI_NET", "50"),
        KPI_REV=_found_record("KPI_REV", "300"),
        KPI_EXP=_found_record("KPI_EXP", "200"),
    )

    # Retry doesn't fix anything
    mock_retry_turn.side_effect = lambda ledger, **kwargs: ledger

    out = run_validation_retry_loop(
        ledger=ledger,
        rules=[TALLY_RULE],
        taxonomy_by_id=TAXONOMY_BY_ID,
        sections_by_canonical_name=SECTIONS,
        footnotes_by_id=FOOTNOTES,
        report_id="rpt1",
        fiscal_year="FY2024",
        remaining_retry_budget=2,
        confidence_threshold=0.5,
        material_keywords=[],
    )
    assert out.turns_used == 2
    assert mock_retry_turn.call_count == 2


def test_not_found_records_become_not_found_after_retries():
    ledger = _make_ledger(
        KPI_NET=_not_found_record("KPI_NET"),
    )
    out = run_validation_retry_loop(
        ledger=ledger,
        rules=[],
        taxonomy_by_id=TAXONOMY_BY_ID,
        sections_by_canonical_name=SECTIONS,
        footnotes_by_id=FOOTNOTES,
        report_id="rpt1",
        fiscal_year="FY2024",
        remaining_retry_budget=2,
        confidence_threshold=0.5,
        material_keywords=[],
    )
    assert out.ledger.records["KPI_NET"].status == "needs_human_review"
    assert out.ledger.records["KPI_NET"].review_reason == "not_found_after_retries"


def test_low_confidence_passthrough_applied_after_loop():
    ledger = _make_ledger(
        KPI_NET=_found_record("KPI_NET", "100", confidence=0.2),
    )
    out = run_validation_retry_loop(
        ledger=ledger,
        rules=[],
        taxonomy_by_id=TAXONOMY_BY_ID,
        sections_by_canonical_name=SECTIONS,
        footnotes_by_id=FOOTNOTES,
        report_id="rpt1",
        fiscal_year="FY2024",
        remaining_retry_budget=2,
        confidence_threshold=0.5,
        material_keywords=[],
    )
    assert out.ledger.records["KPI_NET"].status == "needs_human_review"
    assert out.ledger.records["KPI_NET"].review_reason == "low_confidence"
