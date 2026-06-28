"""Unit tests for llm_extractor/extraction.py.

All LLM calls are mocked.  No Postgres, no real OpenAI API.
"""

import pytest
from unittest.mock import MagicMock, patch

from llm_extractor.extraction import (
    Tier3ExtractionResult,
    Tier3KPIRequest,
    _format_kpi_list,
    _table_to_markdown,
    build_section_context_for_prompt,
    run_llm_extraction,
    run_llm_extraction_for_section,
)
from common.schemas import (
    ChartElement,
    ExtractionLedger,
    ExtractionRecord,
    FootnoteAnchor,
    FootnoteElement,
    Section,
    TableCell,
    TableElement,
    TableRow,
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
    definition: str = "Total revenue for the fiscal year.",
) -> TaxonomyEntry:
    return TaxonomyEntry(
        kpi_id=kpi_id,
        kpi_name=kpi_name,
        definition=definition,
        canonical_sections=canonical_sections or ["income_statement"],
        applicable_industries=[],
        applicable_report_types=[],
        applicable_accounting_standards=[],
        aliases=aliases or [],
    )


def _section(
    canonical: str = "income_statement",
    markdown: str = "Some narrative.",
    tables: list[TableElement] | None = None,
    footnotes: list[FootnoteElement] | None = None,
    charts: list[ChartElement] | None = None,
    page_range: tuple[int, int] = (1, 5),
) -> Section:
    return Section(
        section_name_raw=canonical.replace("_", " ").title(),
        section_name_canonical=canonical,
        alignment_confidence=0.9,
        alignment_source="fuzzy_match",
        content_markdown=markdown,
        tables=tables or [],
        charts=charts or [],
        footnotes=footnotes or [],
        page_range=page_range,
    )


def _table(
    rows: list[tuple[str, dict[str, str]]],
    section: str = "income_statement",
    page: int = 3,
) -> TableElement:
    table_rows = [
        TableRow(
            row_label=label,
            cells=[
                TableCell(column_label=col, value=val, footnote_refs=[])
                for col, val in cells.items()
            ],
        )
        for label, cells in rows
    ]
    return TableElement(
        table_id="tbl_001",
        section_name_canonical=section,
        page=page,
        rows=table_rows,
        footnote_refs=[],
    )


def _footnote(marker: str, text: str, section: str = "income_statement") -> FootnoteElement:
    return FootnoteElement(
        footnote_id=f"fn_{marker}",
        marker=marker,
        text=text,
        section_name_canonical=section,
        page=4,
        anchors=[],
    )


def _request(
    kpi_id: str = "rev_001",
    kpi_name: str = "Total Revenue",
    aliases: list[str] | None = None,
    definition: str = "Total revenue.",
) -> Tier3KPIRequest:
    return Tier3KPIRequest(
        kpi_id=kpi_id,
        kpi_name=kpi_name,
        aliases=aliases or [],
        definition=definition,
    )


def _ledger_for(entries: list[TaxonomyEntry], fiscal_year: str = "FY2025") -> ExtractionLedger:
    return initialize_extraction_ledger(entries, fiscal_year)


# ---------------------------------------------------------------------------
# _table_to_markdown
# ---------------------------------------------------------------------------

class TestTableToMarkdown:

    def test_basic_table(self):
        table = _table([("Revenue", {"FY2025": "1000", "FY2024": "900"})])
        md = _table_to_markdown(table)
        assert "Revenue" in md
        assert "1000" in md
        assert "FY2025" in md

    def test_empty_table_returns_empty(self):
        table = TableElement(
            table_id="t", section_name_canonical="x", page=1, rows=[], footnote_refs=[]
        )
        assert _table_to_markdown(table) == ""

    def test_has_markdown_header_row(self):
        table = _table([("Revenue", {"FY2025": "1000"})])
        md = _table_to_markdown(table)
        lines = md.splitlines()
        assert any("|" in line for line in lines)

    def test_has_separator_row(self):
        table = _table([("Revenue", {"FY2025": "1000"})])
        md = _table_to_markdown(table)
        assert "---" in md

    def test_caption_and_page_in_output(self):
        table = _table([("Revenue", {"FY2025": "1000"})])
        table = table.model_copy(update={"caption": "Income Statement", "page": 7})
        md = _table_to_markdown(table)
        assert "Income Statement" in md
        assert "7" in md

    def test_multiple_rows(self):
        table = _table([
            ("Revenue", {"FY2025": "1000"}),
            ("PAT", {"FY2025": "200"}),
        ])
        md = _table_to_markdown(table)
        assert "Revenue" in md
        assert "PAT" in md

    def test_missing_cell_handled(self):
        """Row with no FY2025 cell — column value should be empty string."""
        row1 = TableRow(
            row_label="Revenue",
            cells=[TableCell(column_label="FY2025", value="1000", footnote_refs=[])],
        )
        row2 = TableRow(
            row_label="PAT",
            cells=[TableCell(column_label="FY2024", value="200", footnote_refs=[])],
        )
        table = TableElement(
            table_id="t", section_name_canonical="income_statement", page=3,
            rows=[row1, row2], footnote_refs=[],
        )
        md = _table_to_markdown(table)
        assert "Revenue" in md
        assert "PAT" in md


# ---------------------------------------------------------------------------
# build_section_context_for_prompt
# ---------------------------------------------------------------------------

class TestBuildSectionContextForPrompt:

    def test_includes_narrative(self):
        section = _section(markdown="The company reported strong revenue growth.")
        ctx = build_section_context_for_prompt(section)
        assert "strong revenue growth" in ctx

    def test_empty_narrative_not_included(self):
        section = _section(markdown="   ")
        ctx = build_section_context_for_prompt(section)
        assert ctx.strip() == "" or "Table" not in ctx

    def test_includes_table_markdown(self):
        table = _table([("Total Revenue", {"FY2025": "12000"})])
        section = _section(tables=[table])
        ctx = build_section_context_for_prompt(section)
        assert "Total Revenue" in ctx
        assert "12000" in ctx

    def test_includes_footnotes(self):
        fn = _footnote("1", "As restated for accounting policy change.")
        section = _section(footnotes=[fn])
        ctx = build_section_context_for_prompt(section)
        assert "restated" in ctx
        assert "[1]" in ctx

    def test_includes_chart_interpretation(self):
        chart = ChartElement(
            chart_id="c1",
            caption="Revenue Chart",
            section_name_canonical="income_statement",
            page=5,
            image_ref="img/c1.png",
            interpretation="Revenue grew 12% YoY to 12,000.",
            interpretation_confidence=0.9,
            footnote_refs=[],
        )
        section = _section(charts=[chart])
        ctx = build_section_context_for_prompt(section)
        assert "Revenue Chart" in ctx
        assert "12,000" in ctx

    def test_empty_chart_interpretation_skipped(self):
        chart = ChartElement(
            chart_id="c1",
            caption="Empty Chart",
            section_name_canonical="income_statement",
            page=5,
            image_ref="img/c1.png",
            interpretation="",
            interpretation_confidence=0.0,
            footnote_refs=[],
        )
        section = _section(charts=[chart])
        ctx = build_section_context_for_prompt(section)
        assert "Empty Chart" not in ctx

    def test_empty_section_returns_empty(self):
        section = _section(markdown="")
        ctx = build_section_context_for_prompt(section)
        assert ctx == ""

    def test_parts_separated(self):
        table = _table([("Revenue", {"FY2025": "1000"})])
        fn = _footnote("1", "Restated.")
        section = _section(markdown="Some narrative.", tables=[table], footnotes=[fn])
        ctx = build_section_context_for_prompt(section)
        assert "narrative" in ctx
        assert "Revenue" in ctx
        assert "Restated" in ctx


# ---------------------------------------------------------------------------
# _format_kpi_list
# ---------------------------------------------------------------------------

class TestFormatKpiList:

    def test_single_kpi(self):
        req = _request("rev_001", "Total Revenue", ["Net Sales"])
        text = _format_kpi_list([req])
        assert "1." in text
        assert "Total Revenue" in text
        assert "Net Sales" in text
        assert "rev_001" in text

    def test_two_kpis_numbered(self):
        reqs = [_request("rev_001", "Revenue"), _request("pat_001", "Net Profit")]
        text = _format_kpi_list(reqs)
        assert "1." in text
        assert "2." in text

    def test_no_aliases_shows_none(self):
        req = _request("rev_001", "Revenue", aliases=[])
        text = _format_kpi_list([req])
        assert "none" in text

    def test_empty_list_returns_empty(self):
        assert _format_kpi_list([]) == ""


# ---------------------------------------------------------------------------
# run_llm_extraction_for_section — mocked LLM
# ---------------------------------------------------------------------------

class TestRunTier3ForSection:

    def _mock_chain_with(self, extractions: list[Tier3ExtractionResult]):
        from llm_extractor.extraction import _Tier3BatchOutput
        batch = _Tier3BatchOutput(extractions=extractions)
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = batch
        return mock_chain

    @patch("llm_extractor.extraction.get_llm_client")
    @patch("llm_extractor.extraction.TIER3_EXTRACTION_PROMPT")
    def test_happy_path_returns_results(self, mock_prompt, mock_get_llm):
        result = Tier3ExtractionResult(
            kpi_id="rev_001", found=True, value="12000", confidence=0.9,
            source_element_type="table_cell",
        )
        with patch("llm_extractor.extraction.TIER3_EXTRACTION_PROMPT") as mock_p:
            chain = self._mock_chain_with([result])
            mock_p.__or__ = lambda self, other: chain
            results = run_llm_extraction_for_section(
                _section(), [_request()], "FY2025"
            )
        assert len(results) == 1
        assert results[0].kpi_id == "rev_001"
        assert results[0].value == "12000"

    def test_empty_requests_returns_empty(self):
        # No LLM call at all
        results = run_llm_extraction_for_section(_section(), [], "FY2025")
        assert results == []

    @patch("llm_extractor.extraction.get_llm_client")
    @patch("llm_extractor.extraction.TIER3_EXTRACTION_PROMPT")
    def test_llm_failure_returns_all_not_found(self, mock_prompt, mock_get_llm):
        with patch("llm_extractor.extraction.TIER3_EXTRACTION_PROMPT") as mock_p:
            chain = MagicMock()
            chain.invoke.side_effect = RuntimeError("LLM down")
            mock_p.__or__ = lambda self, other: chain
            results = run_llm_extraction_for_section(
                _section(),
                [_request("rev_001"), _request("pat_001", "Profit After Tax")],
                "FY2025",
            )
        assert len(results) == 2
        assert all(not r.found for r in results)
        assert {r.kpi_id for r in results} == {"rev_001", "pat_001"}

    @patch("llm_extractor.extraction.get_llm_client")
    @patch("llm_extractor.extraction.TIER3_EXTRACTION_PROMPT")
    def test_batch_call_invoked_once(self, mock_prompt, mock_get_llm):
        """One LLM call per section, regardless of number of KPIs."""
        result1 = Tier3ExtractionResult(kpi_id="rev_001", found=True, value="1000")
        result2 = Tier3ExtractionResult(kpi_id="pat_001", found=False)
        with patch("llm_extractor.extraction.TIER3_EXTRACTION_PROMPT") as mock_p:
            chain = self._mock_chain_with([result1, result2])
            mock_p.__or__ = lambda self, other: chain
            run_llm_extraction_for_section(
                _section(),
                [_request("rev_001"), _request("pat_001", "Profit After Tax")],
                "FY2025",
            )
        assert chain.invoke.call_count == 1


# ---------------------------------------------------------------------------
# run_llm_extraction — mocked run_llm_extraction_for_section
# ---------------------------------------------------------------------------

class TestRunTier3:

    @patch("llm_extractor.extraction.run_llm_extraction_for_section")
    def test_already_found_entries_skipped(self, mock_section_fn):
        entry = _entry()
        ledger = _ledger_for([entry])
        ledger.records["rev_001"].status = "found"
        ledger.records["rev_001"].value = "PRE_EXISTING"
        run_llm_extraction(ledger, [entry], [_section()], "FY2025")
        mock_section_fn.assert_not_called()

    @patch("llm_extractor.extraction.run_llm_extraction_for_section")
    def test_no_sections_skips_llm(self, mock_section_fn):
        entry = _entry()
        ledger = _ledger_for([entry])
        run_llm_extraction(ledger, [entry], [], "FY2025")
        mock_section_fn.assert_not_called()

    @patch("llm_extractor.extraction.run_llm_extraction_for_section")
    def test_llm_not_found_stays_not_found(self, mock_section_fn):
        mock_section_fn.return_value = [
            Tier3ExtractionResult(kpi_id="rev_001", found=False)
        ]
        entry = _entry()
        ledger = _ledger_for([entry])
        result = run_llm_extraction(ledger, [entry], [_section()], "FY2025")
        assert result.records["rev_001"].status == "not_found"

    @patch("llm_extractor.extraction.run_llm_extraction_for_section")
    def test_llm_not_found_records_attempt(self, mock_section_fn):
        mock_section_fn.return_value = [
            Tier3ExtractionResult(kpi_id="rev_001", found=False)
        ]
        entry = _entry()
        ledger = _ledger_for([entry])
        result = run_llm_extraction(ledger, [entry], [_section()], "FY2025")
        rec = result.records["rev_001"]
        assert any(a.tier == "llm" for a in rec.attempts)
        assert any(a.outcome == "not_found" for a in rec.attempts)

    @patch("llm_extractor.extraction.run_llm_extraction_for_section")
    def test_found_high_confidence_sets_found(self, mock_section_fn):
        mock_section_fn.return_value = [
            Tier3ExtractionResult(kpi_id="rev_001", found=True, value="12000", confidence=0.9)
        ]
        entry = _entry()
        ledger = _ledger_for([entry])
        result = run_llm_extraction(ledger, [entry], [_section()], "FY2025")
        rec = result.records["rev_001"]
        assert rec.status == "found"
        assert rec.method == "llm"
        assert rec.value == "12000"

    @patch("llm_extractor.extraction.run_llm_extraction_for_section")
    def test_found_low_confidence_routes_to_review(self, mock_section_fn):
        mock_section_fn.return_value = [
            Tier3ExtractionResult(kpi_id="rev_001", found=True, value="12000", confidence=0.3)
        ]
        entry = _entry()
        ledger = _ledger_for([entry])
        result = run_llm_extraction(ledger, [entry], [_section()], "FY2025")
        rec = result.records["rev_001"]
        assert rec.status == "needs_human_review"
        assert rec.review_reason == "low_confidence"

    @patch("llm_extractor.extraction.run_llm_extraction_for_section")
    def test_found_records_llm_attempt(self, mock_section_fn):
        mock_section_fn.return_value = [
            Tier3ExtractionResult(kpi_id="rev_001", found=True, value="5000", confidence=0.8)
        ]
        entry = _entry()
        ledger = _ledger_for([entry])
        result = run_llm_extraction(ledger, [entry], [_section()], "FY2025")
        rec = result.records["rev_001"]
        assert len(rec.attempts) == 1
        assert rec.attempts[0].tier == "llm"
        assert rec.attempts[0].outcome == "found"

    @patch("llm_extractor.extraction.run_llm_extraction_for_section")
    def test_footnote_ids_propagated(self, mock_section_fn):
        mock_section_fn.return_value = [
            Tier3ExtractionResult(
                kpi_id="rev_001", found=True, value="5000", confidence=0.9,
                footnote_ids=["1", "a"],
            )
        ]
        entry = _entry()
        ledger = _ledger_for([entry])
        result = run_llm_extraction(ledger, [entry], [_section()], "FY2025")
        assert "1" in result.records["rev_001"].footnotes
        assert "a" in result.records["rev_001"].footnotes

    @patch("llm_extractor.extraction.resolve_cross_section_discrepancy")
    @patch("llm_extractor.extraction.run_llm_extraction_for_section")
    def test_first_section_wins_subsequent_skipped(self, mock_section_fn, mock_resolve):
        """Once a KPI is found in section 1, section 2 does not receive it in
        its prompt — only truly unresolved KPIs appear in each LLM call."""
        s1 = _section("income_statement")
        s2 = _section("management_discussion")

        def section_results(section, requests, fiscal_year):
            if section.section_name_canonical == "income_statement":
                return [Tier3ExtractionResult(kpi_id="rev_001", found=True, value="12000", confidence=0.9)]
            # management_discussion should never be called for rev_001
            return [Tier3ExtractionResult(kpi_id="rev_001", found=True, value="11950", confidence=0.85)]

        mock_section_fn.side_effect = section_results

        entry = _entry(canonical_sections=["income_statement", "management_discussion"])
        ledger = _ledger_for([entry])
        result = run_llm_extraction(ledger, [entry], [s1, s2], "FY2025")

        # Only one LLM call — management_discussion was skipped after income_statement resolved it.
        assert mock_section_fn.call_count == 1
        assert mock_section_fn.call_args[0][0].section_name_canonical == "income_statement"
        # No discrepancy call since only one value was ever collected.
        mock_resolve.assert_not_called()
        assert result.records["rev_001"].status == "found"
        assert result.records["rev_001"].value == "12000"

    @patch("llm_extractor.extraction.run_llm_extraction_for_section")
    def test_kpi_with_no_matching_sections_stays_not_found(self, mock_section_fn):
        entry = _entry(canonical_sections=["balance_sheet"])
        ledger = _ledger_for([entry])
        # Only income_statement section provided, not balance_sheet
        run_llm_extraction(ledger, [entry], [_section("income_statement")], "FY2025")
        mock_section_fn.assert_not_called()
        assert ledger.records["rev_001"].status == "not_found"

    @patch("llm_extractor.extraction.run_llm_extraction_for_section")
    def test_batching_by_section_one_call_per_section(self, mock_section_fn):
        """Two KPIs share the same canonical section → one LLM call, not two."""
        mock_section_fn.return_value = [
            Tier3ExtractionResult(kpi_id="rev_001", found=True, value="1000", confidence=0.9),
            Tier3ExtractionResult(kpi_id="pat_001", found=True, value="200", confidence=0.8),
        ]
        e1 = _entry("rev_001", "Total Revenue", canonical_sections=["income_statement"])
        e2 = _entry("pat_001", "Profit After Tax", canonical_sections=["income_statement"])
        ledger = _ledger_for([e1, e2])
        run_llm_extraction(ledger, [e1, e2], [_section("income_statement")], "FY2025")
        assert mock_section_fn.call_count == 1
        # Both KPIs passed in same call
        call_requests = mock_section_fn.call_args[0][1]
        kpi_ids_in_call = {r.kpi_id for r in call_requests}
        assert "rev_001" in kpi_ids_in_call
        assert "pat_001" in kpi_ids_in_call

    @patch("llm_extractor.extraction.run_llm_extraction_for_section")
    def test_two_sections_same_value_found(self, mock_section_fn):
        """First section finds the KPI → second section is skipped → status=found."""
        mock_section_fn.return_value = [
            Tier3ExtractionResult(kpi_id="rev_001", found=True, value="12000", confidence=0.9)
        ]
        entry = _entry(canonical_sections=["income_statement", "financial_highlights"])
        s1 = _section("income_statement")
        s2 = _section("financial_highlights")
        ledger = _ledger_for([entry])
        result = run_llm_extraction(ledger, [entry], [s1, s2], "FY2025")
        # financial_highlights is skipped because income_statement already resolved rev_001.
        assert mock_section_fn.call_count == 1
        assert result.records["rev_001"].status == "found"
        assert result.records["rev_001"].value == "12000"

    @patch("llm_extractor.extraction.run_llm_extraction_for_section")
    def test_multiple_kpis_independent(self, mock_section_fn):
        mock_section_fn.return_value = [
            Tier3ExtractionResult(kpi_id="rev_001", found=True, value="5000", confidence=0.9),
            Tier3ExtractionResult(kpi_id="pat_001", found=False),
        ]
        e1 = _entry("rev_001", "Total Revenue")
        e2 = _entry("pat_001", "Profit After Tax")
        ledger = _ledger_for([e1, e2])
        result = run_llm_extraction(ledger, [e1, e2], [_section()], "FY2025")
        assert result.records["rev_001"].status == "found"
        assert result.records["pat_001"].status == "not_found"
