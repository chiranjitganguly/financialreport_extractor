"""Unit tests for deterministic_extractor/extraction.py.

All LLM calls (discrepancy resolution) are mocked.  All deterministic
matching runs against inline fake Section/Table objects.
"""

import pytest
from unittest.mock import MagicMock, patch

from deterministic_extractor.extraction import extract_deterministic_candidates, run_deterministic_extraction
from common.schemas import (
    CandidateValue,
    ExtractionLedger,
    ExtractionRecord,
    Section,
    TableCell,
    TableElement,
    TableRow,
    TaxonomyEntry,
)
from common.taxonomy_map import initialize_extraction_ledger


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------

def _taxonomy_entry(
    kpi_id: str = "rev_001",
    kpi_name: str = "Total Revenue",
    canonical_sections: list[str] | None = None,
    aliases: list[str] | None = None,
) -> TaxonomyEntry:
    return TaxonomyEntry(
        kpi_id=kpi_id,
        kpi_name=kpi_name,
        definition="Definition of " + kpi_name,
        canonical_sections=canonical_sections or ["income_statement"],
        applicable_industries=[],
        applicable_report_types=[],
        applicable_accounting_standards=[],
        aliases=aliases or [],
    )


def _section(
    canonical: str = "income_statement",
    markdown: str = "",
    tables: list[TableElement] | None = None,
    page_range: tuple[int, int] = (1, 5),
) -> Section:
    return Section(
        section_name_raw=canonical.replace("_", " ").title(),
        section_name_canonical=canonical,
        alignment_confidence=0.95,
        alignment_source="fuzzy_match",
        content_markdown=markdown,
        tables=tables or [],
        charts=[],
        footnotes=[],
        page_range=page_range,
    )


def _table_with_value(kpi_label: str, fiscal_year: str, value: str, section: str = "income_statement") -> TableElement:
    row = TableRow(
        row_label=kpi_label,
        cells=[TableCell(column_label=fiscal_year, value=value, footnote_refs=[])],
    )
    return TableElement(
        table_id="tbl_001",
        section_name_canonical=section,
        page=3,
        rows=[row],
        footnote_refs=[],
    )


# ---------------------------------------------------------------------------
# extract_deterministic_candidates
# ---------------------------------------------------------------------------

class TestExtractTier1CandidatesForKpi:

    def test_empty_sections_returns_empty(self):
        entry = _taxonomy_entry()
        candidates = extract_deterministic_candidates(entry, {}, "FY2025", 85.0)
        assert candidates == []

    def test_section_not_in_canonical_sections_skipped(self):
        entry = _taxonomy_entry(canonical_sections=["income_statement"])
        sections_map = {"balance_sheet": [_section("balance_sheet", "Revenue: 5000")]}
        candidates = extract_deterministic_candidates(entry, sections_map, "FY2025", 85.0)
        assert candidates == []

    def test_table_match_returns_candidate(self):
        table = _table_with_value("Total Revenue", "FY2025", "12000")
        section = _section("income_statement", tables=[table])
        sections_map = {"income_statement": [section]}
        entry = _taxonomy_entry()
        candidates = extract_deterministic_candidates(entry, sections_map, "FY2025", 85.0)
        assert len(candidates) == 1
        assert candidates[0].source_element_type == "table_cell"
        assert str(candidates[0].value) == "12000"

    def test_narrative_fallback_when_no_table_match(self):
        section = _section("income_statement", markdown="Total Revenue: 5000")
        sections_map = {"income_statement": [section]}
        entry = _taxonomy_entry()
        candidates = extract_deterministic_candidates(entry, sections_map, "FY2025", 85.0)
        assert len(candidates) == 1
        assert candidates[0].source_element_type == "text"

    def test_table_preferred_over_narrative(self):
        table = _table_with_value("Total Revenue", "FY2025", "12000")
        section = _section("income_statement", markdown="Revenue 9000", tables=[table])
        sections_map = {"income_statement": [section]}
        entry = _taxonomy_entry()
        candidates = extract_deterministic_candidates(entry, sections_map, "FY2025", 85.0)
        # Only 1 candidate (from table), narrative is skipped when table matches
        assert len(candidates) == 1
        assert candidates[0].source_element_type == "table_cell"

    def test_alias_match_in_table(self):
        table = _table_with_value("Net Sales", "FY2025", "8000")
        section = _section("income_statement", tables=[table])
        sections_map = {"income_statement": [section]}
        entry = _taxonomy_entry(aliases=["Net Sales", "Net Revenue"])
        candidates = extract_deterministic_candidates(entry, sections_map, "FY2025", 85.0)
        assert len(candidates) == 1
        assert str(candidates[0].value) == "8000"

    def test_multiple_sections_both_queried(self):
        t1 = _table_with_value("Total Revenue", "FY2025", "12000", "income_statement")
        t2 = _table_with_value("Total Revenue", "FY2025", "12000", "financial_highlights")
        s1 = _section("income_statement", tables=[t1])
        s2 = _section("financial_highlights", tables=[t2])
        sections_map = {
            "income_statement": [s1],
            "financial_highlights": [s2],
        }
        entry = _taxonomy_entry(canonical_sections=["income_statement", "financial_highlights"])
        candidates = extract_deterministic_candidates(entry, sections_map, "FY2025", 85.0)
        assert len(candidates) == 2

    def test_section_name_canonical_set_on_narrative_candidate(self):
        section = _section("income_statement", markdown="Total Revenue: 5000")
        sections_map = {"income_statement": [section]}
        entry = _taxonomy_entry()
        candidates = extract_deterministic_candidates(entry, sections_map, "FY2025", 85.0)
        assert candidates[0].section_name_canonical == "income_statement"


# ---------------------------------------------------------------------------
# run_deterministic_extraction
# ---------------------------------------------------------------------------

class TestRunTier1:

    def _ledger_for(self, entries: list[TaxonomyEntry], fiscal_year: str = "FY2025") -> ExtractionLedger:
        return initialize_extraction_ledger(entries, fiscal_year)

    def test_no_sections_leaves_not_found(self):
        entry = _taxonomy_entry()
        ledger = self._ledger_for([entry])
        result = run_deterministic_extraction(ledger, [entry], [], "FY2025")
        assert result.records["rev_001"].status == "not_found"

    def test_found_single_section(self):
        entry = _taxonomy_entry()
        table = _table_with_value("Total Revenue", "FY2025", "12345")
        section = _section("income_statement", tables=[table])
        ledger = self._ledger_for([entry])
        result = run_deterministic_extraction(ledger, [entry], [section], "FY2025")
        rec = result.records["rev_001"]
        assert rec.status == "found"
        assert str(rec.value) == "12345"
        assert rec.method == "deterministic"
        assert rec.confidence == 1.0

    def test_found_sets_section_name(self):
        entry = _taxonomy_entry()
        table = _table_with_value("Total Revenue", "FY2025", "12345")
        section = _section("income_statement", tables=[table])
        ledger = self._ledger_for([entry])
        result = run_deterministic_extraction(ledger, [entry], [section], "FY2025")
        assert result.records["rev_001"].section == "income_statement"

    def test_attempt_recorded_for_not_found(self):
        entry = _taxonomy_entry()
        ledger = self._ledger_for([entry])
        result = run_deterministic_extraction(ledger, [entry], [], "FY2025")
        rec = result.records["rev_001"]
        assert len(rec.attempts) == 1
        assert rec.attempts[0].tier == "deterministic"
        assert rec.attempts[0].outcome == "not_found"

    def test_attempt_recorded_for_found(self):
        entry = _taxonomy_entry()
        table = _table_with_value("Total Revenue", "FY2025", "12345")
        section = _section("income_statement", tables=[table])
        ledger = self._ledger_for([entry])
        result = run_deterministic_extraction(ledger, [entry], [section], "FY2025")
        rec = result.records["rev_001"]
        assert len(rec.attempts) == 1
        assert rec.attempts[0].outcome == "found"

    def test_already_found_record_skipped(self):
        entry = _taxonomy_entry()
        table = _table_with_value("Total Revenue", "FY2025", "12345")
        section = _section("income_statement", tables=[table])
        ledger = self._ledger_for([entry])
        # Pre-mark as found
        ledger.records["rev_001"].status = "found"
        ledger.records["rev_001"].value = "PRE_EXISTING"
        result = run_deterministic_extraction(ledger, [entry], [section], "FY2025")
        assert result.records["rev_001"].value == "PRE_EXISTING"

    @patch("deterministic_extractor.extraction.resolve_cross_section_discrepancy")
    def test_discrepancy_calls_step_6a(self, mock_resolve):
        """Two sections return different values — discrepancy resolver is called."""
        entry = _taxonomy_entry(canonical_sections=["income_statement", "financial_highlights"])
        t1 = _table_with_value("Total Revenue", "FY2025", "12000", "income_statement")
        t2 = _table_with_value("Total Revenue", "FY2025", "11950", "financial_highlights")
        s1 = _section("income_statement", tables=[t1])
        s2 = _section("financial_highlights", tables=[t2])

        resolved_record = ExtractionRecord(
            kpi_id="rev_001",
            fiscal_year="",
            value="12000",
            status="needs_human_review",
            review_reason="section_discrepancy",
            method="llm",
        )
        mock_resolve.return_value = resolved_record

        ledger = self._ledger_for([entry])
        result = run_deterministic_extraction(ledger, [entry], [s1, s2], "FY2025")

        mock_resolve.assert_called_once()
        # fiscal_year stamped by run_deterministic_extraction after resolve
        assert result.records["rev_001"].fiscal_year == "FY2025"
        assert result.records["rev_001"].status == "needs_human_review"

    def test_multiple_kpis_independent(self):
        e1 = _taxonomy_entry("rev_001", "Total Revenue", ["income_statement"])
        e2 = _taxonomy_entry("pat_001", "Profit After Tax", ["income_statement"])
        t1 = _table_with_value("Total Revenue", "FY2025", "12000")
        # No row for PAT → not_found
        section = _section("income_statement", tables=[t1])
        ledger = self._ledger_for([e1, e2])
        result = run_deterministic_extraction(ledger, [e1, e2], [section], "FY2025")

        assert result.records["rev_001"].status == "found"
        assert result.records["pat_001"].status == "not_found"

    def test_kpi_not_in_taxonomy_by_id_skipped(self):
        """Ledger has a kpi_id that isn't in filtered_taxonomy — should not crash."""
        entry = _taxonomy_entry("rev_001")
        ledger = self._ledger_for([entry])
        # Pass empty taxonomy — kpi_id has no TaxonomyEntry
        result = run_deterministic_extraction(ledger, [], [], "FY2025")
        # Should remain not_found, no crash
        assert result.records["rev_001"].status == "not_found"
