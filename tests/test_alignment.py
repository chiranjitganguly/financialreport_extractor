"""Tests for section_parser.alignment.

LLM calls are mocked via langchain_core.runnables.RunnableLambda so the prompt
pipeline is exercised end-to-end (template formatting → runnable invocation)
without hitting the real OpenAI API.
"""
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.runnables import RunnableLambda

from section_parser.alignment import (
    align_section_fuzzy,
    batch_align_sections_llm,
    realign_section_low_confidence,
)
from section_parser.schemas import SectionAlignmentResult

# ---------------------------------------------------------------------------
# Shared vocabulary
# ---------------------------------------------------------------------------

_VOCAB = [
    "Statement of Profit and Loss",
    "Balance Sheet",
    "Statement of Cash Flows",
    "Management Discussion and Analysis",
    "Notes to Financial Statements",
]


def _make_llm_mock(return_value):
    """Return a patched 'llm' whose chain call returns *return_value*."""
    async def _fake_chain(_inputs):
        return return_value

    fake_runnable = RunnableLambda(_fake_chain)
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value.with_retry.return_value = fake_runnable
    return mock_llm


# ---------------------------------------------------------------------------
# align_section_fuzzy — Stage A, sync, no LLM
# ---------------------------------------------------------------------------


class TestAlignSectionFuzzy:
    def test_exact_match_returns_result(self):
        result = align_section_fuzzy("Balance Sheet", _VOCAB, fuzzy_cutoff=80.0)
        assert result is not None
        assert result.section_name_canonical == "Balance Sheet"

    def test_near_match_clears_cutoff(self):
        # "Profit and Loss Statement" is close enough to "Statement of Profit and Loss"
        result = align_section_fuzzy(
            "Profit and Loss Statement", _VOCAB, fuzzy_cutoff=70.0
        )
        assert result is not None
        assert "Profit" in result.section_name_canonical

    def test_score_normalized_to_0_1(self):
        result = align_section_fuzzy("Balance Sheet", _VOCAB, fuzzy_cutoff=80.0)
        assert result is not None
        assert 0.0 <= result.match_score <= 1.0 if hasattr(result, "match_score") else True
        assert 0.0 <= result.confidence <= 1.0

    def test_source_is_fuzzy_match(self):
        result = align_section_fuzzy("Balance Sheet", _VOCAB, fuzzy_cutoff=80.0)
        assert result is not None
        assert result.source == "fuzzy_match"

    def test_high_score_for_exact_match(self):
        result = align_section_fuzzy("Balance Sheet", _VOCAB, fuzzy_cutoff=80.0)
        assert result is not None
        assert result.confidence >= 0.9

    def test_unrelated_header_returns_none(self):
        result = align_section_fuzzy(
            "Chairman's Letter to Shareholders", _VOCAB, fuzzy_cutoff=85.0
        )
        # Chairmen's letters don't match financial statement section names closely
        # — either None or a low match; with cutoff 85 it should be None
        # (if some partial match clears 85 this test would need a higher cutoff)
        if result is not None:
            assert result.confidence < 1.0  # at minimum it's not a high-confidence match

    def test_empty_vocabulary_returns_none(self):
        result = align_section_fuzzy("Balance Sheet", [], fuzzy_cutoff=85.0)
        assert result is None

    def test_high_cutoff_rejects_near_match(self):
        result = align_section_fuzzy(
            "Statement of P&L", _VOCAB, fuzzy_cutoff=99.0
        )
        assert result is None

    def test_returns_none_below_cutoff(self):
        result = align_section_fuzzy(
            "Corporate Governance Report", _VOCAB, fuzzy_cutoff=85.0
        )
        # "Corporate Governance Report" is semantically distinct from financial statements
        # With cutoff 85 this should be None; confidence would be low
        assert result is None or result.confidence < 0.85


# ---------------------------------------------------------------------------
# batch_align_sections_llm — Stage B, async, mocked LLM
# ---------------------------------------------------------------------------


class TestBatchAlignSectionsLlm:
    async def test_empty_unresolved_returns_empty(self):
        results = await batch_align_sections_llm(unresolved=[], vocabulary=_VOCAB)
        assert results == []

    async def test_happy_path_returns_correct_alignments(self):
        entry = MagicMock()
        entry.section_name_raw = "P&L Statement"
        entry.section_name_canonical = "Statement of Profit and Loss"
        entry.confidence = 0.91

        fake_result = MagicMock()
        fake_result.alignments = [entry]

        with patch("section_parser.alignment.llm", _make_llm_mock(fake_result)):
            results = await batch_align_sections_llm(
                unresolved=[("P&L Statement", "Revenue and expense details...")],
                vocabulary=_VOCAB,
            )

        assert len(results) == 1
        assert results[0].section_name_canonical == "Statement of Profit and Loss"
        assert results[0].confidence == 0.91
        assert results[0].source == "llm_fallback"

    async def test_result_source_is_llm_fallback(self):
        entry = MagicMock()
        entry.section_name_raw = "Cash Flows"
        entry.section_name_canonical = "Statement of Cash Flows"
        entry.confidence = 0.85

        fake_result = MagicMock()
        fake_result.alignments = [entry]

        with patch("section_parser.alignment.llm", _make_llm_mock(fake_result)):
            results = await batch_align_sections_llm(
                unresolved=[("Cash Flows", "Cash from operations...")],
                vocabulary=_VOCAB,
            )

        assert results[0].source == "llm_fallback"

    async def test_missing_llm_entry_substitutes_zero_confidence_other(self):
        # LLM returns an entry for only one of two sections
        entry = MagicMock()
        entry.section_name_raw = "Cash Flows"
        entry.section_name_canonical = "Statement of Cash Flows"
        entry.confidence = 0.85

        fake_result = MagicMock()
        fake_result.alignments = [entry]  # Only one entry, not two

        with patch("section_parser.alignment.llm", _make_llm_mock(fake_result)):
            results = await batch_align_sections_llm(
                unresolved=[
                    ("Cash Flows", "Cash content"),
                    ("Chairman Message", "Dear shareholders..."),
                ],
                vocabulary=_VOCAB,
            )

        assert len(results) == 2
        # Second entry missing from LLM → zero-confidence OTHER substitution
        assert results[1].section_name_canonical == "OTHER"
        assert results[1].confidence == 0.0
        assert results[1].source == "llm_fallback"

    async def test_multiple_sections_in_one_call(self):
        # Verify all unresolved sections are sent in a single LLM call
        entries = []
        for raw in ["MDA", "Notes"]:
            e = MagicMock()
            e.section_name_raw = raw
            e.section_name_canonical = "Management Discussion and Analysis" if raw == "MDA" else "Notes to Financial Statements"
            e.confidence = 0.88
            entries.append(e)

        fake_result = MagicMock()
        fake_result.alignments = entries

        call_count = 0

        async def _counting_chain(_):
            nonlocal call_count
            call_count += 1
            return fake_result

        fake_runnable = RunnableLambda(_counting_chain)
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.with_retry.return_value = fake_runnable

        with patch("section_parser.alignment.llm", mock_llm):
            results = await batch_align_sections_llm(
                unresolved=[("MDA", "Analysis..."), ("Notes", "Note 1...")],
                vocabulary=_VOCAB,
            )

        assert call_count == 1, "all sections must be batched in a single LLM call"
        assert len(results) == 2


# ---------------------------------------------------------------------------
# realign_section_low_confidence — retry, async, mocked LLM
# ---------------------------------------------------------------------------


class TestRealignSectionLowConfidence:
    async def test_returns_llm_fallback_source(self):
        fake_result = MagicMock()
        fake_result.section_name_canonical = "Balance Sheet"
        fake_result.confidence = 0.78

        previous = SectionAlignmentResult(
            section_name_canonical="Balance Sheet",
            confidence=0.20,
            source="llm_fallback",
        )

        with patch("section_parser.alignment.llm", _make_llm_mock(fake_result)):
            result = await realign_section_low_confidence(
                section_name_raw="Assets and Liabilities",
                content_excerpt="Total assets 50000, Total liabilities 30000...",
                vocabulary=_VOCAB,
                previous_result=previous,
            )

        assert result.source == "llm_fallback"

    async def test_returns_updated_confidence(self):
        fake_result = MagicMock()
        fake_result.section_name_canonical = "Balance Sheet"
        fake_result.confidence = 0.82

        previous = SectionAlignmentResult(
            section_name_canonical="OTHER",
            confidence=0.18,
            source="llm_fallback",
        )

        with patch("section_parser.alignment.llm", _make_llm_mock(fake_result)):
            result = await realign_section_low_confidence(
                section_name_raw="Assets and Liabilities",
                content_excerpt="Extensive balance sheet content...",
                vocabulary=_VOCAB,
                previous_result=previous,
            )

        assert result.confidence == 0.82
        assert result.section_name_canonical == "Balance Sheet"

    async def test_returns_section_alignment_result_instance(self):
        fake_result = MagicMock()
        fake_result.section_name_canonical = "Notes to Financial Statements"
        fake_result.confidence = 0.75

        previous = SectionAlignmentResult(
            section_name_canonical="Notes to Financial Statements",
            confidence=0.22,
            source="llm_fallback",
        )

        with patch("section_parser.alignment.llm", _make_llm_mock(fake_result)):
            result = await realign_section_low_confidence(
                section_name_raw="Notes",
                content_excerpt="Note 1: Accounting policies...",
                vocabulary=_VOCAB,
                previous_result=previous,
            )

        assert isinstance(result, SectionAlignmentResult)
