"""Shared pytest fixtures."""
from pathlib import Path

import pytest

from report_ingestion.industry_map import load_company_reference_map
from report_ingestion.schemas import (
    AccountingStandardResult,
    CompanyLookupResult,
    CompanyReferenceEntry,
    IndustryResult,
    LanguageResult,
    ReportTypeResult,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
COMPANY_MAP_FIXTURE = FIXTURE_DIR / "company_reference_map.json"


@pytest.fixture
def sample_reference_map() -> list[CompanyReferenceEntry]:
    return load_company_reference_map(str(COMPANY_MAP_FIXTURE))


@pytest.fixture
def acme_lookup_result(sample_reference_map) -> CompanyLookupResult:
    """A high-confidence lookup result for 'Acme Manufacturing Inc'."""
    entry = next(e for e in sample_reference_map if "Acme" in e.company_name)
    return CompanyLookupResult(matched_entry=entry, match_score=0.97)


@pytest.fixture
def retail_lookup_result_no_standard(sample_reference_map) -> CompanyLookupResult:
    """A lookup result for a company whose accounting_standard is null."""
    entry = next(e for e in sample_reference_map if e.accounting_standard is None)
    return CompanyLookupResult(matched_entry=entry, match_score=0.91)


# ---------------------------------------------------------------------------
# Passing result set — all confidences at 1.0 (used in routing & pipeline tests)
# ---------------------------------------------------------------------------

@pytest.fixture
def passing_report_type() -> ReportTypeResult:
    return ReportTypeResult(
        report_type="annual_report", confidence=1.0, source="document_marker"
    )


@pytest.fixture
def passing_language() -> LanguageResult:
    return LanguageResult(language="en", confidence=0.99)


@pytest.fixture
def passing_accounting_standard() -> AccountingStandardResult:
    return AccountingStandardResult(
        standard="IFRS", confidence=1.0, source="document_statement"
    )


@pytest.fixture
def passing_industry() -> IndustryResult:
    return IndustryResult(
        industry="Telecommunications", confidence=0.95, source="company_map"
    )
