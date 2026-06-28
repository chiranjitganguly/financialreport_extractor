"""
Agent 2 — Section Alignment.

Maps raw section headers to canonical taxonomy names using a two-stage approach:
  Stage A — rapidfuzz fuzzy match (sync, cheap)
  Stage B — batched GPT-4o structured-output call for anything Stage A missed

Public API:
  align_section_fuzzy(...)            — Stage A only, sync
  batch_align_sections_llm(...)       — Stage B, async, one call for all unresolved
  realign_section_low_confidence(...) — per-section retry with more context, async
"""

from __future__ import annotations

import json
import logging
from typing import Optional, Union

from pydantic import BaseModel, Field, create_model
from rapidfuzz import fuzz, process

from section_parser.llm_client import get_llm
from section_parser.prompts import (
    SECTION_ALIGNMENT_PROMPT,
    SECTION_REALIGNMENT_PROMPT,
)
from section_parser.schemas import (
    BatchSectionAlignmentLLMResult,
    SectionAlignmentEntry,
    SectionAlignmentResult,
)

log = logging.getLogger(__name__)

# Sentinel value added to the vocabulary so the LLM can explicitly say
# "this section has no KPI relevance."
_OTHER_SENTINEL = "OTHER"


# ---------------------------------------------------------------------------
# Dynamic Literal builder — same pattern as report_ingestion/fallback.py
# ---------------------------------------------------------------------------


def _make_section_literal(vocabulary: list[str]):
    """Return Union[Literal[v1], Literal[v2], ...] for use in a dynamic Pydantic model.

    The vocabulary includes "OTHER" so the model can explicitly signal that a
    section is not KPI-relevant.
    """
    from typing import Literal

    if not vocabulary:
        return str  # type: ignore[return-value]
    literal_types = tuple(Literal[v] for v in vocabulary)  # type: ignore[misc]
    return Union[literal_types]  # type: ignore[return-value]


def _build_batch_llm_schema(vocabulary_with_other: list[str]) -> type[BaseModel]:
    """Dynamically build the LLM structured-output schema for a batch alignment call."""
    from typing import Literal

    SectionNameLiteral = _make_section_literal(vocabulary_with_other)

    # Build a dynamic per-entry model with the constrained vocabulary
    EntryModel = create_model(
        "_SectionAlignmentEntryLLM",
        section_name_raw=(str, ...),
        section_name_canonical=(SectionNameLiteral, ...),
        confidence=(float, Field(..., ge=0.0, le=1.0)),
    )

    BatchModel = create_model(
        "_BatchSectionAlignmentLLM",
        alignments=(list[EntryModel], ...),  # type: ignore[valid-type]
    )
    return BatchModel  # type: ignore[return-value]


def _build_realign_llm_schema(vocabulary_with_other: list[str]) -> type[BaseModel]:
    """Dynamically build the LLM structured-output schema for a single re-alignment."""
    SectionNameLiteral = _make_section_literal(vocabulary_with_other)

    return create_model(
        "_RealignLLMOut",
        section_name_canonical=(SectionNameLiteral, ...),
        confidence=(float, Field(..., ge=0.0, le=1.0)),
    )  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Stage A: sync fuzzy match
# ---------------------------------------------------------------------------


def align_section_fuzzy(
    section_name_raw: str,
    canonical_vocabulary: list[str],
    fuzzy_cutoff: float,
) -> Optional[SectionAlignmentResult]:
    """Stage A: fuzzy-match *section_name_raw* against *canonical_vocabulary*.

    Uses rapidfuzz.process.extractOne with WRatio, same scorer as Agent 1's
    company lookup.  If the best match's score (0-100) >= fuzzy_cutoff, returns
    a SectionAlignmentResult with confidence = score / 100, source="fuzzy_match".
    Returns None if no match clears the cutoff — the caller should then batch
    this section for the LLM fallback (Stage B).

    Args:
        section_name_raw: Raw heading text from the document.
        canonical_vocabulary: Output of get_canonical_section_vocabulary();
            should NOT include "OTHER" — the fuzzy-match stage never maps to OTHER.
        fuzzy_cutoff: Minimum score on the 0-100 rapidfuzz scale.  From
            config.settings.SECTION_ALIGNMENT_FUZZY_CUTOFF (default 85.0).

    Returns:
        SectionAlignmentResult or None.
    """
    if not canonical_vocabulary:
        return None

    match = process.extractOne(
        section_name_raw,
        canonical_vocabulary,
        scorer=fuzz.WRatio,
        score_cutoff=fuzzy_cutoff,
    )

    if match is None:
        return None

    matched_name, score, _index = match
    return SectionAlignmentResult(
        section_name_canonical=matched_name,
        confidence=score / 100.0,
        source="fuzzy_match",
    )


# ---------------------------------------------------------------------------
# Stage B: async batched LLM alignment
# ---------------------------------------------------------------------------


_ALIGNMENT_BATCH_SIZE = 25  # max sections per LLM call — larger batches cause truncation


async def _align_batch_chunk(
    chunk: list[tuple[str, str]],
    vocabulary_with_other: list[str],
    BatchSchema,
    chain,
) -> dict[str, SectionAlignmentEntry]:
    """Align one chunk of sections; returns raw_name → entry mapping."""
    sections_json = json.dumps(
        [{"section_name_raw": raw, "content_excerpt": excerpt} for raw, excerpt in chunk],
        ensure_ascii=False,
    )
    raw_result = await chain.ainvoke(
        {
            "canonical_vocabulary": ", ".join(vocabulary_with_other),
            "sections_json": sections_json,
        }
    )
    return {entry.section_name_raw: entry for entry in raw_result.alignments}


async def batch_align_sections_llm(
    unresolved: list[tuple[str, str]],
    vocabulary: list[str],
) -> list[SectionAlignmentResult]:
    """Stage B: batched LLM call for all sections that fuzzy missed.

    Splits the input into chunks of at most _ALIGNMENT_BATCH_SIZE to avoid
    LLM response truncation when the document has many sections.

    Args:
        unresolved: List of ``(section_name_raw, content_excerpt)`` tuples for
            sections where Stage A returned None.
        vocabulary: Canonical vocabulary from get_canonical_section_vocabulary().
            "OTHER" is automatically appended inside this function.

    Returns:
        list[SectionAlignmentResult], one entry per input tuple, in the same
        order. If the LLM omits an entry, a zero-confidence OTHER result is
        substituted so the caller always gets a full-length list.
    """
    if not unresolved:
        return []

    vocabulary_with_other = vocabulary + [_OTHER_SENTINEL]

    BatchSchema = _build_batch_llm_schema(vocabulary_with_other)
    chain = (
        SECTION_ALIGNMENT_PROMPT
        | get_llm().with_structured_output(BatchSchema).with_retry()
    )

    # Process in chunks to avoid response truncation
    raw_name_to_entry: dict[str, SectionAlignmentEntry] = {}
    chunks = [
        unresolved[i: i + _ALIGNMENT_BATCH_SIZE]
        for i in range(0, len(unresolved), _ALIGNMENT_BATCH_SIZE)
    ]
    for chunk in chunks:
        chunk_result = await _align_batch_chunk(chunk, vocabulary_with_other, BatchSchema, chain)
        raw_name_to_entry.update(chunk_result)

    results: list[SectionAlignmentResult] = []
    for section_name_raw, _excerpt in unresolved:
        entry = raw_name_to_entry.get(section_name_raw)
        if entry is None:
            log.warning(
                "LLM batch alignment did not return an entry for section '%s'; "
                "substituting zero-confidence OTHER.",
                section_name_raw,
            )
            results.append(
                SectionAlignmentResult(
                    section_name_canonical=_OTHER_SENTINEL,
                    confidence=0.0,
                    source="llm_fallback",
                )
            )
        else:
            results.append(
                SectionAlignmentResult(
                    section_name_canonical=entry.section_name_canonical,
                    confidence=entry.confidence,
                    source="llm_fallback",
                )
            )

    return results


# ---------------------------------------------------------------------------
# Retry: async per-section realignment with fuller context
# ---------------------------------------------------------------------------


async def realign_section_low_confidence(
    section_name_raw: str,
    content_excerpt: str,
    vocabulary: list[str],
    previous_result: SectionAlignmentResult,
) -> SectionAlignmentResult:
    """Retry alignment for a section whose first-pass confidence was too low.

    Always uses the LLM path (no point retrying fuzzy-match identically) but
    with a fuller content excerpt and the previous low-confidence guess so the
    model can confirm or override with better grounding.

    Args:
        section_name_raw: Raw heading text.
        content_excerpt: Larger slice of the section's content (e.g. first
            1000 chars of body text, not just the heading).
        vocabulary: Canonical vocabulary; "OTHER" is appended automatically.
        previous_result: What the first alignment pass returned — passed to the
            prompt so the model knows what to confirm or override.

    Returns:
        SectionAlignmentResult with source="llm_fallback".
    """
    vocabulary_with_other = vocabulary + [_OTHER_SENTINEL]
    RealignSchema = _build_realign_llm_schema(vocabulary_with_other)
    chain = (
        SECTION_REALIGNMENT_PROMPT
        | get_llm().with_structured_output(RealignSchema).with_retry()
    )

    raw_result = await chain.ainvoke(
        {
            "canonical_vocabulary": ", ".join(vocabulary_with_other),
            "section_name_raw": section_name_raw,
            "previous_canonical": previous_result.section_name_canonical,
            "previous_confidence": previous_result.confidence,
            "content_excerpt": content_excerpt,
        }
    )

    return SectionAlignmentResult(
        section_name_canonical=raw_result.section_name_canonical,
        confidence=raw_result.confidence,
        source="llm_fallback",
    )
