"""Tests for vector_indexer.pipeline.run_vector_indexer.

All I/O (DB writes, vector store) is mocked.  These tests cover only the
orchestration logic: correct handoff to persist_section and
embed_and_store_chunks, correct aggregation of document_keys and
chunks_embedded, and edge cases (empty input).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from vector_indexer.pipeline import run_vector_indexer
from vector_indexer.schemas import VectorIndexerOutput
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

_METADATA_ABC = ReportMetadata(
    report_id="rpt-abc",
    report_type="annual_report",
    language="en",
    accounting_standard="IFRS",
    industry="Technology",
    fiscal_year="FY2024",
    country="US",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_section(canonical: str = "Balance Sheet") -> Section:
    return Section(
        section_name_raw="Balance Sheet",
        section_name_canonical=canonical,
        alignment_confidence=0.95,
        alignment_source="fuzzy_match",
        content_markdown="Assets and liabilities.",
        tables=[],
        charts=[],
        footnotes=[],
        page_range=(1, 3),
    )


def _patches(persist_side_effect=None, chunk_side_effect=None, embed_return=5):
    """Return a context manager that patches persist_section, chunk_section,
    and embed_and_store_chunks inside the pipeline module."""
    import contextlib

    if persist_side_effect is None:
        persist_side_effect = ["key-001", "key-002", "key-003"]

    @contextlib.contextmanager
    def _ctx():
        with (
            patch(
                "vector_indexer.pipeline.persist_section",
                side_effect=persist_side_effect,
            ) as mock_persist,
            patch(
                "vector_indexer.pipeline.chunk_section",
                side_effect=chunk_side_effect or (lambda s: [MagicMock()]),
            ) as mock_chunk,
            patch(
                "vector_indexer.pipeline.embed_and_store_chunks",
                return_value=embed_return,
            ) as mock_embed,
        ):
            yield mock_persist, mock_chunk, mock_embed

    return _ctx


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------

class TestEmptyInput:
    async def test_returns_agent3output(self):
        result = await run_vector_indexer(sections=[], report_metadata=_METADATA)
        assert isinstance(result, VectorIndexerOutput)

    async def test_empty_sections_yields_no_document_keys(self):
        result = await run_vector_indexer(sections=[], report_metadata=_METADATA)
        assert result.document_keys == []

    async def test_empty_sections_yields_zero_chunks_embedded(self):
        result = await run_vector_indexer(sections=[], report_metadata=_METADATA)
        assert result.chunks_embedded == 0

    async def test_empty_sections_does_not_call_persist(self):
        with patch("vector_indexer.pipeline.persist_section") as mock_p:
            await run_vector_indexer(sections=[], report_metadata=_METADATA)
        mock_p.assert_not_called()

    async def test_empty_sections_does_not_call_embed(self):
        with patch("vector_indexer.pipeline.embed_and_store_chunks") as mock_e:
            await run_vector_indexer(sections=[], report_metadata=_METADATA)
        mock_e.assert_not_called()

    async def test_report_id_preserved_on_empty_output(self):
        result = await run_vector_indexer(sections=[], report_metadata=_METADATA)
        assert result.report_id == "rpt-001"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestHappyPath:
    async def test_returns_agent3output_instance(self):
        sections = [_make_section()]
        with _patches(persist_side_effect=["k1"], embed_return=3)():
            result = await run_vector_indexer(sections=sections, report_metadata=_METADATA)
        assert isinstance(result, VectorIndexerOutput)

    async def test_report_id_propagated_from_metadata(self):
        sections = [_make_section()]
        with _patches(persist_side_effect=["k1"])():
            result = await run_vector_indexer(sections=sections, report_metadata=_METADATA_ABC)
        assert result.report_id == "rpt-abc"

    async def test_document_keys_one_per_section(self):
        sections = [_make_section(), _make_section("Statement of Profit and Loss")]
        with _patches(persist_side_effect=["key-A", "key-B"])():
            result = await run_vector_indexer(sections=sections, report_metadata=_METADATA)
        assert result.document_keys == ["key-A", "key-B"]

    async def test_persist_called_once_per_section(self):
        sections = [_make_section(), _make_section()]
        with _patches(persist_side_effect=["k1", "k2"])() as (mock_p, _, __):
            await run_vector_indexer(sections=sections, report_metadata=_METADATA)
        assert mock_p.call_count == 2

    async def test_persist_receives_correct_report_id(self):
        sections = [_make_section()]
        with _patches(persist_side_effect=["k1"])() as (mock_p, _, __):
            await run_vector_indexer(sections=sections, report_metadata=_METADATA_ABC)
        _, kwargs = mock_p.call_args
        assert kwargs.get("report_id") or mock_p.call_args[0][1] == "rpt-abc"

    async def test_chunks_embedded_matches_embed_return(self):
        sections = [_make_section()]
        with _patches(persist_side_effect=["k1"], embed_return=42)():
            result = await run_vector_indexer(sections=sections, report_metadata=_METADATA)
        assert result.chunks_embedded == 42

    async def test_embed_called_once_for_all_sections(self):
        sections = [_make_section(), _make_section()]
        with _patches(persist_side_effect=["k1", "k2"])() as (_, __, mock_e):
            await run_vector_indexer(sections=sections, report_metadata=_METADATA)
        mock_e.assert_called_once()

    async def test_chunk_called_once_per_section(self):
        sections = [_make_section(), _make_section()]
        with _patches(persist_side_effect=["k1", "k2"])() as (_, mock_c, __):
            await run_vector_indexer(sections=sections, report_metadata=_METADATA)
        assert mock_c.call_count == 2

    async def test_all_chunks_passed_to_embed_in_one_call(self):
        """chunk_section returns [chunk_a] for section 1 and [chunk_b] for section 2.
        embed_and_store_chunks must receive both in a single call."""
        chunk_a = MagicMock(name="chunk_a")
        chunk_b = MagicMock(name="chunk_b")
        sections = [_make_section(), _make_section()]

        with (
            patch("vector_indexer.pipeline.persist_section", side_effect=["k1", "k2"]),
            patch(
                "vector_indexer.pipeline.chunk_section",
                side_effect=[[chunk_a], [chunk_b]],
            ),
            patch("vector_indexer.pipeline.embed_and_store_chunks", return_value=2) as mock_e,
        ):
            await run_vector_indexer(sections=sections, report_metadata=_METADATA)

        chunks_arg = mock_e.call_args[0][0]
        assert chunk_a in chunks_arg
        assert chunk_b in chunks_arg
        assert len(chunks_arg) == 2

    async def test_embed_receives_report_id_from_metadata(self):
        sections = [_make_section()]
        with _patches(persist_side_effect=["k1"])() as (_, __, mock_e):
            await run_vector_indexer(sections=sections, report_metadata=_METADATA_ABC)
        call_kwargs = mock_e.call_args
        report_id_arg = (
            call_kwargs[1].get("report_id")
            or (call_kwargs[0][1] if len(call_kwargs[0]) > 1 else None)
        )
        assert report_id_arg == "rpt-abc"

    async def test_full_metadata_available_in_pipeline(self):
        """Verify run_vector_indexer accepts the full ReportMetadata object — not just report_id.
        Checks that metadata fields beyond report_id are accessible without error."""
        sections = [_make_section()]
        meta = ReportMetadata(
            report_id="rpt-full",
            report_type="quarterly_report",
            language="en",
            accounting_standard="US-GAAP",
            industry="Technology",
            fiscal_year="Q1FY2026",
            country="US",
        )
        with _patches(persist_side_effect=["k1"])():
            result = await run_vector_indexer(sections=sections, report_metadata=meta)
        assert result.report_id == "rpt-full"


# ---------------------------------------------------------------------------
# Sections with no chunks (e.g. empty content + no elements)
# ---------------------------------------------------------------------------

class TestSectionWithNoChunks:
    async def test_zero_chunks_still_persists_section(self):
        """Even a section that produces no embeddable content must still be
        stored in the Document DB — it may have structural value for Agent 4."""
        sections = [_make_section()]
        with (
            patch("vector_indexer.pipeline.persist_section", return_value="key-empty") as mock_p,
            patch("vector_indexer.pipeline.chunk_section", return_value=[]),
            patch("vector_indexer.pipeline.embed_and_store_chunks", return_value=0),
        ):
            result = await run_vector_indexer(sections=sections, report_metadata=_METADATA)
        mock_p.assert_called_once()
        assert result.document_keys == ["key-empty"]

    async def test_zero_chunks_result_has_zero_embedded(self):
        sections = [_make_section()]
        with (
            patch("vector_indexer.pipeline.persist_section", return_value="key-empty"),
            patch("vector_indexer.pipeline.chunk_section", return_value=[]),
            patch("vector_indexer.pipeline.embed_and_store_chunks", return_value=0),
        ):
            result = await run_vector_indexer(sections=sections, report_metadata=_METADATA)
        assert result.chunks_embedded == 0
