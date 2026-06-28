"""Tests for deterministic classifier stages (no LLM calls)."""
import pytest

from report_ingestion.classifiers.accounting_standard import (
    detect_accounting_standard_deterministic,
    detect_accounting_standard_from_map,
)
from report_ingestion.classifiers.industry import detect_industry_from_map
from report_ingestion.classifiers.report_type import detect_report_type_deterministic
from report_ingestion.converter import get_classification_excerpt


# ---------------------------------------------------------------------------
# Report type — deterministic stage
# ---------------------------------------------------------------------------

class TestReportTypeDetector:
    # SEC form codes
    def test_10k_returns_annual_report(self):
        result = detect_report_type_deterministic("Filed on Form 10-K for fiscal year 2024")
        assert result is not None
        assert result.report_type == "annual_report"

    def test_10k_without_form_prefix(self):
        result = detect_report_type_deterministic("This 10-K filing covers the period ending December 31")
        assert result is not None
        assert result.report_type == "annual_report"

    def test_10q_returns_quarterly_report(self):
        result = detect_report_type_deterministic("Form 10-Q for the quarter ended March 31, 2024")
        assert result is not None
        assert result.report_type == "quarterly_report"

    def test_20f_returns_annual_report(self):
        result = detect_report_type_deterministic("Annual report on Form 20-F")
        assert result is not None
        assert result.report_type == "annual_report"

    def test_40f_returns_annual_report(self):
        result = detect_report_type_deterministic("Form 40-F Annual Report")
        assert result is not None
        assert result.report_type == "annual_report"

    def test_8k_returns_regulatory_filing(self):
        result = detect_report_type_deterministic("Current report on Form 8-K")
        assert result is not None
        assert result.report_type == "regulatory_filing"

    def test_6k_returns_regulatory_filing(self):
        result = detect_report_type_deterministic("Report of Foreign Private Issuer on Form 6-K")
        assert result is not None
        assert result.report_type == "regulatory_filing"

    # Self-identifying title text
    def test_annual_report_text(self):
        result = detect_report_type_deterministic("Acme Corp\nAnnual Report 2024")
        assert result is not None
        assert result.report_type == "annual_report"

    def test_quarterly_report_text(self):
        result = detect_report_type_deterministic("Quarterly Report — Q1 FY2025")
        assert result is not None
        assert result.report_type == "quarterly_report"

    def test_interim_report_text(self):
        result = detect_report_type_deterministic("Interim Report for the six months ended 30 June 2024")
        assert result is not None
        assert result.report_type == "quarterly_report"

    def test_half_year_report_text(self):
        result = detect_report_type_deterministic("Half-Year Report 2024")
        assert result is not None
        assert result.report_type == "quarterly_report"

    def test_semi_annual_report_text(self):
        result = detect_report_type_deterministic("Semi-Annual Report ending June 30")
        assert result is not None
        assert result.report_type == "quarterly_report"

    def test_prospectus_returns_regulatory_filing(self):
        result = detect_report_type_deterministic("Prospectus dated January 1, 2024")
        assert result is not None
        assert result.report_type == "regulatory_filing"

    # Metadata checks
    def test_match_has_confidence_1(self):
        result = detect_report_type_deterministic("Annual Report 2024")
        assert result is not None
        assert result.confidence == 1.0

    def test_match_source_is_document_marker(self):
        result = detect_report_type_deterministic("Annual Report 2024")
        assert result is not None
        assert result.source == "document_marker"

    def test_evidence_captured(self):
        result = detect_report_type_deterministic("This is an Annual Report for FY2024")
        assert result is not None
        assert result.evidence is not None
        assert "Annual Report" in result.evidence

    def test_case_insensitive(self):
        result = detect_report_type_deterministic("ANNUAL REPORT 2024")
        assert result is not None
        assert result.report_type == "annual_report"

    def test_no_marker_returns_none(self):
        result = detect_report_type_deterministic(
            "Acme Corporation\nFiscal Year 2024\nChairman's Message"
        )
        assert result is None


# ---------------------------------------------------------------------------
# Accounting standard — deterministic Stage A
# ---------------------------------------------------------------------------

class TestAccountingStandardDetectorDeterministic:
    def test_ifrs_long_form(self):
        result = detect_accounting_standard_deterministic(
            "prepared in accordance with International Financial Reporting Standards"
        )
        assert result is not None
        assert result.standard == "IFRS"

    def test_ifrs_acronym(self):
        result = detect_accounting_standard_deterministic(
            "These financial statements comply with IFRS as adopted by the EU"
        )
        assert result is not None
        assert result.standard == "IFRS"

    def test_us_gaap_long_form(self):
        result = detect_accounting_standard_deterministic(
            "in conformity with generally accepted accounting principles in the United States"
        )
        assert result is not None
        assert result.standard == "US-GAAP"

    def test_us_gaap_alternative_long_form(self):
        result = detect_accounting_standard_deterministic(
            "accounting principles generally accepted in the United States"
        )
        assert result is not None
        assert result.standard == "US-GAAP"

    def test_us_gaap_abbreviated(self):
        result = detect_accounting_standard_deterministic(
            "financial statements presented under U.S. GAAP"
        )
        assert result is not None
        assert result.standard == "US-GAAP"

    def test_us_gaap_no_dots(self):
        result = detect_accounting_standard_deterministic(
            "results are reported under US GAAP"
        )
        assert result is not None
        assert result.standard == "US-GAAP"

    def test_ind_as_long_form(self):
        result = detect_accounting_standard_deterministic(
            "prepared in accordance with Indian Accounting Standards"
        )
        assert result is not None
        assert result.standard == "IND-AS"

    def test_ind_as_acronym(self):
        result = detect_accounting_standard_deterministic(
            "The Company follows Ind AS for financial reporting"
        )
        assert result is not None
        assert result.standard == "IND-AS"

    def test_confidence_always_1(self):
        result = detect_accounting_standard_deterministic("prepared under IFRS")
        assert result is not None
        assert result.confidence == 1.0

    def test_source_is_document_statement(self):
        result = detect_accounting_standard_deterministic("prepared under IFRS")
        assert result is not None
        assert result.source == "document_statement"

    def test_evidence_captured(self):
        result = detect_accounting_standard_deterministic("prepared under IFRS standards")
        assert result is not None
        assert result.evidence is not None

    def test_no_standard_returns_none(self):
        result = detect_accounting_standard_deterministic(
            "The board of directors presents the annual results for the year ended"
        )
        assert result is None


# ---------------------------------------------------------------------------
# Accounting standard — Stage B (from map)
# ---------------------------------------------------------------------------

class TestAccountingStandardFromMap:
    def test_returns_result_when_standard_present(self, acme_lookup_result):
        result = detect_accounting_standard_from_map(acme_lookup_result)
        assert result is not None
        assert result.standard == "US-GAAP"
        assert result.source == "company_map"

    def test_confidence_equals_match_score(self, acme_lookup_result):
        result = detect_accounting_standard_from_map(acme_lookup_result)
        assert result is not None
        assert result.confidence == acme_lookup_result.match_score

    def test_returns_none_when_standard_is_null(self, retail_lookup_result_no_standard):
        result = detect_accounting_standard_from_map(retail_lookup_result_no_standard)
        assert result is None

    def test_returns_none_when_lookup_is_none(self):
        result = detect_accounting_standard_from_map(None)
        assert result is None


# ---------------------------------------------------------------------------
# Industry — Stage B (from map)
# ---------------------------------------------------------------------------

class TestIndustryFromMap:
    def test_returns_result_when_lookup_present(self, acme_lookup_result):
        result = detect_industry_from_map(acme_lookup_result)
        assert result is not None
        assert result.industry == "Manufacturing"
        assert result.source == "company_map"

    def test_confidence_equals_match_score(self, acme_lookup_result):
        result = detect_industry_from_map(acme_lookup_result)
        assert result is not None
        assert result.confidence == acme_lookup_result.match_score

    def test_returns_none_when_lookup_is_none(self):
        result = detect_industry_from_map(None)
        assert result is None


# ---------------------------------------------------------------------------
# Converter — get_classification_excerpt
# ---------------------------------------------------------------------------

class TestGetClassificationExcerpt:
    def test_short_text_returned_unchanged(self):
        text = "Short document text."
        assert get_classification_excerpt(text, max_chars=6000) == text

    def test_text_exactly_at_limit_returned_unchanged(self):
        text = "x" * 6000
        assert get_classification_excerpt(text, max_chars=6000) == text

    def test_truncates_at_paragraph_boundary(self):
        para1 = "First paragraph content here."
        para2 = "Second paragraph that should be cut off."
        text = para1 + "\n\n" + para2
        max_chars = len(para1) + 5  # limit falls inside para2
        result = get_classification_excerpt(text, max_chars=max_chars)
        assert result == para1
        assert "Second paragraph" not in result

    def test_hard_truncates_when_no_paragraph_break(self):
        text = "A" * 200
        result = get_classification_excerpt(text, max_chars=100)
        assert result == "A" * 100

    def test_custom_max_chars_respected(self):
        text = "word " * 1000
        result = get_classification_excerpt(text, max_chars=50)
        assert len(result) <= 50
