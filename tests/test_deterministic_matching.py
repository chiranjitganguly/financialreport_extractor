"""Unit tests for common/deterministic_matching.py.

All tests are pure deterministic — no LLM calls, no DB.
"""

import pytest

from common.deterministic_matching import (
    _build_narrative_pattern,
    _normalize_fiscal_year,
    compute_match_confidence,
    match_narrative_text,
    match_table_row,
)
from common.schemas import TableCell, TableElement, TableRow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _table(rows: list[TableRow], section: str = "income_statement", page: int = 10) -> TableElement:
    return TableElement(
        table_id="tbl_001",
        section_name_canonical=section,
        page=page,
        rows=rows,
        footnote_refs=[],
    )


def _row(label: str, columns: dict[str, str]) -> TableRow:
    cells = [
        TableCell(column_label=col, value=val, footnote_refs=[])
        for col, val in columns.items()
    ]
    return TableRow(row_label=label, cells=cells)


def _row_with_footnotes(label: str, col: str, val: str, footnotes: list[str]) -> TableRow:
    cell = TableCell(column_label=col, value=val, footnote_refs=footnotes)
    return TableRow(row_label=label, cells=[cell])


# ---------------------------------------------------------------------------
# compute_match_confidence
# ---------------------------------------------------------------------------

class TestComputeMatchConfidence:
    def test_single_candidate_is_perfect(self):
        assert compute_match_confidence(1, 1.0, None) == 1.0

    def test_single_candidate_ignores_score(self):
        assert compute_match_confidence(1, 0.5, None) == 1.0

    def test_two_equal_candidates_minimum_confidence(self):
        # margin = 0 → 0.5 + 0.2*0 = 0.5
        result = compute_match_confidence(2, 0.9, 0.9)
        assert result == 0.5

    def test_two_well_separated_candidates(self):
        # top=1.0, runner_up=0.0 → margin=1.0 → 0.5+0.2=0.7
        result = compute_match_confidence(2, 1.0, 0.0)
        assert result == pytest.approx(0.7, abs=0.001)

    def test_mid_separation(self):
        # top=1.0, runner_up=0.5 → margin=0.5 → 0.6
        result = compute_match_confidence(2, 1.0, 0.5)
        assert result == pytest.approx(0.6, abs=0.001)

    def test_no_runner_up_multiple_candidates(self):
        # no runner_up_score → 0.7
        assert compute_match_confidence(3, 0.9, None) == 0.7

    def test_zero_top_score_no_division_by_zero(self):
        result = compute_match_confidence(2, 0.0, 0.0)
        assert 0 <= result <= 1.0


# ---------------------------------------------------------------------------
# _normalize_fiscal_year
# ---------------------------------------------------------------------------

class TestNormalizeFiscalYear:
    def test_extracts_plain_year(self):
        assert _normalize_fiscal_year("FY2025") == "2025"

    def test_extracts_year_from_long_label(self):
        assert _normalize_fiscal_year("Year ended March 31, 2024") == "2024"

    def test_extracts_first_year_only(self):
        # When two years in label, grabs the first 20xx
        result = _normalize_fiscal_year("2023-2024")
        assert result == "2023"

    def test_fallback_for_no_year(self):
        assert _normalize_fiscal_year("No year here") == "No year here"

    def test_exact_four_digit_year(self):
        assert _normalize_fiscal_year("2026") == "2026"


# ---------------------------------------------------------------------------
# match_table_row
# ---------------------------------------------------------------------------

class TestMatchTableRow:
    def test_exact_label_match(self):
        table = _table([_row("Total Revenue", {"FY2025": "12345"})])
        cv = match_table_row(table, "Total Revenue", [], "FY2025", 85.0)
        assert cv is not None
        assert cv.value == "12345"
        assert cv.source_element_type == "table_cell"

    def test_alias_match(self):
        table = _table([_row("Net Sales", {"FY2025": "9999"})])
        cv = match_table_row(table, "Total Revenue", ["Net Sales", "Revenue"], "FY2025", 85.0)
        assert cv is not None
        assert cv.value == "9999"

    def test_fuzzy_label_match(self):
        table = _table([_row("Total Revenues", {"FY2025": "11000"})])
        cv = match_table_row(table, "Total Revenue", [], "FY2025", 80.0)
        assert cv is not None

    def test_no_match_below_cutoff(self):
        table = _table([_row("Depreciation", {"FY2025": "500"})])
        cv = match_table_row(table, "Total Revenue", [], "FY2025", 85.0)
        assert cv is None

    def test_no_fiscal_year_column_returns_none(self):
        table = _table([_row("Total Revenue", {"FY2024": "12345"})])
        cv = match_table_row(table, "Total Revenue", [], "FY2025", 85.0)
        assert cv is None

    def test_returns_correct_section_and_page(self):
        table = _table([_row("Total Revenue", {"FY2025": "100"})], section="financial_highlights", page=5)
        cv = match_table_row(table, "Total Revenue", [], "FY2025", 85.0)
        assert cv is not None
        assert cv.section_name_canonical == "financial_highlights"
        assert cv.page == 5

    def test_footnote_refs_propagated(self):
        row = _row_with_footnotes("Total Revenue", "FY2025", "500", ["1", "2"])
        table = _table([row])
        cv = match_table_row(table, "Total Revenue", [], "FY2025", 85.0)
        assert cv is not None
        assert "1" in cv.footnotes
        assert "2" in cv.footnotes

    def test_single_row_match_confidence_is_1(self):
        table = _table([_row("Total Revenue", {"FY2025": "12345"})])
        cv = match_table_row(table, "Total Revenue", [], "FY2025", 85.0)
        assert cv is not None
        assert cv.confidence == 1.0

    def test_multiple_passing_rows_lowers_confidence(self):
        table = _table([
            _row("Total Revenue", {"FY2025": "12345"}),
            _row("Total Revenues", {"FY2025": "12345"}),
        ])
        cv = match_table_row(table, "Total Revenue", [], "FY2025", 80.0)
        assert cv is not None
        assert cv.confidence < 1.0

    def test_long_year_column_label(self):
        table = _table([_row("Total Revenue", {"Year ended March 31, 2025": "777"})])
        cv = match_table_row(table, "Total Revenue", [], "FY2025", 85.0)
        assert cv is not None
        assert cv.value == "777"

    def test_empty_table(self):
        table = _table([])
        cv = match_table_row(table, "Total Revenue", [], "FY2025", 85.0)
        assert cv is None


# ---------------------------------------------------------------------------
# _build_narrative_pattern
# ---------------------------------------------------------------------------

class TestBuildNarrativePattern:
    def test_basic_colon_match(self):
        pattern = _build_narrative_pattern("Total Revenue", [])
        m = pattern.search("Total Revenue: 12345")
        assert m is not None

    def test_dash_separator(self):
        pattern = _build_narrative_pattern("Revenue", [])
        m = pattern.search("Revenue - 9999")
        assert m is not None

    def test_no_separator(self):
        pattern = _build_narrative_pattern("Revenue", [])
        m = pattern.search("Revenue 5000")
        assert m is not None

    def test_alias_matches(self):
        pattern = _build_narrative_pattern("Total Revenue", ["Net Sales"])
        m = pattern.search("Net Sales: 3000")
        assert m is not None

    def test_no_match_for_unrelated_text(self):
        pattern = _build_narrative_pattern("Total Revenue", [])
        m = pattern.search("Operating costs were 1000")
        assert m is None

    def test_currency_prefix(self):
        pattern = _build_narrative_pattern("Revenue", [])
        m = pattern.search("Revenue: $12,345.67")
        assert m is not None

    def test_scale_suffix(self):
        pattern = _build_narrative_pattern("Revenue", [])
        m = pattern.search("Revenue: 1234 million")
        assert m is not None

    def test_case_insensitive(self):
        pattern = _build_narrative_pattern("Total Revenue", [])
        m = pattern.search("total revenue: 5000")
        assert m is not None


# ---------------------------------------------------------------------------
# match_narrative_text
# ---------------------------------------------------------------------------

class TestMatchNarrativeText:
    def test_basic_match(self):
        cv = match_narrative_text(
            "Total Revenue: 12345",
            "Total Revenue",
            [],
            (1, 3),
        )
        assert cv is not None
        assert "12345" in str(cv.value)

    def test_alias_match(self):
        cv = match_narrative_text(
            "Net Sales: 9999",
            "Total Revenue",
            ["Net Sales"],
            (2, 4),
        )
        assert cv is not None

    def test_no_match_returns_none(self):
        cv = match_narrative_text(
            "Operating expenses were 500.",
            "Total Revenue",
            [],
            (1, 2),
        )
        assert cv is None

    def test_empty_content_returns_none(self):
        cv = match_narrative_text("", "Total Revenue", [], (1, 2))
        assert cv is None

    def test_page_uses_range_start(self):
        cv = match_narrative_text(
            "Revenue: 5000",
            "Revenue",
            [],
            (7, 9),
        )
        assert cv is not None
        assert cv.page == 7

    def test_source_element_type_is_text(self):
        cv = match_narrative_text("Revenue: 5000", "Revenue", [], (1, 2))
        assert cv is not None
        assert cv.source_element_type == "text"

    def test_no_footnotes_in_narrative(self):
        cv = match_narrative_text("Revenue: 5000", "Revenue", [], (1, 2))
        assert cv is not None
        assert cv.footnotes == []

    def test_single_unique_value_confidence_is_1(self):
        cv = match_narrative_text("Revenue: 5000", "Revenue", [], (1, 2))
        assert cv is not None
        assert cv.confidence == 1.0

    def test_multiple_unique_values_lower_confidence(self):
        # Two different numbers found → confidence < 1.0
        cv = match_narrative_text(
            "Revenue: 5000. Net Revenue: 4999.",
            "Revenue",
            ["Net Revenue"],
            (1, 2),
        )
        assert cv is not None
        assert cv.confidence < 1.0

    def test_whitespace_only_content_returns_none(self):
        cv = match_narrative_text("   \n\t  ", "Revenue", [], (1, 2))
        assert cv is None
