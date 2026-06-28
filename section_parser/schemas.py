"""
Pydantic models for Agent 2 (Section Parser & Splitter).

Shared pipeline contracts (Section, TableElement, etc.) live in common.schemas
and are imported here — Agent 2 consumes and produces them but does not
redefine them.

Agent 2-specific models:
  - RawSection                       — intermediate result after heading split,
                                        before alignment
  - SectionAlignmentResult           — what align_section functions return
  - SectionAlignmentEntry /
    BatchSectionAlignmentLLMResult   — LLM structured output
  - SectionParserInput                      — what Agent 2 receives from the orchestrator
  - SectionParserOutput                     — what Agent 2 hands to Agent 3
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from common.schemas import (
    ChartElement,
    FootnoteElement,
    ReportMetadata,
    Section,
    TableElement,
)


# ---------------------------------------------------------------------------
# Heading-split intermediate (produced before LLM alignment)
# ---------------------------------------------------------------------------


class RawSection(BaseModel):
    """One section as produced by the heading-split step, before taxonomy alignment.

    This is an internal intermediate — it is never written to a store or
    included in Agent 2's output contract. It becomes a full Section once
    alignment_confidence, alignment_source, tables, charts, and footnotes
    are populated.
    """

    section_name_raw: str
    content_markdown: str
    page_range: tuple[int, int]


# ---------------------------------------------------------------------------
# Section alignment result (returned by align_section functions)
# ---------------------------------------------------------------------------


class SectionAlignmentResult(BaseModel):
    """What the alignment functions return for a single section.

    section_name_canonical is one of the Taxonomy Map's canonical section names
    or the sentinel "OTHER".  confidence is the alignment certainty (0-1).
    source identifies how the result was produced:
      "fuzzy_match"          — rapidfuzz Stage A cleared the cutoff
      "llm_fallback"         — GPT-4o Stage B or retry produced this result
      "best_guess_unresolved" — still below threshold after all retries;
                                stamped by pipeline.py before calling
                                assign_elements_to_sections(), which copies it
                                directly into Section.alignment_source
    """

    section_name_canonical: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    source: Literal["fuzzy_match", "llm_fallback", "best_guess_unresolved"]


# ---------------------------------------------------------------------------
# LLM structured-output schemas for section alignment
# ---------------------------------------------------------------------------


class SectionAlignmentEntry(BaseModel):
    """LLM output for one section's canonical-name alignment.

    section_name_raw is echoed back from the prompt so the caller can match
    each entry back to the originating RawSection without relying on index
    order.

    section_name_canonical must be one of the Taxonomy Map's canonical section
    names or the sentinel "OTHER". At the call site in alignment.py this field
    is further constrained via a dynamically-built Literal[...] type (same
    pattern as IndustryResult in Agent 1 — see report_ingestion/fallback.py's
    _make_industry_literal) before being passed to with_structured_output(), so
    the LLM cannot invent names outside the valid vocabulary.
    """

    section_name_raw: str
    section_name_canonical: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class BatchSectionAlignmentLLMResult(BaseModel):
    """LLM structured output for a single batched alignment call covering all
    sections whose fuzzy-match confidence fell below the alignment threshold.

    All unresolved sections are sent in one call to avoid per-section API cost
    (same batching principle as ClassificationFallbackResult in Agent 1).
    """

    alignments: list[SectionAlignmentEntry]


# ---------------------------------------------------------------------------
# Agent 2 input / output contracts
# ---------------------------------------------------------------------------


class SectionParserInput(BaseModel):
    """Everything Agent 2 needs to parse and align sections.

    narrative_markdown comes from Agent 1's conversion output (Docling primary
    or PyMuPDF fallback). The three element lists come from Agent 1b's
    multimodal extraction — they arrive pre-tagged with page numbers, which
    is how Agent 2 assigns them to sections by page-range overlap.

    retry_budget_remaining tracks the shared 2-turn budget (spec §8). Agent 2
    decrements it for each alignment self-correction attempt before passing
    the remainder on to Agent 8 via SectionParserOutput. Starts at 2.
    """

    report_metadata: ReportMetadata
    narrative_markdown: str
    tables: list[TableElement] = []
    charts: list[ChartElement] = []
    footnotes: list[FootnoteElement] = []
    retry_budget_remaining: int = Field(default=2, ge=0, le=2)


class SectionParserOutput(BaseModel):
    """Section list produced by Agent 2, ready for Agent 3 to persist and index.

    retry_turns_used is reported back to the Orchestrator, which owns the actual
    shared-budget bookkeeping and subtracts this from the report's shared budget
    before any subsequent agent gets to spend from it.

    has_unresolved_sections is True when any Section has
    alignment_source="best_guess_unresolved". Downstream agents use this to
    know that any KPI extracted from such a section must surface as
    needs_human_review regardless of extraction confidence.
    """

    report_id: str
    sections: list[Section]
    retry_turns_used: int = Field(default=0, ge=0)
    has_unresolved_sections: bool = False
