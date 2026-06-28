"""Agent 3 — Persistence & Indexing pipeline.

Two jobs, in order:
  1. Persist each Section to the Document DB (Postgres sections table).
  2. Chunk every section's content, embed the chunks, store in the Vector DB.

No LLM calls, no confidence routing, no HITL — this agent is pure storage.
"""

from __future__ import annotations

import logging

from vector_indexer.chunking import chunk_section
from vector_indexer.documents import persist_section
from vector_indexer.embeddings import embed_and_store_chunks
from vector_indexer.schemas import VectorIndexerOutput
from common.schemas import ReportMetadata, Section

log = logging.getLogger(__name__)


async def run_vector_indexer(sections: list[Section], report_metadata: ReportMetadata) -> VectorIndexerOutput:
    """Top-level Agent 3 orchestration.

    Args:
        sections:        The fully-assembled sections from Agent 2's output.
        report_metadata: Full report metadata from Agent 1; report_id is used as
                         the vector DB namespace and Document DB foreign key.
                         The complete object is available here for any downstream
                         enrichment (e.g. tagging fiscal_year onto chunks).

    Returns:
        VectorIndexerOutput with document_keys (one per persisted section) and
        chunks_embedded (total chunks stored in the vector DB).
    """
    report_id = report_metadata.report_id
    if not sections:
        log.warning("run_vector_indexer: no sections for report_id=%s; nothing to persist.", report_id)
        return VectorIndexerOutput(report_id=report_id)

    # ------------------------------------------------------------------
    # Step 1: Persist each section to the Document DB
    # ------------------------------------------------------------------
    document_keys: list[str] = []
    for section in sections:
        key = persist_section(section, report_id)
        document_keys.append(key)
        log.debug(
            "Persisted section '%s' for report_id=%s -> key=%s",
            section.section_name_canonical, report_id, key,
        )

    # ------------------------------------------------------------------
    # Step 2: Chunk all sections, embed, store in Vector DB
    # ------------------------------------------------------------------
    all_chunks = [chunk for section in sections for chunk in chunk_section(section)]
    chunks_embedded = embed_and_store_chunks(all_chunks, report_id)

    log.info(
        "Agent 3 complete for report_id=%s: %d sections persisted, %d chunks embedded.",
        report_id, len(document_keys), chunks_embedded,
    )
    return VectorIndexerOutput(
        report_id=report_id,
        document_keys=document_keys,
        chunks_embedded=chunks_embedded,
    )
