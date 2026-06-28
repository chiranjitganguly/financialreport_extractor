"""Tests for confidence.py — threshold routing and HITL hand-off logic."""
import pytest

from report_ingestion.confidence import route_confidence
from report_ingestion.schemas import (
    AccountingStandardResult,
    IndustryResult,
    LanguageResult,
    ReportTypeResult,
)

THRESHOLD = 0.25


def _rt(confidence: float) -> ReportTypeResult:
    return ReportTypeResult(
        report_type="annual_report", confidence=confidence, source="document_marker"
    )


def _lang(confidence: float) -> LanguageResult:
    return LanguageResult(language="en", confidence=confidence)


def _std(confidence: float) -> AccountingStandardResult:
    return AccountingStandardResult(
        standard="IFRS", confidence=confidence, source="document_statement"
    )


def _ind(confidence: float) -> IndustryResult:
    return IndustryResult(
        industry="Technology", confidence=confidence, source="company_map"
    )


# ---------------------------------------------------------------------------
# Happy path — all fields above threshold
# ---------------------------------------------------------------------------

class TestAllFieldsPass:
    def test_returns_report_metadata(
        self, passing_report_type, passing_language,
        passing_accounting_standard, passing_industry
    ):
        metadata, flagged = route_confidence(
            passing_report_type, passing_language,
            passing_accounting_standard, passing_industry,
            country="United Kingdom", threshold=THRESHOLD,
        )
        assert metadata is not None
        assert flagged == []

    def test_metadata_fields_populated_correctly(
        self, passing_report_type, passing_language,
        passing_accounting_standard, passing_industry
    ):
        metadata, _ = route_confidence(
            passing_report_type, passing_language,
            passing_accounting_standard, passing_industry,
            country="India", threshold=THRESHOLD,
        )
        assert metadata.report_type == "annual_report"
        assert metadata.language == "en"
        assert metadata.accounting_standard == "IFRS"
        assert metadata.industry == "Telecommunications"
        assert metadata.country == "India"

    def test_country_none_propagates_to_metadata(
        self, passing_report_type, passing_language,
        passing_accounting_standard, passing_industry
    ):
        metadata, _ = route_confidence(
            passing_report_type, passing_language,
            passing_accounting_standard, passing_industry,
            country=None, threshold=THRESHOLD,
        )
        assert metadata.country is None

    def test_report_id_is_empty_placeholder(
        self, passing_report_type, passing_language,
        passing_accounting_standard, passing_industry
    ):
        metadata, _ = route_confidence(
            passing_report_type, passing_language,
            passing_accounting_standard, passing_industry,
            country=None, threshold=THRESHOLD,
        )
        assert metadata.report_id == ""


# ---------------------------------------------------------------------------
# Threshold boundary
# ---------------------------------------------------------------------------

class TestThresholdBoundary:
    def test_confidence_exactly_at_threshold_passes(self):
        metadata, flagged = route_confidence(
            _rt(THRESHOLD), _lang(THRESHOLD),
            _std(THRESHOLD), _ind(THRESHOLD),
            country=None, threshold=THRESHOLD,
        )
        assert metadata is not None
        assert flagged == []

    def test_confidence_just_below_threshold_fails(self):
        below = THRESHOLD - 0.0001
        metadata, flagged = route_confidence(
            _rt(below), _lang(1.0),
            _std(1.0), _ind(1.0),
            country=None, threshold=THRESHOLD,
        )
        assert metadata is None
        assert len(flagged) == 1
        assert flagged[0].field_name == "report_type"

    def test_zero_confidence_fails(self):
        metadata, flagged = route_confidence(
            _rt(0.0), _lang(1.0),
            _std(1.0), _ind(1.0),
            country=None, threshold=THRESHOLD,
        )
        assert metadata is None
        assert len(flagged) == 1


# ---------------------------------------------------------------------------
# Multiple fields failing
# ---------------------------------------------------------------------------

class TestMultipleFieldsFail:
    def test_all_fields_failing_all_flagged(self):
        _, flagged = route_confidence(
            _rt(0.0), _lang(0.0),
            _std(0.0), _ind(0.0),
            country=None, threshold=THRESHOLD,
        )
        assert len(flagged) == 4

    def test_two_fields_failing_both_flagged(self):
        _, flagged = route_confidence(
            _rt(0.0), _lang(1.0),
            _std(0.0), _ind(1.0),
            country=None, threshold=THRESHOLD,
        )
        assert len(flagged) == 2
        flagged_names = {f.field_name for f in flagged}
        assert flagged_names == {"report_type", "accounting_standard"}

    def test_metadata_is_none_when_any_field_fails(self):
        metadata, _ = route_confidence(
            _rt(0.0), _lang(1.0),
            _std(1.0), _ind(1.0),
            country=None, threshold=THRESHOLD,
        )
        assert metadata is None


# ---------------------------------------------------------------------------
# FieldReview content
# ---------------------------------------------------------------------------

class TestFieldReviewContent:
    def test_flagged_field_name_correct(self):
        _, flagged = route_confidence(
            _rt(0.1), _lang(1.0),
            _std(1.0), _ind(1.0),
            country=None, threshold=THRESHOLD,
        )
        assert flagged[0].field_name == "report_type"

    def test_flagged_field_value_is_best_guess(self):
        _, flagged = route_confidence(
            _rt(0.1), _lang(1.0),
            _std(1.0), _ind(1.0),
            country=None, threshold=THRESHOLD,
        )
        assert flagged[0].value == "annual_report"

    def test_flagged_field_confidence_matches(self):
        _, flagged = route_confidence(
            _rt(0.1), _lang(1.0),
            _std(1.0), _ind(1.0),
            country=None, threshold=THRESHOLD,
        )
        assert flagged[0].confidence == pytest.approx(0.1)

    def test_reason_string_contains_confidence_and_threshold(self):
        below = 0.10
        _, flagged = route_confidence(
            _rt(below), _lang(1.0),
            _std(1.0), _ind(1.0),
            country=None, threshold=THRESHOLD,
        )
        assert str(below) in flagged[0].reason or "0.1" in flagged[0].reason
        assert str(THRESHOLD) in flagged[0].reason


# ---------------------------------------------------------------------------
# Country is never confidence-checked
# ---------------------------------------------------------------------------

class TestCountryNotChecked:
    def test_country_none_does_not_generate_flag(
        self, passing_report_type, passing_language,
        passing_accounting_standard, passing_industry
    ):
        _, flagged = route_confidence(
            passing_report_type, passing_language,
            passing_accounting_standard, passing_industry,
            country=None, threshold=THRESHOLD,
        )
        assert not any(f.field_name == "country" for f in flagged)

    def test_country_populated_does_not_affect_routing(
        self, passing_report_type, passing_language,
        passing_accounting_standard, passing_industry
    ):
        metadata, flagged = route_confidence(
            passing_report_type, passing_language,
            passing_accounting_standard, passing_industry,
            country="Japan", threshold=THRESHOLD,
        )
        assert metadata is not None
        assert metadata.country == "Japan"
        assert flagged == []
