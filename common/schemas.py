"""
Shared pipeline contracts for the KPI-extraction pipeline.

These models are defined in kpi-extraction-agentic-spec-FINAL.md §2 and are
shared across multiple agents.  They live here — not inside any single agent's
package — so that downstream agents can import them without creating circular
dependencies on Agent 1.
"""

from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Report-level metadata
# ---------------------------------------------------------------------------


class ReportMetadata(BaseModel):
    report_id: str
    report_type: Literal["annual_report", "quarterly_report", "regulatory_filing"]
    language: str  # ISO 639-1
    accounting_standard: str  # e.g. "IFRS", "US-GAAP", "IND-AS"
    industry: str
    fiscal_year: str
    country: Optional[str] = None
    company_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Taxonomy / KPI catalogue
# ---------------------------------------------------------------------------


class TaxonomyEntry(BaseModel):
    kpi_id: str
    kpi_name: str
    definition: str
    canonical_sections: list[str]
    applicable_industries: list[str]
    applicable_report_types: list[str]
    applicable_accounting_standards: list[str]
    aliases: list[str]


# ---------------------------------------------------------------------------
# Table elements
# ---------------------------------------------------------------------------


class TableCell(BaseModel):
    column_label: str
    value: Union[str, float, int]
    footnote_refs: list[str]


class TableRow(BaseModel):
    row_label: str
    cells: list[TableCell]


class TableElement(BaseModel):
    table_id: str
    caption: Optional[str] = None
    section_name_canonical: str
    page: int
    rows: list[TableRow]
    footnote_refs: list[str]


# ---------------------------------------------------------------------------
# Chart elements
# ---------------------------------------------------------------------------


class ChartElement(BaseModel):
    chart_id: str
    caption: Optional[str] = None
    section_name_canonical: str
    page: int
    image_ref: str
    interpretation: str
    interpretation_confidence: float
    footnote_refs: list[str]


# ---------------------------------------------------------------------------
# Footnote elements
# ---------------------------------------------------------------------------


class FootnoteAnchor(BaseModel):
    element_type: Literal["table_cell", "chart", "text"]
    element_id: str
    location: str


class FootnoteElement(BaseModel):
    footnote_id: str
    marker: str
    text: str
    section_name_canonical: str
    page: int
    anchors: list[FootnoteAnchor]


# ---------------------------------------------------------------------------
# Section (Agent 2 output, Agent 3+ input)
# ---------------------------------------------------------------------------


class Section(BaseModel):
    section_name_raw: str
    section_name_canonical: str  # canonical taxonomy name, or "OTHER"
    alignment_confidence: float  # 0-1; how certain the raw→canonical mapping is
    alignment_source: Literal["fuzzy_match", "llm_fallback", "best_guess_unresolved"]
    content_markdown: str
    tables: list[TableElement]
    charts: list[ChartElement]
    footnotes: list[FootnoteElement]
    page_range: tuple[int, int]


# ---------------------------------------------------------------------------
# Extraction records (Agent 4+ output)
# ---------------------------------------------------------------------------


class CandidateValue(BaseModel):
    """Intermediate extraction result produced by Tier 1/2 matching functions.

    Not part of the final wire format — converted to ExtractionRecord or
    ConflictingValue before leaving an agent.  Lives in common/ because both
    deterministic_matching.py (Tier 1) and discrepancy_resolution.py (Step 6a)
    consume it.
    """

    value: Union[str, float, int]
    section_name_canonical: str
    page: Optional[int] = None
    source_element_type: Literal["text", "table_cell", "chart"]
    footnotes: list[str] = []
    confidence: float


class ConflictingValue(BaseModel):
    section: str
    value: Union[str, float, int]
    method: Literal["deterministic", "semantic", "llm"]
    source_element_type: Literal["text", "table_cell", "chart"]


class ExtractionAttempt(BaseModel):
    tier: Literal["deterministic", "semantic", "llm"]
    value: Optional[Union[str, float, int]] = None
    confidence: float = 0.0
    outcome: Literal["found", "not_found", "flagged"]
    note: str = ""


# Design-doc alias — same model, two names for cross-agent readability.
AttemptRecord = ExtractionAttempt


class ExtractionRecord(BaseModel):
    kpi_id: str
    kpi_name: Optional[str] = None  # populated from taxonomy at ledger init
    value: Optional[Union[str, float, int]] = None
    fiscal_year: str
    # section/method are None until a tier populates them; initialize_extraction_ledger
    # creates records with only kpi_id + fiscal_year + status="not_found".
    section: Optional[str] = None
    page: Optional[int] = None
    method: Optional[Literal["deterministic", "semantic", "llm"]] = None
    source_element_type: Optional[Literal["text", "table_cell", "chart"]] = None
    footnotes: list[str] = []
    confidence: float = 0.0
    status: Literal["found", "not_found", "flagged", "needs_human_review"] = "not_found"
    review_reason: Optional[
        Literal[
            "low_confidence",
            "validation_failed",
            "not_found_after_retries",
            "section_discrepancy",
            "footnoted_caveat",
        ]
    ] = None
    conflicting_values: list[ConflictingValue] = []
    attempts: list[ExtractionAttempt] = []
    alias_used: Optional[str] = None  # term the LLM found the KPI under in the doc


class ExtractionLedger(BaseModel):
    """Mutable shared state passed across the full extraction cascade.

    Keyed by kpi_id.  Every agent reads only the entries relevant to its tier
    and updates only the ones it resolves — no agent overwrites another's work.
    """

    records: dict[str, ExtractionRecord] = {}


# ---------------------------------------------------------------------------
# Validation Rules (Agent 7)
# ---------------------------------------------------------------------------

class ValidationRule(BaseModel):
    rule_id: str
    description: str
    rule_type: Literal["tally", "plausibility_bound"]
    formula: str          # expression using kpi_id strings as variable names,
                          # evaluated via simpleeval — e.g.
                          # "abs(revenue - (cogs + gross_profit)) <= tolerance"
    participating_kpi_ids: list[str]
    tolerance: float = 0.0
