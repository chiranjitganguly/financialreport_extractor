"""Tests for vector_indexer.embeddings.

The real PGVector store and OpenAI embeddings are mocked throughout — no
network calls, no API key required.  Tests verify that embed_and_store_chunks:
  - calls add_texts() once with all texts and metadata
  - propagates report_id and source_element_type into metadata
  - returns the chunk count
  - handles an empty input correctly (returns 0, no add_texts call)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from vector_indexer.embeddings import embed_and_store_chunks
from vector_indexer.schemas import TextChunk

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chunk(
    text: str = "Revenue was 1000.",
    source_type: str = "text",
    canonical: str = "Statement of Profit and Loss",
    page: int | None = None,
    page_range: tuple | None = None,
    element_ref: str | None = None,
) -> TextChunk:
    return TextChunk(
        chunk_text=text,
        source_element_type=source_type,
        section_name_canonical=canonical,
        page=page,
        page_range=page_range,
        element_ref=element_ref,
    )


# ---------------------------------------------------------------------------
# Fixtures — patch PGVector at the point of use inside embeddings.py
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_pgvector():
    """Patch langchain_postgres.PGVector and OpenAIEmbeddings in embeddings.py."""
    with (
        patch("vector_indexer.embeddings.PGVector") as mock_cls,
        patch("vector_indexer.embeddings.OpenAIEmbeddings"),
    ):
        mock_store = MagicMock()
        mock_cls.return_value = mock_store
        yield mock_store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEmbedAndStoreChunks:
    def test_returns_zero_for_empty_input(self, mock_pgvector):
        result = embed_and_store_chunks([], report_id="rpt-001")
        assert result == 0

    def test_add_texts_not_called_for_empty_input(self, mock_pgvector):
        embed_and_store_chunks([], report_id="rpt-001")
        mock_pgvector.add_texts.assert_not_called()

    def test_returns_chunk_count(self, mock_pgvector):
        chunks = [_chunk(), _chunk(text="Another sentence."), _chunk(text="Third.")]
        result = embed_and_store_chunks(chunks, report_id="rpt-001")
        assert result == 3

    def test_add_texts_called_exactly_once(self, mock_pgvector):
        chunks = [_chunk(), _chunk(text="Second.")]
        embed_and_store_chunks(chunks, report_id="rpt-001")
        mock_pgvector.add_texts.assert_called_once()

    def test_add_texts_receives_all_texts(self, mock_pgvector):
        c1 = _chunk(text="Alpha.")
        c2 = _chunk(text="Beta.")
        embed_and_store_chunks([c1, c2], report_id="rpt-001")
        call_kwargs = mock_pgvector.add_texts.call_args
        texts_arg = call_kwargs[1].get("texts") or call_kwargs[0][0]
        assert texts_arg == ["Alpha.", "Beta."]

    def test_metadata_contains_report_id(self, mock_pgvector):
        embed_and_store_chunks([_chunk()], report_id="rpt-xyz")
        call_kwargs = mock_pgvector.add_texts.call_args
        metadatas = call_kwargs[1].get("metadatas") or call_kwargs[0][1]
        assert metadatas[0]["report_id"] == "rpt-xyz"

    def test_metadata_contains_source_element_type(self, mock_pgvector):
        embed_and_store_chunks([_chunk(source_type="table_row")], report_id="rpt-001")
        call_kwargs = mock_pgvector.add_texts.call_args
        metadatas = call_kwargs[1].get("metadatas") or call_kwargs[0][1]
        assert metadatas[0]["source_element_type"] == "table_row"

    def test_metadata_contains_section_name_canonical(self, mock_pgvector):
        embed_and_store_chunks(
            [_chunk(canonical="Balance Sheet")], report_id="rpt-001"
        )
        call_kwargs = mock_pgvector.add_texts.call_args
        metadatas = call_kwargs[1].get("metadatas") or call_kwargs[0][1]
        assert metadatas[0]["section_name_canonical"] == "Balance Sheet"

    def test_metadata_page_range_serialised_as_list(self, mock_pgvector):
        embed_and_store_chunks(
            [_chunk(page_range=(3, 7))], report_id="rpt-001"
        )
        call_kwargs = mock_pgvector.add_texts.call_args
        metadatas = call_kwargs[1].get("metadatas") or call_kwargs[0][1]
        assert metadatas[0]["page_range"] == [3, 7]

    def test_metadata_page_range_none_when_absent(self, mock_pgvector):
        embed_and_store_chunks([_chunk()], report_id="rpt-001")
        call_kwargs = mock_pgvector.add_texts.call_args
        metadatas = call_kwargs[1].get("metadatas") or call_kwargs[0][1]
        assert metadatas[0]["page_range"] is None

    def test_element_ref_propagated(self, mock_pgvector):
        embed_and_store_chunks(
            [_chunk(element_ref="tbl-007")], report_id="rpt-001"
        )
        call_kwargs = mock_pgvector.add_texts.call_args
        metadatas = call_kwargs[1].get("metadatas") or call_kwargs[0][1]
        assert metadatas[0]["element_ref"] == "tbl-007"

    def test_metadata_count_matches_chunk_count(self, mock_pgvector):
        chunks = [_chunk(), _chunk(text="B."), _chunk(text="C.")]
        embed_and_store_chunks(chunks, report_id="rpt-001")
        call_kwargs = mock_pgvector.add_texts.call_args
        metadatas = call_kwargs[1].get("metadatas") or call_kwargs[0][1]
        texts_arg = call_kwargs[1].get("texts") or call_kwargs[0][0]
        assert len(metadatas) == len(texts_arg) == 3

    def test_mixed_source_types_all_in_one_call(self, mock_pgvector):
        chunks = [
            _chunk(source_type="text"),
            _chunk(source_type="table_row"),
            _chunk(source_type="chart_interpretation"),
            _chunk(source_type="footnote"),
        ]
        embed_and_store_chunks(chunks, report_id="rpt-001")
        mock_pgvector.add_texts.assert_called_once()
        call_kwargs = mock_pgvector.add_texts.call_args
        texts = call_kwargs[1].get("texts") or call_kwargs[0][0]
        assert len(texts) == 4
