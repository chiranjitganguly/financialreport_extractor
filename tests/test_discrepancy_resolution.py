"""Unit tests for common/discrepancy_resolution.py.

The real LLM is never called — the chain is patched at the
`resolve_cross_section_discrepancy` module boundary.
"""

import pytest
from unittest.mock import MagicMock, patch

from common.discrepancy_resolution import (
    _format_candidates,
    _format_sections_context,
    resolve_cross_section_discrepancy,
)
from common.schemas import (
    CandidateValue,
    ChartElement,
    FootnoteElement,
    Section,
    TableElement,
    TaxonomyEntry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def taxonomy_entry() -> TaxonomyEntry:
    return TaxonomyEntry(
        kpi_id="rev_001",
        kpi_name="Total Revenue",
        definition="Total revenue in the fiscal year.",
        canonical_sections=["income_statement", "financial_highlights"],
        applicable_industries=[],
        applicable_report_types=[],
        applicable_accounting_standards=[],
        aliases=["Net Sales"],
    )


@pytest.fixture
def two_candidates() -> list[CandidateValue]:
    return [
        CandidateValue(
            value="12000",
            section_name_canonical="income_statement",
            page=10,
            source_element_type="table_cell",
            footnotes=[],
            confidence=1.0,
        ),
        CandidateValue(
            value="11950",
            section_name_canonical="financial_highlights",
            page=3,
            source_element_type="text",
            footnotes=["a"],
            confidence=0.8,
        ),
    ]


@pytest.fixture
def two_sections() -> list[Section]:
    def _section(canonical: str, page_start: int) -> Section:
        return Section(
            section_name_raw=canonical.replace("_", " ").title(),
            section_name_canonical=canonical,
            alignment_confidence=0.95,
            alignment_source="fuzzy_match",
            content_markdown=f"Content for {canonical}.",
            tables=[],
            charts=[],
            footnotes=[],
            page_range=(page_start, page_start + 2),
        )

    return [_section("income_statement", 9), _section("financial_highlights", 2)]


# ---------------------------------------------------------------------------
# _format_candidates
# ---------------------------------------------------------------------------

class TestFormatCandidates:
    def test_includes_section_and_value(self, two_candidates):
        text = _format_candidates(two_candidates)
        assert "income_statement" in text
        assert "12000" in text
        assert "financial_highlights" in text
        assert "11950" in text

    def test_numbering_starts_at_one(self, two_candidates):
        text = _format_candidates(two_candidates)
        assert text.startswith("1.")

    def test_two_candidates_have_two_lines(self, two_candidates):
        lines = [l for l in _format_candidates(two_candidates).splitlines() if l.strip()]
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# _format_sections_context
# ---------------------------------------------------------------------------

class TestFormatSectionsContext:
    def test_contains_section_canonical_name(self, two_sections):
        text = _format_sections_context(two_sections)
        assert "income_statement" in text
        assert "financial_highlights" in text

    def test_contains_page_range(self, two_sections):
        text = _format_sections_context(two_sections)
        assert "9" in text

    def test_truncates_long_markdown(self):
        long_content = "x" * 3000
        section = Section(
            section_name_raw="Big Section",
            section_name_canonical="income_statement",
            alignment_confidence=0.9,
            alignment_source="fuzzy_match",
            content_markdown=long_content,
            tables=[],
            charts=[],
            footnotes=[],
            page_range=(1, 5),
        )
        text = _format_sections_context([section])
        # First 1500 chars of content should be present, not all 3000
        assert "income_statement" in text
        assert len(text) < len(long_content)


# ---------------------------------------------------------------------------
# resolve_cross_section_discrepancy — mocked LLM
# ---------------------------------------------------------------------------

class TestResolveDiscrepancy:

    def _mock_chain_result(self, chosen_value, chosen_section, source_type, confidence, reasoning):
        from common.discrepancy_resolution import _DiscrepancyResolutionResult
        return _DiscrepancyResolutionResult(
            chosen_value=chosen_value,
            chosen_section=chosen_section,
            chosen_source_element_type=source_type,
            confidence=confidence,
            reasoning=reasoning,
        )

    @patch("common.discrepancy_resolution.get_llm_client")
    @patch("common.discrepancy_resolution.DISCREPANCY_RESOLUTION_PROMPT")
    def test_happy_path_status_needs_human_review(
        self, mock_prompt, mock_get_llm, taxonomy_entry, two_candidates, two_sections
    ):
        mock_result = self._mock_chain_result(
            "12000", "income_statement", "table_cell", 0.9, "Formal financial statement preferred."
        )
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = mock_result
        mock_prompt.__or__ = MagicMock(return_value=mock_chain)
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.with_retry.return_value = MagicMock()
        mock_get_llm.return_value = mock_llm

        # Patch the | operator on the prompt to return the mock chain
        with patch(
            "common.discrepancy_resolution.DISCREPANCY_RESOLUTION_PROMPT"
        ) as mock_p:
            mock_chain2 = MagicMock()
            mock_chain2.invoke.return_value = mock_result
            mock_p.__or__ = lambda self, other: mock_chain2

            record = resolve_cross_section_discrepancy(
                taxonomy_entry=taxonomy_entry,
                candidates=two_candidates,
                sections_involved=two_sections,
            )

        assert record.status == "needs_human_review"
        assert record.review_reason == "section_discrepancy"
        assert record.kpi_id == "rev_001"
        assert record.method == "llm"

    @patch("common.discrepancy_resolution.get_llm_client")
    @patch("common.discrepancy_resolution.DISCREPANCY_RESOLUTION_PROMPT")
    def test_conflicting_values_excludes_chosen(
        self, mock_prompt, mock_get_llm, taxonomy_entry, two_candidates, two_sections
    ):
        """The conflicting_values list must contain candidates NOT chosen."""
        mock_result = self._mock_chain_result(
            "12000", "income_statement", "table_cell", 0.85, "reason"
        )
        with patch(
            "common.discrepancy_resolution.DISCREPANCY_RESOLUTION_PROMPT"
        ) as mock_p:
            mock_chain = MagicMock()
            mock_chain.invoke.return_value = mock_result
            mock_p.__or__ = lambda self, other: mock_chain

            record = resolve_cross_section_discrepancy(
                taxonomy_entry=taxonomy_entry,
                candidates=two_candidates,
                sections_involved=two_sections,
            )

        # Only the unchosen candidate (11950) should be in conflicting_values
        conflict_values = {str(cv.value) for cv in record.conflicting_values}
        assert "12000" not in conflict_values
        assert "11950" in conflict_values

    @patch("common.discrepancy_resolution.get_llm_client")
    @patch("common.discrepancy_resolution.DISCREPANCY_RESOLUTION_PROMPT")
    def test_llm_failure_falls_back_to_first_candidate(
        self, mock_prompt, mock_get_llm, taxonomy_entry, two_candidates, two_sections
    ):
        with patch(
            "common.discrepancy_resolution.DISCREPANCY_RESOLUTION_PROMPT"
        ) as mock_p:
            mock_chain = MagicMock()
            mock_chain.invoke.side_effect = RuntimeError("LLM down")
            mock_p.__or__ = lambda self, other: mock_chain

            record = resolve_cross_section_discrepancy(
                taxonomy_entry=taxonomy_entry,
                candidates=two_candidates,
                sections_involved=two_sections,
            )

        # Fallback: first candidate's value
        assert str(record.value) == "12000"
        assert record.status == "needs_human_review"

    @patch("common.discrepancy_resolution.get_llm_client")
    @patch("common.discrepancy_resolution.DISCREPANCY_RESOLUTION_PROMPT")
    def test_fiscal_year_is_empty_string_for_caller_to_stamp(
        self, mock_prompt, mock_get_llm, taxonomy_entry, two_candidates, two_sections
    ):
        """resolve_cross_section_discrepancy leaves fiscal_year='' — caller stamps it."""
        mock_result = self._mock_chain_result(
            "12000", "income_statement", "table_cell", 0.9, "reason"
        )
        with patch(
            "common.discrepancy_resolution.DISCREPANCY_RESOLUTION_PROMPT"
        ) as mock_p:
            mock_chain = MagicMock()
            mock_chain.invoke.return_value = mock_result
            mock_p.__or__ = lambda self, other: mock_chain

            record = resolve_cross_section_discrepancy(
                taxonomy_entry=taxonomy_entry,
                candidates=two_candidates,
                sections_involved=two_sections,
            )

        assert record.fiscal_year == ""

    @patch("common.discrepancy_resolution.get_llm_client")
    @patch("common.discrepancy_resolution.DISCREPANCY_RESOLUTION_PROMPT")
    def test_attempts_list_has_one_entry(
        self, mock_prompt, mock_get_llm, taxonomy_entry, two_candidates, two_sections
    ):
        mock_result = self._mock_chain_result(
            "12000", "income_statement", "table_cell", 0.9, "reason"
        )
        with patch(
            "common.discrepancy_resolution.DISCREPANCY_RESOLUTION_PROMPT"
        ) as mock_p:
            mock_chain = MagicMock()
            mock_chain.invoke.return_value = mock_result
            mock_p.__or__ = lambda self, other: mock_chain

            record = resolve_cross_section_discrepancy(
                taxonomy_entry=taxonomy_entry,
                candidates=two_candidates,
                sections_involved=two_sections,
            )

        assert len(record.attempts) == 1
        assert record.attempts[0].tier == "llm"

    @patch("common.discrepancy_resolution.get_llm_client")
    @patch("common.discrepancy_resolution.DISCREPANCY_RESOLUTION_PROMPT")
    def test_footnotes_merged_from_all_candidates(
        self, mock_prompt, mock_get_llm, taxonomy_entry, two_candidates, two_sections
    ):
        mock_result = self._mock_chain_result(
            "12000", "income_statement", "table_cell", 0.9, "reason"
        )
        with patch(
            "common.discrepancy_resolution.DISCREPANCY_RESOLUTION_PROMPT"
        ) as mock_p:
            mock_chain = MagicMock()
            mock_chain.invoke.return_value = mock_result
            mock_p.__or__ = lambda self, other: mock_chain

            record = resolve_cross_section_discrepancy(
                taxonomy_entry=taxonomy_entry,
                candidates=two_candidates,  # candidate 2 has footnote "a"
                sections_involved=two_sections,
            )

        assert "a" in record.footnotes
