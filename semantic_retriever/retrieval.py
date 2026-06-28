"""Agent 5 — Tier 2: Semantic Retrieval.

Queries the vector store (populated by Agent 3) for each still-unresolved
KPI, then applies the same deterministic matching logic as Tier 1 against the
retrieved chunks.  The matching logic itself is NOT duplicated here — it lives
in common/deterministic_matching.py per the CLAUDE.md mandate.

Differences from Tier 1:
  - Input is retrieved vector chunks, not in-memory Section objects.
  - method="semantic" on resolved entries (records which tier ran it).
  - Table-row chunk text is a serialized string that must be parsed back to
    (row_label, {col_label: value}) before applying fuzzy matching.
  - For text/chart_interpretation/footnote chunks, match_narrative_text is
    called directly — those chunk types are plain narrative as far as pattern
    matching is concerned.
  - Discrepancy resolver is called with sections_involved=[] since Tier 2
    does not hold full Section objects in memory; the LLM has the candidate
    list for context.
"""

from __future__ import annotations

import logging
from typing import Optional

from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector
from rapidfuzz import fuzz

from common.config import settings as common_settings
from vector_indexer.config import settings as agent3_settings
from vector_indexer.schemas import TextChunk
from semantic_retriever.config import settings
from common.deterministic_matching import (
    _normalize_fiscal_year,
    compute_match_confidence,
    match_narrative_text,
)
from common.discrepancy_resolution import resolve_cross_section_discrepancy
from common.schemas import (
    CandidateValue,
    ExtractionAttempt,
    ExtractionLedger,
    TaxonomyEntry,
)

log = logging.getLogger(__name__)

_COLLECTION_NAME = "kpi_chunks"


# ---------------------------------------------------------------------------
# Vector store access
# ---------------------------------------------------------------------------

def _get_vector_store() -> PGVector:
    """Build a PGVector instance — same collection and connection as Agent 3."""
    embeddings = OpenAIEmbeddings(
        model=agent3_settings.EMBEDDING_MODEL,
        openai_api_key=common_settings.OPENAI_API_KEY,
    )
    return PGVector(
        embeddings=embeddings,
        collection_name=_COLLECTION_NAME,
        connection=common_settings.DATABASE_URL,
        use_jsonb=True,
    )


def search_vector_chunks(
    kpi_name: str,
    aliases: list[str],
    canonical_sections: list[str],
    report_id: str,
    top_k: int,
) -> list[TextChunk]:
    """Similarity-search the vector store for chunks relevant to one KPI.

    Args:
        kpi_name: The KPI's primary name — used as the query seed.
        aliases: Additional labels for the same KPI, appended to query.
        canonical_sections: Only return chunks from these section names.
        report_id: Filter to this report's chunks only.
        top_k: Maximum results returned.

    Returns:
        Reconstructed TextChunk objects from the vector store's Documents.
    """
    query = " ".join([kpi_name] + aliases)
    store = _get_vector_store()

    docs = store.similarity_search(
        query=query,
        k=top_k,
        filter={
            "report_id": report_id,
            "section_name_canonical": {"$in": canonical_sections},
        },
    )

    chunks: list[TextChunk] = []
    for doc in docs:
        m = doc.metadata
        raw_page_range = m.get("page_range")
        page_range: Optional[tuple[int, int]] = (
            tuple(raw_page_range) if raw_page_range else None  # type: ignore[assignment]
        )
        chunks.append(
            TextChunk(
                chunk_text=doc.page_content,
                source_element_type=m.get("source_element_type", "text"),
                section_name_canonical=m.get("section_name_canonical", ""),
                page=m.get("page"),
                page_range=page_range,
                element_ref=m.get("element_ref"),
            )
        )
    return chunks


# ---------------------------------------------------------------------------
# Per-chunk extraction
# ---------------------------------------------------------------------------

def _parse_table_row_chunk(chunk_text: str) -> tuple[str, dict[str, str]]:
    """Parse Agent 3's table_row serialization back to (row_label, cells).

    Serialization format (from vector_indexer/chunking.py):
        "{row_label} — {col_label}: {value}; {col_label}: {value}; ..."
    e.g. "Total Revenue — FY2025: 1,234; FY2024: 1,100"
    """
    if " — " not in chunk_text:
        return chunk_text.strip(), {}

    row_label, _, cells_str = chunk_text.partition(" — ")
    cells: dict[str, str] = {}
    for part in cells_str.split("; "):
        col, _, val = part.partition(": ")
        if col.strip() and val.strip():
            cells[col.strip()] = val.strip()
    return row_label.strip(), cells


def extract_from_chunk(
    chunk: TextChunk,
    kpi_name: str,
    aliases: list[str],
    fiscal_year: str,
) -> Optional[CandidateValue]:
    """Extract a CandidateValue from one retrieved chunk.

    Routes on chunk.source_element_type rather than duplicating matching logic:
      - table_row: parse serialized text, fuzzy-match row_label, read fiscal-year
        cell.  Uses compute_match_confidence from common/deterministic_matching.py.
      - text / chart_interpretation / footnote: delegate directly to
        match_narrative_text — all three are plain narrative for pattern purposes.

    Args:
        chunk: A TextChunk reconstructed from the vector store.
        kpi_name: KPI primary name.
        aliases: KPI aliases.
        fiscal_year: Target fiscal year column (e.g. "FY2025").

    Returns:
        CandidateValue or None if the chunk doesn't contain a usable value.
    """
    page = chunk.page
    if page is None and chunk.page_range:
        page = chunk.page_range[0]

    if chunk.source_element_type == "table_row":
        row_label, cells = _parse_table_row_chunk(chunk.chunk_text)
        if not cells:
            return None

        terms = [kpi_name] + aliases
        scores = [fuzz.WRatio(row_label, t) for t in terms]
        top_score = max(scores) if scores else 0.0

        if top_score < settings.TABLE_ROW_FUZZY_CUTOFF:
            return None

        fy_year = _normalize_fiscal_year(fiscal_year)
        matched_val: Optional[str] = None
        for col_label, val in cells.items():
            col_year = _normalize_fiscal_year(col_label)
            if col_year == fy_year or fiscal_year in col_label:
                matched_val = val
                break

        if matched_val is None:
            return None

        # Single retrieved chunk = single candidate; confidence from the match score.
        confidence = compute_match_confidence(1, top_score / 100.0, None)
        return CandidateValue(
            value=matched_val,
            section_name_canonical=chunk.section_name_canonical,
            page=page,
            source_element_type="table_cell",
            footnotes=[],
            confidence=confidence,
        )

    else:
        # text, chart_interpretation, footnote — all treated as narrative text.
        if chunk.page_range:
            page_range = chunk.page_range
        elif page is not None:
            page_range = (page, page)
        else:
            page_range = (0, 0)

        cv = match_narrative_text(
            content_markdown=chunk.chunk_text,
            kpi_name=kpi_name,
            aliases=aliases,
            section_page_range=page_range,
        )
        if cv is not None:
            cv = cv.model_copy(
                update={"section_name_canonical": chunk.section_name_canonical}
            )
        return cv


# ---------------------------------------------------------------------------
# Per-KPI Tier 2 extraction
# ---------------------------------------------------------------------------

def extract_semantic_candidates(
    taxonomy_entry: TaxonomyEntry,
    report_id: str,
    fiscal_year: str,
    top_k: int,
) -> list[CandidateValue]:
    """Retrieve chunks and extract candidates for one KPI.

    Deduplicates by (section_name_canonical, value) — multiple agreeing chunks
    from the same section are corroboration, not separate candidates.

    Args:
        taxonomy_entry: The KPI being extracted.
        report_id: Report identifier used to scope the vector search.
        fiscal_year: Target fiscal year.
        top_k: Max chunks to retrieve.

    Returns:
        Deduplicated list of CandidateValues.
    """
    chunks = search_vector_chunks(
        kpi_name=taxonomy_entry.kpi_name,
        aliases=taxonomy_entry.aliases,
        canonical_sections=taxonomy_entry.canonical_sections,
        report_id=report_id,
        top_k=top_k,
    )

    seen: set[tuple[str, str]] = set()
    candidates: list[CandidateValue] = []

    for chunk in chunks:
        cv = extract_from_chunk(chunk, taxonomy_entry.kpi_name, taxonomy_entry.aliases, fiscal_year)
        if cv is None:
            continue
        key = (cv.section_name_canonical, str(cv.value))
        if key not in seen:
            seen.add(key)
            candidates.append(cv)

    return candidates


# ---------------------------------------------------------------------------
# Tier 2 orchestration
# ---------------------------------------------------------------------------

def run_semantic_retrieval(
    ledger: ExtractionLedger,
    filtered_taxonomy: list[TaxonomyEntry],
    report_id: str,
    fiscal_year: str,
    top_k: int,
) -> ExtractionLedger:
    """Run Tier 2 semantic retrieval over every not_found ledger entry.

    The set of KPIs to query is snapshotted at the START of this function from
    the ledger's current not_found state.  Only those KPIs are sent to the
    vector store — KPIs already resolved by Tiers 1 or earlier in this loop
    are never queried.

    Same 0/1/>1-distinct-values branching as Tier 1 — method="semantic" on
    resolved entries since the vector retrieval is what surfaced the match.
    Discrepancy resolution uses sections_involved=[] because Tier 2 operates
    on chunk text, not full in-memory Section objects; the LLM resolver works
    from candidate text alone.

    Args:
        ledger: ExtractionLedger after Tier 1.
        filtered_taxonomy: Taxonomy entries applicable to this report.
        report_id: Used to scope vector search to this report.
        fiscal_year: Target fiscal year column.
        top_k: Max chunks to retrieve per KPI.

    Returns:
        Updated ExtractionLedger.
    """
    taxonomy_by_id = {e.kpi_id: e for e in filtered_taxonomy}

    # Snapshot the not_found KPIs before any queries so the search list is
    # determined once, not re-evaluated on each loop iteration.
    not_found_pairs = [
        (kpi_id, taxonomy_by_id[kpi_id])
        for kpi_id, record in ledger.records.items()
        if record.status == "not_found" and kpi_id in taxonomy_by_id
    ]

    for kpi_id, entry in not_found_pairs:
        record = ledger.records[kpi_id]

        candidates = extract_semantic_candidates(
            taxonomy_entry=entry,
            report_id=report_id,
            fiscal_year=fiscal_year,
            top_k=top_k,
        )

        if not candidates:
            record.attempts.append(
                ExtractionAttempt(
                    tier="semantic",
                    value=None,
                    confidence=0.0,
                    outcome="not_found",
                    note="no usable vector match found",
                )
            )
            continue

        distinct_values = {str(c.value) for c in candidates}

        if len(distinct_values) == 1:
            best = max(candidates, key=lambda c: c.confidence)
            record.value = best.value
            record.section = best.section_name_canonical
            record.page = best.page
            record.method = "semantic"
            record.source_element_type = best.source_element_type
            record.footnotes = list({fn for c in candidates for fn in c.footnotes})
            record.confidence = best.confidence
            record.status = "found"
            record.attempts.append(
                ExtractionAttempt(
                    tier="semantic",
                    value=best.value,
                    confidence=best.confidence,
                    outcome="found",
                    note=f"vector match in {best.section_name_canonical} via {best.source_element_type}",
                )
            )
            log.debug(
                "Tier 2 found kpi_id=%s value=%s confidence=%.2f",
                kpi_id, best.value, best.confidence,
            )

        else:
            resolved = resolve_cross_section_discrepancy(
                taxonomy_entry=entry,
                candidates=candidates,
                sections_involved=[],
            )
            resolved.fiscal_year = fiscal_year
            ledger.records[kpi_id] = resolved
            log.info(
                "Tier 2 discrepancy for kpi_id=%s: values=%s → LLM chose %s",
                kpi_id, list(distinct_values), resolved.value,
            )

    return ledger
