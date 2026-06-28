"""Tests for section_parser.element_assignment.

Uses synthetic fixture data from tests/fixtures/ and inline objects to cover:
  - Page-range containment (inside, at boundaries)
  - Gap fallback (nearest preceding section)
  - Before-first-section fallback (first section)
  - After-all-sections fallback (nearest preceding = last section)
  - Empty inputs
  - Alignment metadata preserved in output Section objects
"""
import json
from pathlib import Path

import pytest

from common.schemas import (
    ChartElement,
    FootnoteElement,
    Section,
    TableElement,
    TableRow,
    TableCell,
    FootnoteAnchor,
)
from section_parser.element_assignment import assign_elements_to_sections
from section_parser.schemas import RawSection, SectionAlignmentResult

FIXTURE_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw(name: str, start: int, end: int) -> RawSection:
    return RawSection(
        section_name_raw=name,
        content_markdown=f"Content of {name}.",
        page_range=(start, end),
    )


def _alignment(canonical: str, confidence: float = 0.95,
               source: str = "fuzzy_match") -> SectionAlignmentResult:
    return SectionAlignmentResult(
        section_name_canonical=canonical,
        confidence=confidence,
        source=source,
    )


def _table(table_id: str, page: int) -> TableElement:
    return TableElement(
        table_id=table_id,
        section_name_canonical="",
        page=page,
        rows=[TableRow(row_label="Row1", cells=[TableCell(column_label="FY2025", value="100", footnote_refs=[])])],
        footnote_refs=[],
    )


def _chart(chart_id: str, page: int) -> ChartElement:
    return ChartElement(
        chart_id=chart_id,
        section_name_canonical="",
        page=page,
        image_ref="img.png",
        interpretation="A chart.",
        interpretation_confidence=0.9,
        footnote_refs=[],
    )


def _footnote(fn_id: str, page: int) -> FootnoteElement:
    return FootnoteElement(
        footnote_id=fn_id,
        marker="1",
        text="Footnote text.",
        section_name_canonical="",
        page=page,
        anchors=[],
    )


# Three sections covering pages 1-3, 5-8, 10-12 (gap at 4 and 9, beyond at 13+)
_SECTIONS = [
    _raw("Income Statement", 1, 3),
    _raw("Balance Sheet", 5, 8),
    _raw("Cash Flows", 10, 12),
]
_ALIGNMENTS = [
    _alignment("Statement of Profit and Loss"),
    _alignment("Balance Sheet"),
    _alignment("Statement of Cash Flows"),
]


# ---------------------------------------------------------------------------
# Page-range containment tests
# ---------------------------------------------------------------------------


class TestPageContainment:
    def test_element_inside_range_assigned_to_correct_section(self):
        tables = [_table("t1", page=2)]  # page 2 is inside section 0 (1-3)
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, tables, [], [])
        assert len(sections[0].tables) == 1
        assert sections[0].tables[0].table_id == "t1"
        assert len(sections[1].tables) == 0

    def test_element_at_section_start_boundary(self):
        tables = [_table("t_start", page=5)]  # page 5 = start of section 1 (5-8)
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, tables, [], [])
        assert any(t.table_id == "t_start" for t in sections[1].tables)

    def test_element_at_section_end_boundary(self):
        tables = [_table("t_end", page=8)]  # page 8 = end of section 1 (5-8)
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, tables, [], [])
        assert any(t.table_id == "t_end" for t in sections[1].tables)

    def test_charts_and_footnotes_assigned_by_page(self):
        charts = [_chart("c1", page=7)]    # page 7 inside section 1 (5-8)
        footnotes = [_footnote("f1", page=11)]  # page 11 inside section 2 (10-12)
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, [], charts, footnotes)
        assert len(sections[1].charts) == 1
        assert len(sections[2].footnotes) == 1


# ---------------------------------------------------------------------------
# Fallback assignment tests
# ---------------------------------------------------------------------------


class TestFallbackAssignment:
    def test_element_in_gap_assigned_to_nearest_preceding(self):
        # Page 9: gap between section 1 (5-8) and section 2 (10-12)
        # Nearest preceding = section 1 (end page 8 ≤ 9)
        tables = [_table("t_gap", page=9)]
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, tables, [], [])
        assert any(t.table_id == "t_gap" for t in sections[1].tables)
        assert len(sections[2].tables) == 0

    def test_element_after_all_sections_assigned_to_last(self):
        # Page 13: after all sections (last section ends at 12)
        charts = [_chart("c_late", page=13)]
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, [], charts, [])
        assert any(c.chart_id == "c_late" for c in sections[2].charts)

    def test_element_before_all_sections_assigned_to_first(self):
        # Page 0: before any section (first starts at page 1)
        footnotes = [_footnote("f_early", page=0)]
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, [], [], footnotes)
        assert any(f.footnote_id == "f_early" for f in sections[0].footnotes)

    def test_element_at_gap_page_4_assigned_to_section_0(self):
        # Page 4: gap between section 0 (1-3) and section 1 (5-8)
        # Nearest preceding = section 0 (end page 3 ≤ 4)
        tables = [_table("t_gap4", page=4)]
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, tables, [], [])
        assert any(t.table_id == "t_gap4" for t in sections[0].tables)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_sections_returns_empty_list(self):
        tables = [_table("t1", page=5)]
        result = assign_elements_to_sections([], [], tables, [], [])
        assert result == []

    def test_empty_elements_returns_sections_with_empty_element_lists(self):
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, [], [], [])
        assert len(sections) == 3
        for s in sections:
            assert s.tables == []
            assert s.charts == []
            assert s.footnotes == []

    def test_multiple_tables_to_same_section(self):
        tables = [_table("t_a", page=1), _table("t_b", page=2), _table("t_c", page=3)]
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, tables, [], [])
        assert len(sections[0].tables) == 3

    def test_section_count_matches_input(self):
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, [], [], [])
        assert len(sections) == len(_SECTIONS)


# ---------------------------------------------------------------------------
# Alignment metadata preserved in output Section objects
# ---------------------------------------------------------------------------


class TestAlignmentMetadataPreserved:
    def test_section_name_raw_preserved(self):
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, [], [], [])
        assert sections[0].section_name_raw == "Income Statement"
        assert sections[1].section_name_raw == "Balance Sheet"

    def test_section_name_canonical_from_alignment(self):
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, [], [], [])
        assert sections[0].section_name_canonical == "Statement of Profit and Loss"
        assert sections[1].section_name_canonical == "Balance Sheet"

    def test_alignment_confidence_preserved(self):
        alignments = [_alignment("Statement of Profit and Loss", confidence=0.87)] + _ALIGNMENTS[1:]
        sections = assign_elements_to_sections(_SECTIONS, alignments, [], [], [])
        assert sections[0].alignment_confidence == pytest.approx(0.87)

    def test_alignment_source_preserved(self):
        alignments = [_alignment("Statement of Profit and Loss", source="llm_fallback")] + _ALIGNMENTS[1:]
        sections = assign_elements_to_sections(_SECTIONS, alignments, [], [], [])
        assert sections[0].alignment_source == "llm_fallback"

    def test_best_guess_unresolved_source_preserved(self):
        alignments = [
            _alignment("Statement of Profit and Loss", confidence=0.10, source="best_guess_unresolved")
        ] + _ALIGNMENTS[1:]
        sections = assign_elements_to_sections(_SECTIONS, alignments, [], [], [])
        assert sections[0].alignment_source == "best_guess_unresolved"

    def test_page_range_preserved(self):
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, [], [], [])
        assert sections[0].page_range == (1, 3)
        assert sections[1].page_range == (5, 8)
        assert sections[2].page_range == (10, 12)

    def test_content_markdown_preserved(self):
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, [], [], [])
        assert "Income Statement" in sections[0].content_markdown

    def test_section_order_preserved(self):
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, [], [], [])
        names = [s.section_name_raw for s in sections]
        assert names == ["Income Statement", "Balance Sheet", "Cash Flows"]

    def test_output_is_section_instances(self):
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, [], [], [])
        assert all(isinstance(s, Section) for s in sections)


# ---------------------------------------------------------------------------
# Fixture-based smoke test
# ---------------------------------------------------------------------------


class TestSyntheticFixtures:
    def test_loads_and_assigns_synthetic_tables(self):
        data = json.loads((FIXTURE_DIR / "synthetic_tables.json").read_text())
        tables = [TableElement.model_validate(t) for t in data]
        # Sections: 1-3, 5-8, 10-12. Table pages: 2, 5, 9
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, tables, [], [])
        # tbl_001 page=2 → section 0; tbl_002 page=5 → section 1; tbl_003 page=9 → section 1 (gap fallback)
        assert any(t.table_id == "tbl_001" for t in sections[0].tables)
        assert any(t.table_id == "tbl_002" for t in sections[1].tables)
        assert any(t.table_id == "tbl_003" for t in sections[1].tables)

    def test_loads_and_assigns_synthetic_charts(self):
        data = json.loads((FIXTURE_DIR / "synthetic_charts.json").read_text())
        charts = [ChartElement.model_validate(c) for c in data]
        # Chart pages: 7, 13. Page 7 → section 1 (5-8); page 13 → section 2 (nearest preceding)
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, [], charts, [])
        assert any(c.chart_id == "chrt_001" for c in sections[1].charts)
        assert any(c.chart_id == "chrt_002" for c in sections[2].charts)

    def test_loads_and_assigns_synthetic_footnotes(self):
        data = json.loads((FIXTURE_DIR / "synthetic_footnotes.json").read_text())
        footnotes = [FootnoteElement.model_validate(f) for f in data]
        # Footnote pages: 1, 10. Page 1 → section 0; page 10 → section 2
        sections = assign_elements_to_sections(_SECTIONS, _ALIGNMENTS, [], [], footnotes)
        assert any(f.footnote_id == "fn_1" for f in sections[0].footnotes)
        assert any(f.footnote_id == "fn_2" for f in sections[2].footnotes)
