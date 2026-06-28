"""Unit tests for vector_indexer.chunking.

All tests are pure in-memory — no DB, no LLM, no network.  The chunking
functions are deterministic given their input, so there is nothing to mock.
"""
from __future__ import annotations

from common.schemas import (
    ChartElement,
    FootnoteAnchor,
    FootnoteElement,
    Section,
    TableCell,
    TableElement,
    TableRow,
)
from vector_indexer.chunking import (
    chunk_chart,
    chunk_footnote,
    chunk_narrative_text,
    chunk_section,
    chunk_table,
)
from vector_indexer.schemas import TextChunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_section(
    content_markdown: str = "",
    tables: list = None,
    charts: list = None,
    footnotes: list = None,
    page_range: tuple = (1, 2),
) -> Section:
    return Section(
        section_name_raw="Income Statement",
        section_name_canonical="Statement of Profit and Loss",
        alignment_confidence=0.95,
        alignment_source="fuzzy_match",
        content_markdown=content_markdown,
        tables=tables or [],
        charts=charts or [],
        footnotes=footnotes or [],
        page_range=page_range,
    )


def _make_table(rows_data: list[tuple[str, list[tuple[str, str]]]]) -> TableElement:
    """rows_data: [(row_label, [(col_label, value), ...]), ...]"""
    rows = [
        TableRow(
            row_label=row_label,
            cells=[TableCell(column_label=col, value=val, footnote_refs=[]) for col, val in cells],
        )
        for row_label, cells in rows_data
    ]
    return TableElement(
        table_id="tbl-001",
        section_name_canonical="Statement of Profit and Loss",
        page=3,
        rows=rows,
        footnote_refs=[],
    )


def _make_chart(interpretation: str) -> ChartElement:
    return ChartElement(
        chart_id="chart-001",
        section_name_canonical="Management Discussion and Analysis",
        page=7,
        image_ref="images/chart001.png",
        interpretation=interpretation,
        interpretation_confidence=0.88,
        footnote_refs=[],
    )


def _make_footnote(text: str) -> FootnoteElement:
    return FootnoteElement(
        footnote_id="fn-001",
        marker="1",
        text=text,
        section_name_canonical="Statement of Profit and Loss",
        page=4,
        anchors=[FootnoteAnchor(element_type="table_cell", element_id="tbl-001", location="cell(0,1)")],
    )


# ---------------------------------------------------------------------------
# chunk_narrative_text
# ---------------------------------------------------------------------------

class TestChunkNarrativeText:
    def test_empty_content_returns_no_chunks(self):
        section = _make_section(content_markdown="   ")
        assert chunk_narrative_text(section) == []

    def test_short_content_returns_one_chunk(self):
        section = _make_section(content_markdown="Revenue grew 10%.")
        chunks = chunk_narrative_text(section)
        assert len(chunks) == 1
        assert chunks[0].chunk_text == "Revenue grew 10%."

    def test_chunk_source_type_is_text(self):
        section = _make_section(content_markdown="Revenue grew 10%.")
        chunk = chunk_narrative_text(section)[0]
        assert chunk.source_element_type == "text"

    def test_chunk_carries_section_name_canonical(self):
        section = _make_section(content_markdown="Some narrative.")
        chunk = chunk_narrative_text(section)[0]
        assert chunk.section_name_canonical == "Statement of Profit and Loss"

    def test_chunk_carries_page_range(self):
        section = _make_section(content_markdown="Text.", page_range=(5, 8))
        chunk = chunk_narrative_text(section)[0]
        assert chunk.page_range == (5, 8)

    def test_long_content_produces_multiple_chunks(self):
        # CHUNK_SIZE=500 chars; build a 1500-char block to force splitting
        long_text = ("Revenue grew significantly year over year. " * 40).strip()
        assert len(long_text) > 1000
        section = _make_section(content_markdown=long_text)
        chunks = chunk_narrative_text(section)
        assert len(chunks) > 1

    def test_all_chunks_are_text_chunks(self):
        long_text = "Word. " * 200
        section = _make_section(content_markdown=long_text)
        chunks = chunk_narrative_text(section)
        assert all(isinstance(c, TextChunk) for c in chunks)

    def test_no_empty_chunks(self):
        section = _make_section(content_markdown="\n\nSome text.\n\n\n")
        chunks = chunk_narrative_text(section)
        assert all(c.chunk_text.strip() for c in chunks)


# ---------------------------------------------------------------------------
# chunk_table
# ---------------------------------------------------------------------------

class TestChunkTable:
    def test_one_chunk_per_row(self):
        table = _make_table([
            ("Revenue", [("FY2025", "1000"), ("FY2024", "900")]),
            ("Net Profit", [("FY2025", "200"), ("FY2024", "180")]),
        ])
        chunks = chunk_table(table)
        assert len(chunks) == 2

    def test_chunk_text_contains_row_label(self):
        table = _make_table([("Revenue", [("FY2025", "1000")])])
        chunk = chunk_table(table)[0]
        assert "Revenue" in chunk.chunk_text

    def test_chunk_text_contains_column_label_and_value(self):
        table = _make_table([("Revenue", [("FY2025", "1000")])])
        chunk = chunk_table(table)[0]
        assert "FY2025" in chunk.chunk_text
        assert "1000" in chunk.chunk_text

    def test_multiple_cells_joined_with_semicolon(self):
        table = _make_table([("Revenue", [("FY2025", "1000"), ("FY2024", "900")])])
        chunk = chunk_table(table)[0]
        assert ";" in chunk.chunk_text

    def test_source_type_is_table_row(self):
        table = _make_table([("Revenue", [("FY2025", "1000")])])
        chunk = chunk_table(table)[0]
        assert chunk.source_element_type == "table_row"

    def test_element_ref_is_table_id(self):
        table = _make_table([("Revenue", [("FY2025", "1000")])])
        chunk = chunk_table(table)[0]
        assert chunk.element_ref == "tbl-001"

    def test_page_is_table_page(self):
        table = _make_table([("Revenue", [("FY2025", "1000")])])
        chunk = chunk_table(table)[0]
        assert chunk.page == 3

    def test_empty_table_produces_no_chunks(self):
        table = _make_table([])
        assert chunk_table(table) == []

    def test_row_with_no_cells_uses_row_label_only(self):
        table = TableElement(
            table_id="t1",
            section_name_canonical="Income Statement",
            page=1,
            rows=[TableRow(row_label="Subtotal", cells=[])],
            footnote_refs=[],
        )
        chunks = chunk_table(table)
        assert len(chunks) == 1
        assert chunks[0].chunk_text == "Subtotal"


# ---------------------------------------------------------------------------
# chunk_chart
# ---------------------------------------------------------------------------

class TestChunkChart:
    def test_non_empty_interpretation_returns_one_chunk(self):
        chart = _make_chart("Revenue grew 15% YoY driven by domestic expansion.")
        chunks = chunk_chart(chart)
        assert len(chunks) == 1

    def test_empty_interpretation_returns_no_chunks(self):
        chart = _make_chart("   ")
        assert chunk_chart(chart) == []

    def test_chunk_text_is_interpretation(self):
        interp = "Revenue grew 15% YoY."
        chunk = chunk_chart(_make_chart(interp))[0]
        assert chunk.chunk_text == interp

    def test_source_type_is_chart_interpretation(self):
        chunk = chunk_chart(_make_chart("Trend upward."))[0]
        assert chunk.source_element_type == "chart_interpretation"

    def test_element_ref_is_chart_id(self):
        chunk = chunk_chart(_make_chart("Trend upward."))[0]
        assert chunk.element_ref == "chart-001"

    def test_page_is_chart_page(self):
        chunk = chunk_chart(_make_chart("Trend upward."))[0]
        assert chunk.page == 7


# ---------------------------------------------------------------------------
# chunk_footnote
# ---------------------------------------------------------------------------

class TestChunkFootnote:
    def test_non_empty_text_returns_one_chunk(self):
        fn = _make_footnote("Amounts restated for discontinued operations.")
        chunks = chunk_footnote(fn)
        assert len(chunks) == 1

    def test_empty_text_returns_no_chunks(self):
        fn = _make_footnote("  ")
        assert chunk_footnote(fn) == []

    def test_chunk_text_is_footnote_text(self):
        text = "Amounts restated for discontinued operations."
        chunk = chunk_footnote(_make_footnote(text))[0]
        assert chunk.chunk_text == text

    def test_source_type_is_footnote(self):
        chunk = chunk_footnote(_make_footnote("Note 1."))[0]
        assert chunk.source_element_type == "footnote"

    def test_element_ref_is_footnote_id(self):
        chunk = chunk_footnote(_make_footnote("Note 1."))[0]
        assert chunk.element_ref == "fn-001"

    def test_page_is_footnote_page(self):
        chunk = chunk_footnote(_make_footnote("Note 1."))[0]
        assert chunk.page == 4


# ---------------------------------------------------------------------------
# chunk_section — integration of all four chunkers
# ---------------------------------------------------------------------------

class TestChunkSection:
    def test_empty_section_returns_no_chunks(self):
        section = _make_section()
        assert chunk_section(section) == []

    def test_all_element_types_produce_chunks(self):
        section = _make_section(
            content_markdown="Some narrative text.",
            tables=[_make_table([("Revenue", [("FY2025", "1000")])])],
            charts=[_make_chart("Chart shows upward trend.")],
            footnotes=[_make_footnote("Note about restatement.")],
        )
        chunks = chunk_section(section)
        types = {c.source_element_type for c in chunks}
        assert types == {"text", "table_row", "chart_interpretation", "footnote"}

    def test_total_chunk_count_is_sum_of_parts(self):
        table = _make_table([
            ("Revenue", [("FY2025", "1000")]),
            ("Net Profit", [("FY2025", "200")]),
        ])
        section = _make_section(
            content_markdown="Narrative.",
            tables=[table],
            charts=[_make_chart("Upward trend.")],
            footnotes=[_make_footnote("Note 1.")],
        )
        chunks = chunk_section(section)
        # 1 narrative + 2 table rows + 1 chart + 1 footnote = 5
        assert len(chunks) == 5

    def test_section_with_only_tables_has_no_text_chunks(self):
        section = _make_section(tables=[_make_table([("Rev", [("FY", "100")])])])
        chunks = chunk_section(section)
        assert all(c.source_element_type == "table_row" for c in chunks)
