"""Unit tests for validator/tally.py — RuleEvaluationResult, evaluate_rule, run_tally_checks."""

import pytest

from common.schemas import ExtractionAttempt, ExtractionLedger, ExtractionRecord, ValidationRule
from validator.tally import evaluate_rule, run_tally_checks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ledger(**kpi_values) -> ExtractionLedger:
    """Build a ledger from kpi_id → (value, status) tuples or plain value (status=found)."""
    records = {}
    for kpi_id, val in kpi_values.items():
        if isinstance(val, tuple):
            value, status = val
        else:
            value, status = val, "found"
        records[kpi_id] = ExtractionRecord(
            kpi_id=kpi_id,
            value=value,
            fiscal_year="FY2024",
            status=status,
            confidence=0.9,
            method="llm",
        )
    return ExtractionLedger(records=records)


def _tally_rule(rule_id="R1", formula="abs(KPI_NET - (KPI_REV - KPI_EXP)) <= tolerance",
                participants=None, tolerance=0.01) -> ValidationRule:
    return ValidationRule(
        rule_id=rule_id,
        description="test tally rule",
        rule_type="tally",
        formula=formula,
        participating_kpi_ids=participants or ["KPI_NET", "KPI_REV", "KPI_EXP"],
        tolerance=tolerance,
    )


# ---------------------------------------------------------------------------
# evaluate_rule — balanced (passes)
# ---------------------------------------------------------------------------

def test_evaluate_rule_balanced_passes():
    ledger = _make_ledger(KPI_NET=100.0, KPI_REV=300.0, KPI_EXP=200.0)
    rule = _tally_rule()
    result = evaluate_rule(rule, ledger)
    assert not result.skipped
    assert result.passed is True


def test_evaluate_rule_unbalanced_fails():
    # net=100, rev=300, exp=150 → 100 != 300-150=150
    ledger = _make_ledger(KPI_NET=100.0, KPI_REV=300.0, KPI_EXP=150.0)
    rule = _tally_rule()
    result = evaluate_rule(rule, ledger)
    assert not result.skipped
    assert result.passed is False


# ---------------------------------------------------------------------------
# evaluate_rule — skipped paths
# ---------------------------------------------------------------------------

def test_evaluate_rule_skipped_missing_kpi():
    ledger = _make_ledger(KPI_NET=100.0, KPI_REV=300.0)
    # KPI_EXP is absent from ledger
    rule = _tally_rule()
    result = evaluate_rule(rule, ledger)
    assert result.skipped
    assert result.passed is None


def test_evaluate_rule_skipped_not_found_status():
    ledger = _make_ledger(KPI_NET=100.0, KPI_REV=300.0, KPI_EXP=(None, "not_found"))
    rule = _tally_rule()
    result = evaluate_rule(rule, ledger)
    assert result.skipped


def test_evaluate_rule_skipped_non_numeric_value():
    ledger = _make_ledger(KPI_NET=100.0, KPI_REV=300.0, KPI_EXP=("N/A", "found"))
    rule = _tally_rule()
    result = evaluate_rule(rule, ledger)
    assert result.skipped


def test_evaluate_rule_numeric_string_with_commas():
    # "1,000" should parse to 1000.0
    ledger = _make_ledger(KPI_NET="100.0", KPI_REV="300.0", KPI_EXP="200.0")
    rule = _tally_rule()
    result = evaluate_rule(rule, ledger)
    assert not result.skipped
    assert result.passed is True


# ---------------------------------------------------------------------------
# evaluate_rule — plausibility bound
# ---------------------------------------------------------------------------

def test_evaluate_rule_bound_passes():
    ledger = _make_ledger(KPI_MARGIN=25.0)
    rule = ValidationRule(
        rule_id="BOUND",
        description="margin 0-100",
        rule_type="plausibility_bound",
        formula="0 <= KPI_MARGIN and KPI_MARGIN <= 100",
        participating_kpi_ids=["KPI_MARGIN"],
        tolerance=0.0,
    )
    result = evaluate_rule(rule, ledger)
    assert not result.skipped
    assert result.passed is True


def test_evaluate_rule_bound_fails():
    ledger = _make_ledger(KPI_MARGIN=150.0)
    rule = ValidationRule(
        rule_id="BOUND",
        description="margin 0-100",
        rule_type="plausibility_bound",
        formula="0 <= KPI_MARGIN and KPI_MARGIN <= 100",
        participating_kpi_ids=["KPI_MARGIN"],
        tolerance=0.0,
    )
    result = evaluate_rule(rule, ledger)
    assert not result.skipped
    assert result.passed is False


# ---------------------------------------------------------------------------
# run_tally_checks
# ---------------------------------------------------------------------------

def test_run_tally_checks_no_failures():
    ledger = _make_ledger(KPI_NET=100.0, KPI_REV=300.0, KPI_EXP=200.0)
    rule = _tally_rule()
    ledger_out, flagged = run_tally_checks(ledger, [rule])
    assert flagged == []
    # Status unchanged
    assert ledger_out.records["KPI_NET"].status == "found"


def test_run_tally_checks_flags_participants():
    ledger = _make_ledger(KPI_NET=50.0, KPI_REV=300.0, KPI_EXP=200.0)
    rule = _tally_rule()
    ledger_out, flagged = run_tally_checks(ledger, [rule])
    assert set(flagged) == {"KPI_NET", "KPI_REV", "KPI_EXP"}
    for kpi_id in ["KPI_NET", "KPI_REV", "KPI_EXP"]:
        assert ledger_out.records[kpi_id].status == "flagged"


def test_run_tally_checks_skips_terminal_records():
    ledger = _make_ledger(KPI_NET=50.0, KPI_REV=300.0, KPI_EXP=200.0)
    # Mark KPI_NET as terminal section_discrepancy
    ledger.records["KPI_NET"].status = "needs_human_review"
    ledger.records["KPI_NET"].review_reason = "section_discrepancy"
    rule = _tally_rule()
    ledger_out, flagged = run_tally_checks(ledger, [rule])
    # KPI_NET must NOT appear in flagged (it's terminal)
    assert "KPI_NET" not in flagged
    # But KPI_REV and KPI_EXP are still flagged
    assert "KPI_REV" in flagged
    assert "KPI_EXP" in flagged
    # KPI_NET status unchanged
    assert ledger_out.records["KPI_NET"].status == "needs_human_review"
    assert ledger_out.records["KPI_NET"].review_reason == "section_discrepancy"
    # A note was still appended to KPI_NET
    assert any("terminal" in a.note for a in ledger_out.records["KPI_NET"].attempts)


def test_run_tally_checks_empty_rules():
    ledger = _make_ledger(KPI_NET=100.0)
    ledger_out, flagged = run_tally_checks(ledger, [])
    assert flagged == []


def test_run_tally_checks_skipped_rule_does_not_flag():
    ledger = _make_ledger(KPI_NET=100.0, KPI_REV=300.0)
    # KPI_EXP missing — rule should be skipped, not failed
    rule = _tally_rule()
    ledger_out, flagged = run_tally_checks(ledger, [rule])
    assert flagged == []
