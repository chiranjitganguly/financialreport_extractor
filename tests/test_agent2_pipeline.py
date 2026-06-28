"""End-to-end tests for section_parser.pipeline.run_section_parser.

All sub-functions are mocked so these tests cover only the orchestration
logic: which stages are called, how the retry budget is tracked, when
best_guess_unresolved is stamped, and what SectionParserOutput contains.
"""
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from section_parser.pipeline import run_section_parser
from section_parser.schemas import (
    SectionParserOutput,
    RawSection,
    SectionAlignmentResult,
)
from common.schemas import ReportMetadata, Section

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_METADATA = ReportMetadata(
    report_id="rpt-001",
    report_type="annual_report",
    language="en",
    accounting_standard="IND-AS",
    industry="Manufacturing",
    fiscal_year="FY2025",
    country="India",
)

_VOCAB = [
    "Statement of Profit and Loss",
    "Balance Sheet",
    "Statement of Cash Flows",
    "Management Discussion and Analysis",
]

_NARRATIVE = "# Income Statement\n\nRevenue.\n\n# Balance Sheet\n\nAssets."

_RAW_SECTIONS = [
    RawSection(section_name_raw="Income Statement", content_markdown="Revenue.", page_range=(1, 3)),
    RawSection(section_name_raw="Balance Sheet", content_markdown="Assets.", page_range=(5, 8)),
]

_HIGH_CONF_ALIGNMENTS = [
    SectionAlignmentResult(section_name_canonical="Statement of Profit and Loss", confidence=0.95, source="fuzzy_match"),
    SectionAlignmentResult(section_name_canonical="Balance Sheet", confidence=0.97, source="fuzzy_match"),
]

_LOW_CONF_ALIGNMENT = SectionAlignmentResult(
    section_name_canonical="OTHER", confidence=0.10, source="llm_fallback"
)


def _make_section(name_raw: str, name_canonical: str,
                  alignment_source: str = "fuzzy_match",
                  confidence: float = 0.95) -> Section:
    return Section(
        section_name_raw=name_raw,
        section_name_canonical=name_canonical,
        alignment_confidence=confidence,
        alignment_source=alignment_source,
        content_markdown="",
        tables=[],
        charts=[],
        footnotes=[],
        page_range=(1, 3),
    )


# ---------------------------------------------------------------------------
# Context manager that patches all sub-functions
# ---------------------------------------------------------------------------

def _patches(
    split_return=None,
    fuzzy_side_effect=None,
    batch_return=None,
    realign_return=None,
    assign_return=None,
):
    """Patch all Agent 2 sub-functions and return a context manager."""
    import contextlib

    if split_return is None:
        split_return = _RAW_SECTIONS
    if fuzzy_side_effect is None:
        fuzzy_side_effect = list(_HIGH_CONF_ALIGNMENTS)  # both fuzzy hits
    if batch_return is None:
        batch_return = []
    if realign_return is None:
        realign_return = SectionAlignmentResult(
            section_name_canonical="Balance Sheet", confidence=0.80, source="llm_fallback"
        )
    if assign_return is None:
        assign_return = [
            _make_section("Income Statement", "Statement of Profit and Loss"),
            _make_section("Balance Sheet", "Balance Sheet"),
        ]

    @contextlib.contextmanager
    def _ctx():
        with (
            patch("section_parser.pipeline.split_into_raw_sections",
                  return_value=split_return),
            patch("section_parser.pipeline.align_section_fuzzy",
                  side_effect=fuzzy_side_effect),
            patch("section_parser.pipeline.batch_align_sections_llm",
                  new_callable=AsyncMock, return_value=batch_return),
            patch("section_parser.pipeline.realign_section_low_confidence",
                  new_callable=AsyncMock, return_value=realign_return),
            patch("section_parser.pipeline.assign_elements_to_sections",
                  return_value=assign_return),
        ):
            yield

    return _ctx


# ---------------------------------------------------------------------------
# Happy path — all sections fuzzy-matched
# ---------------------------------------------------------------------------


class TestHappyPath:
    async def test_returns_agent2output_instance(self):
        with _patches()():
            result = await run_section_parser(None, _METADATA, _NARRATIVE, [], [], [], _VOCAB, 2)
        assert isinstance(result, SectionParserOutput)

    async def test_report_id_propagated(self):
        with _patches()():
            result = await run_section_parser(None, _METADATA, _NARRATIVE, [], [], [], _VOCAB, 2)
        assert result.report_id == "rpt-001"

    async def test_retry_turns_used_zero_when_all_fuzzy(self):
        with _patches()():
            result = await run_section_parser(None, _METADATA, _NARRATIVE, [], [], [], _VOCAB, 2)
        assert result.retry_turns_used == 0

    async def test_has_unresolved_sections_false_when_all_confident(self):
        with _patches()():
            result = await run_section_parser(None, _METADATA, _NARRATIVE, [], [], [], _VOCAB, 2)
        assert result.has_unresolved_sections is False

    async def test_sections_count_matches_split_output(self):
        with _patches()():
            result = await run_section_parser(None, _METADATA, _NARRATIVE, [], [], [], _VOCAB, 2)
        assert len(result.sections) == 2

    async def test_batch_llm_not_called_when_fuzzy_resolves_all(self):
        with _patches()():
            with patch("section_parser.pipeline.batch_align_sections_llm",
                       new_callable=AsyncMock) as mock_batch:
                # Override the context manager batch mock to check it's not called
                with patch("section_parser.pipeline.align_section_fuzzy",
                           side_effect=list(_HIGH_CONF_ALIGNMENTS)):
                    with patch("section_parser.pipeline.split_into_raw_sections",
                               return_value=_RAW_SECTIONS):
                        with patch("section_parser.pipeline.assign_elements_to_sections",
                                   return_value=[_make_section("s", "Balance Sheet")]):
                            await run_section_parser(None, _METADATA, _NARRATIVE, [], [], [], _VOCAB, 2)
                mock_batch.assert_not_called()


# ---------------------------------------------------------------------------
# Stage B triggered — some sections missed fuzzy
# ---------------------------------------------------------------------------


class TestStageBTriggered:
    async def test_batch_llm_called_when_fuzzy_misses(self):
        # First section fuzzy hit, second misses
        fuzzy_returns = [_HIGH_CONF_ALIGNMENTS[0], None]

        llm_result = SectionAlignmentResult(
            section_name_canonical="Balance Sheet", confidence=0.88, source="llm_fallback"
        )

        assign_return = [
            _make_section("Income Statement", "Statement of Profit and Loss"),
            _make_section("Balance Sheet", "Balance Sheet"),
        ]

        with (
            patch("section_parser.pipeline.split_into_raw_sections",
                  return_value=_RAW_SECTIONS),
            patch("section_parser.pipeline.align_section_fuzzy",
                  side_effect=fuzzy_returns),
            patch("section_parser.pipeline.batch_align_sections_llm",
                  new_callable=AsyncMock, return_value=[llm_result]) as mock_batch,
            patch("section_parser.pipeline.realign_section_low_confidence",
                  new_callable=AsyncMock),
            patch("section_parser.pipeline.assign_elements_to_sections",
                  return_value=assign_return),
        ):
            result = await run_section_parser(None, _METADATA, _NARRATIVE, [], [], [], _VOCAB, 2)
            mock_batch.assert_called_once()

    async def test_batch_receives_all_unresolved_at_once(self):
        # Both sections miss fuzzy — both should be sent in one batch call
        fuzzy_returns = [None, None]
        llm_results = [
            SectionAlignmentResult(section_name_canonical="Statement of Profit and Loss", confidence=0.85, source="llm_fallback"),
            SectionAlignmentResult(section_name_canonical="Balance Sheet", confidence=0.83, source="llm_fallback"),
        ]
        assign_return = [
            _make_section("Income Statement", "Statement of Profit and Loss"),
            _make_section("Balance Sheet", "Balance Sheet"),
        ]

        with (
            patch("section_parser.pipeline.split_into_raw_sections",
                  return_value=_RAW_SECTIONS),
            patch("section_parser.pipeline.align_section_fuzzy",
                  side_effect=fuzzy_returns),
            patch("section_parser.pipeline.batch_align_sections_llm",
                  new_callable=AsyncMock, return_value=llm_results) as mock_batch,
            patch("section_parser.pipeline.realign_section_low_confidence",
                  new_callable=AsyncMock),
            patch("section_parser.pipeline.assign_elements_to_sections",
                  return_value=assign_return),
        ):
            await run_section_parser(None, _METADATA, _NARRATIVE, [], [], [], _VOCAB, 2)
            # Single call with both sections
            assert mock_batch.call_count == 1
            unresolved_arg = mock_batch.call_args[1]["unresolved"]
            assert len(unresolved_arg) == 2


# ---------------------------------------------------------------------------
# Retry path — below-threshold sections with budget
# ---------------------------------------------------------------------------


class TestRetryPath:
    async def test_retry_called_for_low_confidence_section(self):
        # Fuzzy misses section 2; LLM returns low confidence → retry triggered
        fuzzy_returns = [_HIGH_CONF_ALIGNMENTS[0], None]
        low_llm = SectionAlignmentResult(
            section_name_canonical="OTHER", confidence=0.10, source="llm_fallback"
        )
        better_result = SectionAlignmentResult(
            section_name_canonical="Balance Sheet", confidence=0.80, source="llm_fallback"
        )
        assign_return = [
            _make_section("Income Statement", "Statement of Profit and Loss"),
            _make_section("Balance Sheet", "Balance Sheet", confidence=0.80),
        ]

        with (
            patch("section_parser.pipeline.split_into_raw_sections",
                  return_value=_RAW_SECTIONS),
            patch("section_parser.pipeline.align_section_fuzzy",
                  side_effect=fuzzy_returns),
            patch("section_parser.pipeline.batch_align_sections_llm",
                  new_callable=AsyncMock, return_value=[low_llm]),
            patch("section_parser.pipeline.realign_section_low_confidence",
                  new_callable=AsyncMock, return_value=better_result) as mock_retry,
            patch("section_parser.pipeline.assign_elements_to_sections",
                  return_value=assign_return),
        ):
            result = await run_section_parser(None, _METADATA, _NARRATIVE, [], [], [], _VOCAB, 2)
            mock_retry.assert_called_once()

    async def test_retry_turns_used_incremented(self):
        fuzzy_returns = [None, None]
        low_llm_results = [
            SectionAlignmentResult(section_name_canonical="OTHER", confidence=0.08, source="llm_fallback"),
            SectionAlignmentResult(section_name_canonical="OTHER", confidence=0.09, source="llm_fallback"),
        ]
        better = SectionAlignmentResult(
            section_name_canonical="Balance Sheet", confidence=0.82, source="llm_fallback"
        )
        assign_return = [
            _make_section("s1", "Statement of Profit and Loss", confidence=0.82),
            _make_section("s2", "Balance Sheet", confidence=0.82),
        ]

        with (
            patch("section_parser.pipeline.split_into_raw_sections",
                  return_value=_RAW_SECTIONS),
            patch("section_parser.pipeline.align_section_fuzzy",
                  side_effect=fuzzy_returns),
            patch("section_parser.pipeline.batch_align_sections_llm",
                  new_callable=AsyncMock, return_value=low_llm_results),
            patch("section_parser.pipeline.realign_section_low_confidence",
                  new_callable=AsyncMock, return_value=better),
            patch("section_parser.pipeline.assign_elements_to_sections",
                  return_value=assign_return),
        ):
            result = await run_section_parser(None, _METADATA, _NARRATIVE, [], [], [], _VOCAB, 2)
        # Both sections needed retry, budget=2 → 2 retry turns used
        assert result.retry_turns_used == 2

    async def test_zero_budget_skips_retry(self):
        fuzzy_returns = [_HIGH_CONF_ALIGNMENTS[0], None]
        low_llm = SectionAlignmentResult(
            section_name_canonical="OTHER", confidence=0.10, source="llm_fallback"
        )
        assign_return = [
            _make_section("Income Statement", "Statement of Profit and Loss"),
            _make_section("OTHER", "OTHER", confidence=0.10, alignment_source="best_guess_unresolved"),
        ]

        with (
            patch("section_parser.pipeline.split_into_raw_sections",
                  return_value=_RAW_SECTIONS),
            patch("section_parser.pipeline.align_section_fuzzy",
                  side_effect=fuzzy_returns),
            patch("section_parser.pipeline.batch_align_sections_llm",
                  new_callable=AsyncMock, return_value=[low_llm]),
            patch("section_parser.pipeline.realign_section_low_confidence",
                  new_callable=AsyncMock) as mock_retry,
            patch("section_parser.pipeline.assign_elements_to_sections",
                  return_value=assign_return),
        ):
            result = await run_section_parser(
                None, _METADATA, _NARRATIVE, [], [], [], _VOCAB, remaining_retry_budget=0
            )
            mock_retry.assert_not_called()
        assert result.retry_turns_used == 0


# ---------------------------------------------------------------------------
# best_guess_unresolved stamping
# ---------------------------------------------------------------------------


class TestBestGuessUnresolved:
    async def test_has_unresolved_sections_true_when_below_threshold(self):
        fuzzy_returns = [_HIGH_CONF_ALIGNMENTS[0], None]
        low_llm = SectionAlignmentResult(
            section_name_canonical="OTHER", confidence=0.05, source="llm_fallback"
        )
        assign_return = [
            _make_section("Income Statement", "Statement of Profit and Loss"),
            _make_section("Unknown", "OTHER", confidence=0.05, alignment_source="best_guess_unresolved"),
        ]

        with (
            patch("section_parser.pipeline.split_into_raw_sections",
                  return_value=_RAW_SECTIONS),
            patch("section_parser.pipeline.align_section_fuzzy",
                  side_effect=fuzzy_returns),
            patch("section_parser.pipeline.batch_align_sections_llm",
                  new_callable=AsyncMock, return_value=[low_llm]),
            patch("section_parser.pipeline.realign_section_low_confidence",
                  new_callable=AsyncMock, return_value=low_llm),
            patch("section_parser.pipeline.assign_elements_to_sections",
                  return_value=assign_return),
        ):
            result = await run_section_parser(None, _METADATA, _NARRATIVE, [], [], [], _VOCAB, 2)

        assert result.has_unresolved_sections is True

    async def test_alignment_source_set_to_best_guess_when_still_low_after_retry(self):
        # Both sections miss, LLM gives low confidence, retry also gives low confidence
        # → pipeline stamps best_guess_unresolved before calling assign_elements_to_sections
        fuzzy_returns = [None, None]
        low_llm_results = [
            SectionAlignmentResult(section_name_canonical="OTHER", confidence=0.05, source="llm_fallback"),
            SectionAlignmentResult(section_name_canonical="OTHER", confidence=0.07, source="llm_fallback"),
        ]
        still_low = SectionAlignmentResult(
            section_name_canonical="OTHER", confidence=0.06, source="llm_fallback"
        )

        captured_alignments = []

        def _capture_assign(raw_sections, alignments, tables, charts, footnotes):
            captured_alignments.extend(alignments)
            return [_make_section("s", "OTHER", alignment_source="best_guess_unresolved", confidence=0.05)] * 2

        with (
            patch("section_parser.pipeline.split_into_raw_sections",
                  return_value=_RAW_SECTIONS),
            patch("section_parser.pipeline.align_section_fuzzy",
                  side_effect=fuzzy_returns),
            patch("section_parser.pipeline.batch_align_sections_llm",
                  new_callable=AsyncMock, return_value=low_llm_results),
            patch("section_parser.pipeline.realign_section_low_confidence",
                  new_callable=AsyncMock, return_value=still_low),
            patch("section_parser.pipeline.assign_elements_to_sections",
                  side_effect=_capture_assign),
        ):
            await run_section_parser(None, _METADATA, _NARRATIVE, [], [], [], _VOCAB, 2)

        assert all(a.source == "best_guess_unresolved" for a in captured_alignments)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_empty_document_returns_empty_agent2output(self):
        with (
            patch("section_parser.pipeline.split_into_raw_sections", return_value=[]),
        ):
            result = await run_section_parser(None, _METADATA, _NARRATIVE, [], [], [], _VOCAB, 2)

        assert result.sections == []
        assert result.retry_turns_used == 0
        assert result.has_unresolved_sections is False

    async def test_docling_none_still_produces_output(self):
        with _patches()():
            result = await run_section_parser(
                None,  # docling_document=None → markdown fallback
                _METADATA, _NARRATIVE, [], [], [], _VOCAB, 2,
            )
        assert isinstance(result, SectionParserOutput)

    async def test_elements_forwarded_to_assign(self):
        from common.schemas import TableElement, TableRow, TableCell
        table = TableElement(
            table_id="t1", section_name_canonical="", page=2,
            rows=[TableRow(row_label="Row", cells=[TableCell(column_label="FY", value=100, footnote_refs=[])])],
            footnote_refs=[],
        )

        with (
            patch("section_parser.pipeline.split_into_raw_sections",
                  return_value=_RAW_SECTIONS),
            patch("section_parser.pipeline.align_section_fuzzy",
                  side_effect=list(_HIGH_CONF_ALIGNMENTS)),
            patch("section_parser.pipeline.batch_align_sections_llm",
                  new_callable=AsyncMock, return_value=[]),
            patch("section_parser.pipeline.realign_section_low_confidence",
                  new_callable=AsyncMock),
            patch("section_parser.pipeline.assign_elements_to_sections",
                  return_value=[_make_section("s", "Balance Sheet")]) as mock_assign,
        ):
            await run_section_parser(None, _METADATA, _NARRATIVE, [table], [], [], _VOCAB, 2)
            # Tables should be forwarded to assign_elements_to_sections
            call_kwargs = mock_assign.call_args
            assert table in (call_kwargs[0][2] if call_kwargs[0] else call_kwargs[1].get("tables", []))
