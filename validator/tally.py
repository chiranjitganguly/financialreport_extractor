"""Agent 7 — Validator: tally checks.

Runs on every retry turn (unlike final_review.py which runs once after the loop
converges).  A tally rule fails when participating KPI values can be fetched but
the formula evaluates to False.  Missing or non-numeric participants cause the
rule to be skipped rather than failed — the KPI itself will surface as
not_found_after_retries separately.
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel
from simpleeval import simple_eval, InvalidExpression

from common.schemas import ExtractionAttempt, ExtractionLedger, ValidationRule

log = logging.getLogger(__name__)

# Functions exposed to formula evaluation — only safe arithmetic/comparison ops.
_SAFE_FUNCTIONS = {"abs": abs, "min": min, "max": max, "round": round}

# Terminal review reasons — records with these must NEVER re-enter the retry loop.
_TERMINAL_REVIEW_REASONS = {"section_discrepancy", "footnoted_caveat"}


class RuleEvaluationResult(BaseModel):
    rule_id: str
    skipped: bool
    passed: Optional[bool] = None
    participating_kpi_ids: list[str]


def _parse_numeric(value) -> Optional[float]:
    """Try to coerce an ExtractionRecord value to float for formula evaluation."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("%", "").strip()
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return None
    return None


def evaluate_rule(rule: ValidationRule, ledger: ExtractionLedger) -> RuleEvaluationResult:
    """Evaluate a single validation rule against the current ledger state.

    Skips (does not fail) if any participant is not_found or non-numeric —
    a missing input is a separate not_found problem, not a rule failure.

    Args:
        rule: The ValidationRule to evaluate.
        ledger: Current ExtractionLedger.

    Returns:
        RuleEvaluationResult with skipped=True if any participant is absent/
        non-numeric, otherwise passed=True/False per the formula result.
    """
    names: dict[str, float] = {}

    for kpi_id in rule.participating_kpi_ids:
        record = ledger.records.get(kpi_id)
        if record is None or record.status == "not_found":
            return RuleEvaluationResult(
                rule_id=rule.rule_id,
                skipped=True,
                passed=None,
                participating_kpi_ids=rule.participating_kpi_ids,
            )
        numeric = _parse_numeric(record.value)
        if numeric is None:
            return RuleEvaluationResult(
                rule_id=rule.rule_id,
                skipped=True,
                passed=None,
                participating_kpi_ids=rule.participating_kpi_ids,
            )
        names[kpi_id] = numeric

    names["tolerance"] = rule.tolerance

    try:
        result = simple_eval(rule.formula, names=names, functions=_SAFE_FUNCTIONS)
        passed = bool(result)
    except (InvalidExpression, Exception) as exc:
        log.warning(
            "Rule %s formula evaluation failed (%s); treating as skipped.",
            rule.rule_id, exc,
        )
        return RuleEvaluationResult(
            rule_id=rule.rule_id,
            skipped=True,
            passed=None,
            participating_kpi_ids=rule.participating_kpi_ids,
        )

    return RuleEvaluationResult(
        rule_id=rule.rule_id,
        skipped=False,
        passed=passed,
        participating_kpi_ids=rule.participating_kpi_ids,
    )


def run_tally_checks(
    ledger: ExtractionLedger,
    rules: list[ValidationRule],
) -> tuple[ExtractionLedger, list[str]]:
    """Run all validation rules against the ledger and flag failing KPIs.

    Terminal records (section_discrepancy, footnoted_caveat) have the rule
    failure noted in their attempts history but their status is NOT changed —
    they must not re-enter the retry loop.

    Args:
        ledger: Current ExtractionLedger.
        rules: All ValidationRules to check.

    Returns:
        (updated ledger, list[str] of kpi_ids actually flagged this call —
        excludes terminal records that were noted but not re-flagged).
    """
    newly_flagged: list[str] = []

    for rule in rules:
        eval_result = evaluate_rule(rule, ledger)

        if eval_result.skipped or eval_result.passed:
            continue

        # Rule failed — process each participating KPI.
        for kpi_id in rule.participating_kpi_ids:
            record = ledger.records.get(kpi_id)
            if record is None:
                continue

            failure_note = (
                f"Tally rule '{rule.rule_id}' failed: {rule.description} "
                f"(formula: {rule.formula})"
            )

            if (
                record.status == "needs_human_review"
                and record.review_reason in _TERMINAL_REVIEW_REASONS
            ):
                # Terminal — note but don't re-flag.
                record.attempts.append(
                    ExtractionAttempt(
                        tier=record.method or "llm",
                        value=record.value,
                        confidence=record.confidence,
                        outcome="flagged",
                        note=f"[terminal, not re-queued] {failure_note}",
                    )
                )
                log.debug(
                    "Tally rule %s failed for kpi_id=%s but record is terminal (%s); noted only.",
                    rule.rule_id, kpi_id, record.review_reason,
                )
            else:
                record.status = "flagged"
                record.attempts.append(
                    ExtractionAttempt(
                        tier=record.method or "llm",
                        value=record.value,
                        confidence=record.confidence,
                        outcome="flagged",
                        note=failure_note,
                    )
                )
                if kpi_id not in newly_flagged:
                    newly_flagged.append(kpi_id)
                log.info(
                    "Tally rule %s failed for kpi_id=%s; flagged for retry.",
                    rule.rule_id, kpi_id,
                )

    return ledger, newly_flagged
