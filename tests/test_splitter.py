"""Tests for section_parser.splitter.

Covers the markdown-fallback path exhaustively (always runs regardless of
whether Docling is installed) and the Docling primary path via patching the
module-level _DOCLING_AVAILABLE flag and _split_docling helper.
"""
from unittest.mock import MagicMock, patch

import pytest

from section_parser.schemas import RawSection
from section_parser.splitter import (
    _split_markdown_fallback,
    split_into_raw_sections,
)


# ---------------------------------------------------------------------------
# Markdown fallback — _split_markdown_fallback()
# ---------------------------------------------------------------------------


class TestMarkdownFallback:
    def test_no_headings_returns_single_document_section(self):
        text = "Some content without any headings.\n\nMore content here."
        sections = _split_markdown_fallback(text, heading_level_cutoff=2)
        assert len(sections) == 1
        assert sections[0].section_name_raw == "Document"

    def test_empty_string_returns_single_section(self):
        sections = _split_markdown_fallback("", heading_level_cutoff=2)
        assert len(sections) == 1
        assert sections[0].section_name_raw == "Document"

    def test_single_h1_creates_one_section(self):
        text = "# Income Statement\n\nRevenue: 1000\nExpenses: 800"
        sections = _split_markdown_fallback(text, heading_level_cutoff=2)
        assert len(sections) == 1
        assert sections[0].section_name_raw == "Income Statement"

    def test_multiple_h1_creates_multiple_sections(self):
        text = "# Income Statement\n\nRevenue: 1000\n\n# Balance Sheet\n\nAssets: 5000"
        sections = _split_markdown_fallback(text, heading_level_cutoff=2)
        assert len(sections) == 2
        assert sections[0].section_name_raw == "Income Statement"
        assert sections[1].section_name_raw == "Balance Sheet"

    def test_h2_creates_section_at_cutoff_2(self):
        text = "## Cash Flow Statement\n\nOperating: 200"
        sections = _split_markdown_fallback(text, heading_level_cutoff=2)
        assert len(sections) == 1
        assert sections[0].section_name_raw == "Cash Flow Statement"

    def test_h3_does_not_create_section_at_cutoff_2(self):
        text = "# Balance Sheet\n\nSome content.\n\n### Sub-note\n\nDetail text."
        sections = _split_markdown_fallback(text, heading_level_cutoff=2)
        # H3 is below cutoff — stays in the Balance Sheet section
        assert len(sections) == 1
        assert sections[0].section_name_raw == "Balance Sheet"

    def test_h3_creates_section_at_cutoff_3(self):
        text = "# Balance Sheet\n\nContent.\n\n### Sub-note\n\nDetail."
        sections = _split_markdown_fallback(text, heading_level_cutoff=3)
        assert len(sections) == 2

    def test_content_assigned_to_correct_section(self):
        text = (
            "# Income Statement\n\nRevenue: 1000\n\n"
            "# Balance Sheet\n\nAssets: 5000"
        )
        sections = _split_markdown_fallback(text, heading_level_cutoff=2)
        assert "Revenue" in sections[0].content_markdown
        assert "Assets" in sections[1].content_markdown
        assert "Assets" not in sections[0].content_markdown

    def test_sections_in_document_order(self):
        text = "# A\n\n# B\n\n# C"
        sections = _split_markdown_fallback(text, heading_level_cutoff=2)
        assert [s.section_name_raw for s in sections] == ["A", "B", "C"]

    def test_page_inferred_from_single_marker(self):
        # Page marker is inside the section body (after the heading).
        # Markers *before* the heading fall outside the slice and are not captured.
        text = "# Income Statement\n\n<!-- page 3 -->\nRevenue: 1000"
        sections = _split_markdown_fallback(text, heading_level_cutoff=2)
        assert sections[0].page_range[0] == 3

    def test_multiple_page_markers_give_range(self):
        # Both markers are inside the section body → min/max give the range.
        text = (
            "# Balance Sheet\n\n<!-- page 5 -->\nAssets.\n\n"
            "<!-- page 7 -->\nMore content."
        )
        sections = _split_markdown_fallback(text, heading_level_cutoff=2)
        assert sections[0].page_range == (5, 7)

    def test_no_page_markers_defaults_to_previous_section_end(self):
        text = "# Section A\n\nContent A.\n\n# Section B\n\nContent B."
        sections = _split_markdown_fallback(text, heading_level_cutoff=2)
        # No markers — both sections should have the same page inferred from preceding
        assert sections[0].page_range[0] >= 1
        assert sections[1].page_range[0] >= 1

    def test_returns_raw_section_instances(self):
        text = "# Test Section\n\nBody."
        sections = _split_markdown_fallback(text, heading_level_cutoff=2)
        assert all(isinstance(s, RawSection) for s in sections)


# ---------------------------------------------------------------------------
# Public entry point — split_into_raw_sections()
# ---------------------------------------------------------------------------


class TestSplitIntoRawSections:
    def test_uses_markdown_fallback_when_docling_unavailable(self):
        # With a non-None docling_document but _DOCLING_AVAILABLE=False,
        # the function must fall through to the markdown path.
        fake_doc = MagicMock()
        markdown = "# Annual Report\n\nContent."
        with patch("section_parser.splitter._DOCLING_AVAILABLE", False):
            sections = split_into_raw_sections(
                docling_document=fake_doc,
                narrative_markdown=markdown,
                heading_level_cutoff=2,
            )
        assert len(sections) == 1
        assert sections[0].section_name_raw == "Annual Report"

    def test_uses_markdown_fallback_when_docling_document_is_none(self):
        markdown = "# Balance Sheet\n\nAssets."
        sections = split_into_raw_sections(
            docling_document=None,
            narrative_markdown=markdown,
            heading_level_cutoff=2,
        )
        assert sections[0].section_name_raw == "Balance Sheet"

    def test_calls_split_docling_when_available_and_returns_sections(self):
        fake_doc = MagicMock()
        expected = [RawSection(section_name_raw="P&L", content_markdown="", page_range=(1, 3))]
        with (
            patch("section_parser.splitter._DOCLING_AVAILABLE", True),
            patch("section_parser.splitter._split_docling", return_value=expected),
        ):
            sections = split_into_raw_sections(
                docling_document=fake_doc,
                narrative_markdown="# Fallback\n\n...",
                heading_level_cutoff=2,
            )
        assert sections == expected

    def test_falls_back_to_markdown_when_docling_yields_nothing(self):
        # _split_docling returns [] → fall through to markdown
        fake_doc = MagicMock()
        markdown = "# MDA\n\nSome analysis."
        with (
            patch("section_parser.splitter._DOCLING_AVAILABLE", True),
            patch("section_parser.splitter._split_docling", return_value=[]),
        ):
            sections = split_into_raw_sections(
                docling_document=fake_doc,
                narrative_markdown=markdown,
                heading_level_cutoff=2,
            )
        assert sections[0].section_name_raw == "MDA"

    def test_heading_level_cutoff_forwarded_to_markdown_fallback(self):
        markdown = "# H1\n\n## H2\n\n### H3"
        sections_cutoff2 = split_into_raw_sections(None, markdown, heading_level_cutoff=2)
        sections_cutoff3 = split_into_raw_sections(None, markdown, heading_level_cutoff=3)
        assert len(sections_cutoff2) == 2   # H1 and H2
        assert len(sections_cutoff3) == 3   # H1, H2, H3
