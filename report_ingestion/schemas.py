"""
Pydantic models for Agent 1 (Ingestion & Classification).

Shared pipeline contracts (ReportMetadata, TaxonomyEntry, TableElement, etc.)
have been moved to common.schemas so that downstream agents can import them
without depending on this package.  They are re-exported here unchanged so
that existing code and tests that import from report_ingestion.schemas
continue to work without modification.

Agent 1-specific types (classifiers, converters, HITL routing) are defined
below.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Re-export shared pipeline contracts from common.schemas
# (nothing is redefined here — existing importers will get the exact same types)
# ---------------------------------------------------------------------------

from common.schemas import (  # noqa: F401  (re-exported for backward compat)
    ChartElement,
    ConflictingValue,
    ExtractionAttempt,
    ExtractionLedger,
    ExtractionRecord,
    FootnoteAnchor,
    FootnoteElement,
    ReportMetadata,
    Section,
    TableCell,
    TableElement,
    TableRow,
    TaxonomyEntry,
)

# ---------------------------------------------------------------------------
# Company reference map (design doc §2)
# ---------------------------------------------------------------------------


class CompanyReferenceEntry(BaseModel):
    company_name: str
    industry: str
    accounting_standard: Optional[Literal["IFRS", "US-GAAP", "IND-AS", "OTHER"]] = None
    country: str


class CompanyLookupResult(BaseModel):
    matched_entry: CompanyReferenceEntry
    match_score: float  # normalised 0-1 (rapidfuzz ratio / 100)


# ---------------------------------------------------------------------------
# Document conversion (design doc §3)
# ---------------------------------------------------------------------------


class DoclingConversionResult(BaseModel):
    narrative_markdown: str
    docling_document: object  # raw DoclingDocument, passed through to Agent 1b
    page_count: int

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Classifier result models (design doc §4)
# ---------------------------------------------------------------------------


class LanguageResult(BaseModel):
    language: str  # ISO 639-1
    confidence: float


class CompanyNameResult(BaseModel):
    company_name: str
    confidence: float


class ReportTypeResult(BaseModel):
    report_type: Literal["annual_report", "quarterly_report", "regulatory_filing"]
    confidence: float
    source: Literal["document_marker", "llm_fallback"]
    evidence: Optional[str] = None


class AccountingStandardResult(BaseModel):
    standard: Literal["IFRS", "US-GAAP", "IND-AS", "OTHER"]
    confidence: float
    source: Literal["document_statement", "company_map", "llm_fallback"]
    evidence: Optional[str] = None


class IndustryResult(BaseModel):
    industry: str
    confidence: float
    source: Literal["company_map", "llm_fallback"]


# ---------------------------------------------------------------------------
# Batched LLM fallback (design doc §4.6)
# ---------------------------------------------------------------------------


class ClassificationFallbackResult(BaseModel):
    report_type: Optional[ReportTypeResult] = None
    accounting_standard: Optional[AccountingStandardResult] = None
    industry: Optional[IndustryResult] = None


# ---------------------------------------------------------------------------
# Confidence routing (design doc §5)
# ---------------------------------------------------------------------------


class FieldReview(BaseModel):
    field_name: Literal["report_type", "language", "accounting_standard", "industry"]
    value: str
    confidence: float
    reason: str  # e.g. "0.18 < threshold 0.25"


# ---------------------------------------------------------------------------
# Agent 1 top-level output (design doc §8)
# ---------------------------------------------------------------------------


class ReportIngestionOutput(BaseModel):
    status: Literal["ready", "awaiting_input"]
    report_metadata: Optional[ReportMetadata] = None
    narrative_markdown: str
    flagged_fields: list[FieldReview] = []
