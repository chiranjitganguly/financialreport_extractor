"""Chunking functions for Agent 3.

Each function converts one type of source element into one or more TextChunk
objects.  All four return the same TextChunk shape so the embedding step in
embeddings.py does not need to branch on source type.

Serialization format for table rows is kept consistent with the main spec:
    "{row_label} — {column_label}: {value}; {column_label}: {value}; ..."
e.g. "Revenue — FY2025: 1,234; FY2024: 1,100"
"""

from __future__ import annotations

from langchain_text_splitters import RecursiveCharacterTextSplitter

from vector_indexer.config import settings
from vector_indexer.schemas import TextChunk
from common.schemas import ChartElement, FootnoteElement, Section, TableElement


def chunk_narrative_text(section: Section) -> list[TextChunk]:
    """Split section.content_markdown into overlapping text chunks.

    Page precision for narrative chunks is intentionally coarse — the chunk
    carries the section's page_range, not a per-chunk exact page, because
    Docling's page provenance is at section/element level, not mid-paragraph.
    This is an accepted limitation noted in the design doc §4.
    """
    if not section.content_markdown.strip():
        return []

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
        separators=["\n\n", "\n", " ", ""],
    )
    texts = splitter.split_text(section.content_markdown)
    return [
        TextChunk(
            chunk_text=t,
            source_element_type="text",
            section_name_canonical=section.section_name_canonical,
            page_range=section.page_range,
        )
        for t in texts
        if t.strip()
    ]


def chunk_table(table: TableElement) -> list[TextChunk]:
    """Produce one TextChunk per table row.

    Serialization: "{row_label} — {col_label}: {value}; {col_label}: {value}"
    All cells in a row are collapsed onto one line so Tier 2 vector search can
    match a KPI label and its corresponding values in the same chunk.
    """
    chunks: list[TextChunk] = []
    for row in table.rows:
        parts = [f"{cell.column_label}: {cell.value}" for cell in row.cells]
        text = f"{row.row_label} — {'; '.join(parts)}" if parts else row.row_label
        chunks.append(
            TextChunk(
                chunk_text=text,
                source_element_type="table_row",
                section_name_canonical=table.section_name_canonical,
                page=table.page,
                element_ref=table.table_id,
            )
        )
    return chunks


def chunk_chart(chart: ChartElement) -> list[TextChunk]:
    """One chunk per chart: the LLM-generated interpretation text.

    The chart image is not embeddable as raw text — only its description is.
    If interpretation is empty the chart produces no chunks (nothing to index).
    """
    if not chart.interpretation.strip():
        return []
    return [
        TextChunk(
            chunk_text=chart.interpretation,
            source_element_type="chart_interpretation",
            section_name_canonical=chart.section_name_canonical,
            page=chart.page,
            element_ref=chart.chart_id,
        )
    ]


def chunk_footnote(footnote: FootnoteElement) -> list[TextChunk]:
    """One chunk per footnote: the full footnote text.

    Footnotes are embedded independently so Tier 2 can retrieve them when a
    KPI definition references disclosure caveats.
    """
    if not footnote.text.strip():
        return []
    return [
        TextChunk(
            chunk_text=footnote.text,
            source_element_type="footnote",
            section_name_canonical=footnote.section_name_canonical,
            page=footnote.page,
            element_ref=footnote.footnote_id,
        )
    ]


def chunk_section(section: Section) -> list[TextChunk]:
    """Produce all TextChunks for a section.

    Calls all four chunking functions and returns the combined list.  This is
    what pipeline.py calls — not the individual functions.
    """
    chunks: list[TextChunk] = []
    chunks.extend(chunk_narrative_text(section))
    for table in section.tables:
        chunks.extend(chunk_table(table))
    for chart in section.charts:
        chunks.extend(chunk_chart(chart))
    for footnote in section.footnotes:
        chunks.extend(chunk_footnote(footnote))
    return chunks
