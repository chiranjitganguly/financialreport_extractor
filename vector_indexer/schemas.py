"""Agent 3 Pydantic schemas.

TextChunk is the shared shape produced by all four chunking functions in
chunking.py and consumed by embeddings.py.  VectorIndexerOutput is the pipeline
contract returned by run_vector_indexer() and persisted to agent_runs.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class TextChunk(BaseModel):
    """A single embeddable unit of text derived from one source element.

    All four chunking functions (narrative text, table row, chart
    interpretation, footnote) return this same shape so the embedding step
    downstream does not need to branch on source type.
    """

    chunk_text: str
    source_element_type: Literal["text", "table_row", "chart_interpretation", "footnote"]
    section_name_canonical: str
    page: Optional[int] = None
    page_range: Optional[tuple[int, int]] = None
    element_ref: Optional[str] = None


class VectorIndexerOutput(BaseModel):
    """Storage references produced by Agent 3, consumed by Agents 4–6.

    document_keys: one UUID string per persisted Section row (the primary key
        of the ``sections`` table).  Agents 4 and 6 use these to fetch full
        section content via documents.get_section().

    chunks_embedded: total number of TextChunks stored in the vector DB.
        Used for logging and verification — the vector DB namespace for this
        report is identified by report_id in chunk metadata, not a named
        collection.
    """

    report_id: str
    document_keys: list[str] = Field(default_factory=list)
    chunks_embedded: int = 0
