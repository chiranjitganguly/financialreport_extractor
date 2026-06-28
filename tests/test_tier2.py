"""Unit tests for semantic_retriever/retrieval.py.

The vector store is NEVER called — search_vector_chunks is patched in every
test that touches retrieval.  All LLM calls (discrepancy resolution) are also
mocked.
"""

import pytest
from unittest.mock import MagicMock, patch

from semantic_retriever.retrieval import (
    _parse_table_row_chunk,
    extract_from_chunk,
    extract_semantic_candidates,
    run_semantic_retrieval,
)
from vector_indexer.schemas import TextChunk
from common.schemas import (
    ExtractionAttempt,
    ExtractionLedger,
    ExtractionRecord,
    TaxonomyEntry,
)
from common.taxonomy_map import initialize_extraction_ledger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(
    kpi_id: str = "rev_001",
    kpi_name: str = "Total Revenue",
    aliases: list[str] | None = None,
    canonical_sections: list[str] | None = None,
) -> TaxonomyEntry:
    return TaxonomyEntry(
        kpi_id=kpi_id,
        kpi_name=kpi_name,
        definition="Definition.",
        canonical_sections=canonical_sections or ["income_statement"],
        applicable_industries=[],
        applicable_report_types=[],
        applicable_accounting_standards=[],
        aliases=aliases or [],
    )


def _chunk(
    chunk_text: str,
    source_element_type: str = "text",
    section: str = "income_statement",
    page: int | None = 5,
    page_range: tuple[int, int] | None = None,
) -> TextChunk:
    return TextChunk(
        chunk_text=chunk_text,
        source_element_type=source_element_type,
        section_name_canonical=section,
        page=page,
        page_range=page_range,
    )


def _ledger_for(entries: list[TaxonomyEntry], fiscal_year: str = "FY2025") -> ExtractionLedger:
    return initialize_extraction_ledger(entries, fiscal_year)


# ---------------------------------------------------------------------------
# _parse_table_row_chunk
# ---------------------------------------------------------------------------

class TestParseTableRowChunk:

    def test_standard_format(self):
        label, cells = _parse_table_row_chunk("Total Revenue — FY2025: 12,000; FY2024: 11,000")
        assert label == "Total Revenue"
        assert cells["FY2025"] == "12,000"
        assert cells["FY2024"] == "11,000"

    def test_single_cell(self):
        label, cells = _parse_table_row_chunk("Revenue — FY2025: 5000")
        assert label == "Revenue"
        assert cells["FY2025"] == "5000"

    def test_no_separator_returns_label_empty_cells(self):
        label, cells = _parse_table_row_chunk("Revenue only label")
        assert label == "Revenue only label"
        assert cells == {}

    def test_strips_whitespace(self):
        label, cells = _parse_table_row_chunk("  Revenue  — FY2025 : 100 ")
        assert label == "Revenue"

    def test_empty_string(self):
        label, cells = _parse_table_row_chunk("")
        assert label == ""
        assert cells == {}

    def test_colons_in_value(self):
        label, cells = _parse_table_row_chunk("Revenue — Note: See note 3")
        assert label == "Revenue"
        # partition on first ": " only
        assert "Note" in cells

    def test_multiple_em_dashes_uses_first(self):
        label, cells = _parse_table_row_chunk("A — B — FY2025: 100")
        # partition on first " — "
        assert label == "A"
        # Rest is "B — FY2025: 100" — "B " has no colon so ignored
        # "FY2025: 100" parsed correctly in second ";" split? Let's check.
        # cells_str = "B — FY2025: 100"
        # split("; ") -> ["B — FY2025: 100"]
        # partition(": ") -> ("B — FY2025", ": ", "100")
        assert "B — FY2025" in cells


# ---------------------------------------------------------------------------
# extract_from_chunk — table_row
# ---------------------------------------------------------------------------

class TestExtractFromChunkTableRow:

    def test_exact_match_returns_value(self):
        chunk = _chunk("Total Revenue — FY2025: 12000; FY2024: 11000", "table_row")
        cv = extract_from_chunk(chunk, "Total Revenue", [], "FY2025")
        assert cv is not None
        assert cv.value == "12000"
        assert cv.source_element_type == "table_cell"

    def test_alias_match(self):
        chunk = _chunk("Net Sales — FY2025: 9000", "table_row")
        cv = extract_from_chunk(chunk, "Total Revenue", ["Net Sales"], "FY2025")
        assert cv is not None
        assert cv.value == "9000"

    def test_no_fiscal_year_column_returns_none(self):
        chunk = _chunk("Total Revenue — FY2024: 11000", "table_row")
        cv = extract_from_chunk(chunk, "Total Revenue", [], "FY2025")
        assert cv is None

    def test_below_cutoff_returns_none(self):
        chunk = _chunk("Unrelated Label — FY2025: 999", "table_row")
        cv = extract_from_chunk(chunk, "Total Revenue", [], "FY2025")
        assert cv is None

    def test_page_from_chunk_page(self):
        chunk = _chunk("Total Revenue — FY2025: 100", "table_row", page=7)
        cv = extract_from_chunk(chunk, "Total Revenue", [], "FY2025")
        assert cv is not None
        assert cv.page == 7

    def test_page_from_page_range_when_page_is_none(self):
        chunk = _chunk("Total Revenue — FY2025: 100", "table_row", page=None, page_range=(3, 5))
        cv = extract_from_chunk(chunk, "Total Revenue", [], "FY2025")
        assert cv is not None
        assert cv.page == 3

    def test_section_propagated(self):
        chunk = _chunk("Revenue — FY2025: 100", "table_row", section="financial_highlights")
        cv = extract_from_chunk(chunk, "Revenue", [], "FY2025")
        assert cv is not None
        assert cv.section_name_canonical == "financial_highlights"

    def test_confidence_is_positive(self):
        chunk = _chunk("Total Revenue — FY2025: 100", "table_row")
        cv = extract_from_chunk(chunk, "Total Revenue", [], "FY2025")
        assert cv is not None
        assert cv.confidence > 0.0

    def test_no_cells_in_chunk_returns_none(self):
        chunk = _chunk("Revenue only", "table_row")
        cv = extract_from_chunk(chunk, "Revenue", [], "FY2025")
        assert cv is None

    def test_long_year_label(self):
        chunk = _chunk("Total Revenue — Year ended March 31, 2025: 7777", "table_row")
        cv = extract_from_chunk(chunk, "Total Revenue", [], "FY2025")
        assert cv is not None
        assert cv.value == "7777"


# ---------------------------------------------------------------------------
# extract_from_chunk — text / chart_interpretation / footnote
# ---------------------------------------------------------------------------

class TestExtractFromChunkNarrative:

    def test_text_chunk_match(self):
        chunk = _chunk("Total Revenue: 5000", "text")
        cv = extract_from_chunk(chunk, "Total Revenue", [], "FY2025")
        assert cv is not None
        assert "5000" in str(cv.value)
        assert cv.source_element_type == "text"

    def test_chart_interpretation_chunk(self):
        chunk = _chunk("Revenue: 8000 million", "chart_interpretation")
        cv = extract_from_chunk(chunk, "Revenue", [], "FY2025")
        assert cv is not None

    def test_footnote_chunk(self):
        chunk = _chunk("Net Revenue: 3000 (including adjustments)", "footnote")
        cv = extract_from_chunk(chunk, "Net Revenue", [], "FY2025")
        assert cv is not None

    def test_no_match_returns_none(self):
        chunk = _chunk("Unrelated text about something else.", "text")
        cv = extract_from_chunk(chunk, "Total Revenue", [], "FY2025")
        assert cv is None

    def test_section_propagated(self):
        chunk = _chunk("Revenue: 5000", "text", section="management_discussion")
        cv = extract_from_chunk(chunk, "Revenue", [], "FY2025")
        assert cv is not None
        assert cv.section_name_canonical == "management_discussion"

    def test_page_range_from_chunk(self):
        chunk = _chunk("Revenue: 5000", "text", page=None, page_range=(10, 12))
        cv = extract_from_chunk(chunk, "Revenue", [], "FY2025")
        assert cv is not None
        assert cv.page == 10

    def test_no_page_and_no_range_uses_zero(self):
        chunk = TextChunk(
            chunk_text="Revenue: 5000",
            source_element_type="text",
            section_name_canonical="income_statement",
            page=None,
            page_range=None,
        )
        cv = extract_from_chunk(chunk, "Revenue", [], "FY2025")
        assert cv is not None
        assert cv.page == 0


# ---------------------------------------------------------------------------
# extract_semantic_candidates
# ---------------------------------------------------------------------------

class TestExtractTier2CandidatesForKpi:

    @patch("semantic_retriever.retrieval.search_vector_chunks")
    def test_no_chunks_returns_empty(self, mock_search):
        mock_search.return_value = []
        entry = _entry()
        result = extract_semantic_candidates(entry, "RPT_001", "FY2025", 5)
        assert result == []

    @patch("semantic_retriever.retrieval.search_vector_chunks")
    def test_matching_chunk_returns_candidate(self, mock_search):
        mock_search.return_value = [_chunk("Total Revenue: 12000")]
        entry = _entry()
        result = extract_semantic_candidates(entry, "RPT_001", "FY2025", 5)
        assert len(result) == 1
        assert "12000" in str(result[0].value)

    @patch("semantic_retriever.retrieval.search_vector_chunks")
    def test_deduplication_by_section_and_value(self, mock_search):
        """Two chunks from same section with same value → one candidate."""
        c1 = _chunk("Total Revenue: 5000", section="income_statement")
        c2 = _chunk("Total Revenue: 5000", section="income_statement")
        mock_search.return_value = [c1, c2]
        entry = _entry()
        result = extract_semantic_candidates(entry, "RPT_001", "FY2025", 5)
        assert len(result) == 1

    @patch("semantic_retriever.retrieval.search_vector_chunks")
    def test_different_sections_same_value_both_kept(self, mock_search):
        """Same value from two different sections → two candidates (not deduped)."""
        c1 = _chunk("Total Revenue: 5000", section="income_statement")
        c2 = _chunk("Total Revenue: 5000", section="financial_highlights")
        mock_search.return_value = [c1, c2]
        entry = _entry()
        result = extract_semantic_candidates(entry, "RPT_001", "FY2025", 5)
        assert len(result) == 2

    @patch("semantic_retriever.retrieval.search_vector_chunks")
    def test_non_matching_chunks_filtered(self, mock_search):
        mock_search.return_value = [_chunk("Unrelated content here.")]
        entry = _entry()
        result = extract_semantic_candidates(entry, "RPT_001", "FY2025", 5)
        assert result == []

    @patch("semantic_retriever.retrieval.search_vector_chunks")
    def test_search_called_with_correct_params(self, mock_search):
        mock_search.return_value = []
        entry = _entry(kpi_name="Revenue", aliases=["Net Sales"], canonical_sections=["income_statement"])
        extract_semantic_candidates(entry, "RPT_XYZ", "FY2025", 7)
        mock_search.assert_called_once_with(
            kpi_name="Revenue",
            aliases=["Net Sales"],
            canonical_sections=["income_statement"],
            report_id="RPT_XYZ",
            top_k=7,
        )


# ---------------------------------------------------------------------------
# run_semantic_retrieval
# ---------------------------------------------------------------------------

class TestRunTier2:

    @patch("semantic_retriever.retrieval.search_vector_chunks")
    def test_not_found_skips_found_entries(self, mock_search):
        mock_search.return_value = []
        entry = _entry()
        ledger = _ledger_for([entry])
        ledger.records["rev_001"].status = "found"
        ledger.records["rev_001"].value = "PREEXISTING"
        result = run_semantic_retrieval(ledger, [entry], "RPT_001", "FY2025", 5)
        assert result.records["rev_001"].value == "PREEXISTING"
        mock_search.assert_not_called()

    @patch("semantic_retriever.retrieval.search_vector_chunks")
    def test_no_results_records_attempt(self, mock_search):
        mock_search.return_value = []
        entry = _entry()
        ledger = _ledger_for([entry])
        result = run_semantic_retrieval(ledger, [entry], "RPT_001", "FY2025", 5)
        rec = result.records["rev_001"]
        assert rec.status == "not_found"
        assert len(rec.attempts) == 1
        assert rec.attempts[0].tier == "semantic"
        assert rec.attempts[0].outcome == "not_found"

    @patch("semantic_retriever.retrieval.search_vector_chunks")
    def test_single_value_sets_found(self, mock_search):
        mock_search.return_value = [_chunk("Total Revenue: 12000")]
        entry = _entry()
        ledger = _ledger_for([entry])
        result = run_semantic_retrieval(ledger, [entry], "RPT_001", "FY2025", 5)
        rec = result.records["rev_001"]
        assert rec.status == "found"
        assert rec.method == "semantic"
        assert "12000" in str(rec.value)

    @patch("semantic_retriever.retrieval.search_vector_chunks")
    def test_found_method_is_semantic_not_deterministic(self, mock_search):
        mock_search.return_value = [_chunk("Total Revenue: 5000")]
        entry = _entry()
        ledger = _ledger_for([entry])
        result = run_semantic_retrieval(ledger, [entry], "RPT_001", "FY2025", 5)
        assert result.records["rev_001"].method == "semantic"

    @patch("semantic_retriever.retrieval.search_vector_chunks")
    def test_found_records_attempt(self, mock_search):
        mock_search.return_value = [_chunk("Total Revenue: 5000")]
        entry = _entry()
        ledger = _ledger_for([entry])
        result = run_semantic_retrieval(ledger, [entry], "RPT_001", "FY2025", 5)
        rec = result.records["rev_001"]
        assert len(rec.attempts) == 1
        assert rec.attempts[0].outcome == "found"
        assert rec.attempts[0].tier == "semantic"

    @patch("semantic_retriever.retrieval.resolve_cross_section_discrepancy")
    @patch("semantic_retriever.retrieval.search_vector_chunks")
    def test_discrepancy_calls_step_6a(self, mock_search, mock_resolve):
        """Two sections return different values → Step 6a called."""
        c1 = _chunk("Total Revenue: 5000", section="income_statement")
        c2 = _chunk("Total Revenue: 4999", section="financial_highlights")
        mock_search.return_value = [c1, c2]

        resolved = ExtractionRecord(
            kpi_id="rev_001",
            fiscal_year="",
            value="5000",
            status="needs_human_review",
            review_reason="section_discrepancy",
            method="llm",
        )
        mock_resolve.return_value = resolved

        entry = _entry()
        ledger = _ledger_for([entry])
        result = run_semantic_retrieval(ledger, [entry], "RPT_001", "FY2025", 5)

        mock_resolve.assert_called_once()
        assert result.records["rev_001"].status == "needs_human_review"
        assert result.records["rev_001"].fiscal_year == "FY2025"

    @patch("semantic_retriever.retrieval.resolve_cross_section_discrepancy")
    @patch("semantic_retriever.retrieval.search_vector_chunks")
    def test_discrepancy_stamps_fiscal_year(self, mock_search, mock_resolve):
        c1 = _chunk("Total Revenue: 5000", section="income_statement")
        c2 = _chunk("Total Revenue: 4999", section="financial_highlights")
        mock_search.return_value = [c1, c2]

        resolved = ExtractionRecord(
            kpi_id="rev_001",
            fiscal_year="",
            value="5000",
            status="needs_human_review",
            review_reason="section_discrepancy",
            method="llm",
        )
        mock_resolve.return_value = resolved

        entry = _entry()
        ledger = _ledger_for([entry])
        result = run_semantic_retrieval(ledger, [entry], "RPT_001", "FY2026", 5)

        assert result.records["rev_001"].fiscal_year == "FY2026"

    @patch("semantic_retriever.retrieval.search_vector_chunks")
    def test_kpi_not_in_taxonomy_skipped(self, mock_search):
        mock_search.return_value = []
        entry = _entry()
        ledger = _ledger_for([entry])
        # Pass empty taxonomy — kpi_id has no match
        result = run_semantic_retrieval(ledger, [], "RPT_001", "FY2025", 5)
        assert result.records["rev_001"].status == "not_found"
        mock_search.assert_not_called()

    @patch("semantic_retriever.retrieval.search_vector_chunks")
    def test_table_row_chunk_extracted(self, mock_search):
        table_chunk = _chunk(
            "Total Revenue — FY2025: 77000; FY2024: 70000",
            source_element_type="table_row",
        )
        mock_search.return_value = [table_chunk]
        entry = _entry()
        ledger = _ledger_for([entry])
        result = run_semantic_retrieval(ledger, [entry], "RPT_001", "FY2025", 5)
        rec = result.records["rev_001"]
        assert rec.status == "found"
        assert rec.value == "77000"
        assert rec.source_element_type == "table_cell"

    @patch("semantic_retriever.retrieval.search_vector_chunks")
    def test_multiple_kpis_independent(self, mock_search):
        e1 = _entry("rev_001", "Total Revenue")
        e2 = _entry("pat_001", "Profit After Tax")

        def side_effect(kpi_name, aliases, **kwargs):
            if kpi_name == "Total Revenue":
                return [_chunk("Total Revenue: 10000")]
            return []  # PAT not found

        mock_search.side_effect = side_effect

        ledger = _ledger_for([e1, e2])
        result = run_semantic_retrieval(ledger, [e1, e2], "RPT_001", "FY2025", 5)
        assert result.records["rev_001"].status == "found"
        assert result.records["pat_001"].status == "not_found"
