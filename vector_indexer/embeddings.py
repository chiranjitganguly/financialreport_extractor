"""Embedding and vector store persistence for Agent 3.

Uses langchain_postgres.PGVector with OpenAI text-embedding-3-small.
All chunks for a report are stored in one shared table; report_id in chunk
metadata acts as the namespace — Agents 5+ filter by report_id at query time.
"""

from __future__ import annotations

import logging

from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector

from common.config import settings as common_settings
from vector_indexer.config import settings
from vector_indexer.schemas import TextChunk

log = logging.getLogger(__name__)

_COLLECTION_NAME = "kpi_chunks"


def _get_vector_store() -> PGVector:
    """Build a PGVector instance connected to the shared Postgres database."""
    embeddings = OpenAIEmbeddings(
        model=settings.EMBEDDING_MODEL,
        openai_api_key=common_settings.OPENAI_API_KEY,
    )
    return PGVector(
        embeddings=embeddings,
        collection_name=_COLLECTION_NAME,
        connection=common_settings.DATABASE_URL,
        use_jsonb=True,
    )


def embed_and_store_chunks(chunks: list[TextChunk], report_id: str) -> int:
    """Embed all chunks and store them in the vector DB.

    Args:
        chunks:    Output of chunk_section() across however many sections are
                   being processed.  All chunks are stored in one batched
                   add_texts() call — avoids one API round-trip per chunk.
        report_id: Tagged into every chunk's metadata so Agents 5+ can filter
                   similarity search results to this report.

    Returns:
        Number of chunks successfully embedded and stored.
    """
    if not chunks:
        return 0

    texts = [c.chunk_text for c in chunks]
    metadatas = [
        {
            "report_id": report_id,
            "section_name_canonical": c.section_name_canonical,
            "source_element_type": c.source_element_type,
            "page": c.page,
            "page_range": list(c.page_range) if c.page_range else None,
            "element_ref": c.element_ref,
        }
        for c in chunks
    ]

    store = _get_vector_store()
    store.add_texts(texts=texts, metadatas=metadatas)

    log.info(
        "Embedded and stored %d chunks for report_id=%s", len(chunks), report_id
    )
    return len(chunks)
