"""Unit tests for validator/final_review.py."""

import pytest

from common.schemas import ExtractionLedger, ExtractionRecord, FootnoteElement
from validator.final_review import (
    classify_footnote_materiality,
    run_footnote_materiality_check,
    run_low_confidence_passthrough,
)

_THRESHOLD = 0.5
_KEYWORDS = ["adjusted", "restated", "excludes", "non-gaap", "pro forma"]


def _record(kpi_id="K1", status="found", confidence=0.9, footnotes=None, method="llm"):
    return ExtractionRecord(
        kpi_id=kpi_id,
        value="100",
        fiscal_year="FY2024",
        status=status,
        confidence=confidence,
        method=method,
        footnotes=footnotes or [],
    )


def _ledger(*records):
    return ExtractionLedger(records={r.kpi_id: r for r in records})


# ---------------------------------------------------------------------------
# classify_footnote_materiality
# ---------------------------------------------------------------------------

def test_classify_material_keyword_match():
    assert classify_footnote_materiality(["Revenue is adjusted for one-time items"], _KEYWORDS)


def test_classify_case_insensitive():
    assert classify_footnote_materiality(["Values are RESTATED for the prior period"], _KEYWORDS)


def test_classify_no_match():
    assert not classify_footnote_materiality(["See page 12 for details"], _KEYWORDS)


def test_classify_empty_footnotes():
    assert not classify_footnote_materiality([], _KEYWORDS)


def test_classify_multi_footnote_any_match():
    texts = ["Normal disclosure", "Excludes discontinued operations"]
    assert classify_footnote_materiality(texts, _KEYWORDS)


# ---------------------------------------------------------------------------
# run_low_confidence_passthrough
# ---------------------------------------------------------------------------

def test_low_confidence_flags_found_below_threshold():
    r = _record(confidence=0.3)
    ledger = _ledger(r)
    out = run_low_confidence_passthrough(ledger, _THRESHOLD)
    assert out.records["K1"].status == "needs_human_review"
    assert out.records["K1"].review_reason == "low_confidence"


def test_low_confidence_passes_at_threshold():
    r = _record(confidence=_THRESHOLD)
    ledger = _ledger(r)
    out = run_low_confidence_passthrough(ledger, _THRESHOLD)
    assert out.records["K1"].status == "found"


def test_low_confidence_passes_above_threshold():
    r = _record(confidence=0.9)
    ledger = _ledger(r)
    out = run_low_confidence_passthrough(ledger, _THRESHOLD)
    assert out.records["K1"].status == "found"


def test_low_confidence_does_not_touch_already_flagged():
    r = _record(confidence=0.1, status="needs_human_review")
    r.review_reason = "section_discrepancy"
    ledger = _ledger(r)
    out = run_low_confidence_passthrough(ledger, _THRESHOLD)
    # status stays needs_human_review but review_reason unchanged
    assert out.records["K1"].review_reason == "section_discrepancy"


# ---------------------------------------------------------------------------
# run_footnote_materiality_check
# ---------------------------------------------------------------------------

def _footnotes_by_id(*pairs):
    from common.schemas import FootnoteAnchor
    return {
        marker: FootnoteElement(
            footnote_id=marker,
            marker=marker,
            text=text,
            section_name_canonical="Management Discussion and Analysis",
            page=1,
            anchors=[],
        )
        for marker, text in pairs
    }


def test_footnote_material_flags_found_record():
    r = _record(footnotes=["1"])
    ledger = _ledger(r)
    fns = _footnotes_by_id(("1", "Values are adjusted for exceptional items"))
    out = run_footnote_materiality_check(ledger, fns, _KEYWORDS)
    assert out.records["K1"].status == "needs_human_review"
    assert out.records["K1"].review_reason == "footnoted_caveat"


def test_footnote_non_material_does_not_flag():
    r = _record(footnotes=["2"])
    ledger = _ledger(r)
    fns = _footnotes_by_id(("2", "See Note 3 for further details"))
    out = run_footnote_materiality_check(ledger, fns, _KEYWORDS)
    assert out.records["K1"].status == "found"


def test_footnote_missing_from_map_no_flag():
    r = _record(footnotes=["99"])
    ledger = _ledger(r)
    out = run_footnote_materiality_check(ledger, {}, _KEYWORDS)
    assert out.records["K1"].status == "found"


def test_footnote_check_does_not_touch_already_review():
    r = _record(footnotes=["1"], status="needs_human_review", confidence=0.3)
    r.review_reason = "low_confidence"
    ledger = _ledger(r)
    fns = _footnotes_by_id(("1", "pro forma basis"))
    out = run_footnote_materiality_check(ledger, fns, _KEYWORDS)
    # Already needs_human_review — review_reason unchanged (low_confidence, not footnoted_caveat)
    assert out.records["K1"].review_reason == "low_confidence"


def test_footnote_no_footnotes_skipped():
    r = _record(footnotes=[])
    ledger = _ledger(r)
    out = run_footnote_materiality_check(ledger, {}, _KEYWORDS)
    assert out.records["K1"].status == "found"
