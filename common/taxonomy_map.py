"""
Taxonomy Map loader and vocabulary helpers.

Loaded once at process/pipeline startup (same fail-fast-on-startup pattern as
report_ingestion/industry_map.py).  Both Agent 1 and Agent 2 should call these
functions once and cache the results rather than re-loading per document.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from common.schemas import ExtractionLedger, ExtractionRecord, ReportMetadata, TaxonomyEntry

# Sentinel values that appear in `applicable_industries` to mean "applies to
# every industry" — they are not themselves valid constrained choices for the LLM.
_INDUSTRY_WILDCARDS = {"All", "Industry Specific"}


def load_taxonomy_map(path: str) -> list[TaxonomyEntry]:
    """Load and validate the Taxonomy Map JSON at process startup.

    Mirrors the fail-fast pattern of load_company_reference_map() — any entry
    that fails Pydantic validation causes an immediate ValueError with the
    offending entry's kpi_id so the misconfiguration is caught on startup rather
    than mid-pipeline.

    Args:
        path: Filesystem path to the taxonomy JSON file.  Always sourced from
            config.settings.TAXONOMY_MAP_PATH — never hardcoded.

    Returns:
        Validated list[TaxonomyEntry].

    Raises:
        ValueError: If the file is not valid JSON, is not a list, or any entry
            fails Pydantic validation (message includes the offending kpi_id).
    """
    raw = Path(path).read_text(encoding="utf-8")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Taxonomy map at '{path}' is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, list):
        raise ValueError(
            f"Taxonomy map at '{path}' must be a JSON array, got {type(data).__name__}"
        )

    entries: list[TaxonomyEntry] = []
    for idx, item in enumerate(data):
        try:
            entries.append(TaxonomyEntry.model_validate(item))
        except ValidationError as exc:
            kpi_id = (
                item.get("kpi_id", "<unknown>") if isinstance(item, dict) else "<unknown>"
            )
            raise ValueError(
                f"Taxonomy map entry {idx} (kpi_id='{kpi_id}') failed validation: {exc}"
            ) from exc

    return entries


def get_canonical_section_vocabulary(taxonomy: list[TaxonomyEntry]) -> list[str]:
    """Return the deduplicated union of every canonical_sections value in the taxonomy.

    This is the controlled vocabulary Agent 2 aligns raw section headers against.
    Order is stable (sorted) so that dynamically-built Literal types are
    deterministic across runs.

    Args:
        taxonomy: Output of load_taxonomy_map().

    Returns:
        Sorted, deduplicated list[str] of canonical section names.
    """
    seen: set[str] = set()
    for entry in taxonomy:
        seen.update(entry.canonical_sections)
    return sorted(seen)


def filter_applicable_taxonomy(
    taxonomy: list[TaxonomyEntry],
    report_metadata: ReportMetadata,
) -> list[TaxonomyEntry]:
    """Return only the TaxonomyEntry rows applicable to this report.

    Per the main spec: an empty/missing applicable_industries,
    applicable_report_types, or applicable_accounting_standards means the KPI
    applies to ALL values for that dimension.  A non-empty field restricts the
    KPI to only the listed values.

    Args:
        taxonomy: Full load_taxonomy_map() output.
        report_metadata: This report's industry, report_type, and
            accounting_standard — all three are checked independently.

    Returns:
        Filtered list[TaxonomyEntry].
    """
    result: list[TaxonomyEntry] = []
    for entry in taxonomy:
        if entry.applicable_industries and report_metadata.industry not in entry.applicable_industries:
            continue
        if entry.applicable_report_types and report_metadata.report_type not in entry.applicable_report_types:
            continue
        if (
            entry.applicable_accounting_standards
            and report_metadata.accounting_standard not in entry.applicable_accounting_standards
        ):
            continue
        result.append(entry)
    return result


def initialize_extraction_ledger(
    filtered_taxonomy: list[TaxonomyEntry],
    fiscal_year: str,
) -> ExtractionLedger:
    """Create the starting ledger — every applicable KPI marked not_found.

    Args:
        filtered_taxonomy: Output of filter_applicable_taxonomy().
        fiscal_year: report_metadata.fiscal_year, stamped onto every record so
            Tier 1's fiscal-year column matching has it in one place.

    Returns:
        ExtractionLedger with one ExtractionRecord per taxonomy entry,
        all status="not_found", confidence=0.0.
    """
    records = {
        entry.kpi_id: ExtractionRecord(kpi_id=entry.kpi_id, fiscal_year=fiscal_year)
        for entry in filtered_taxonomy
    }
    return ExtractionLedger(records=records)


def get_industry_vocabulary(taxonomy: list[TaxonomyEntry]) -> list[str]:
    """Return the deduplicated industry vocabulary from the taxonomy.

    Excludes the wildcard sentinels "All" and "Industry Specific" — those are
    taxonomy-level wildcards, not valid constrained choices for the LLM.

    Used by Agent 1's industry-classification fallback (retrofitting the ad-hoc
    JSON parsing that existed in report_ingestion/pipeline.py).

    Args:
        taxonomy: Output of load_taxonomy_map().

    Returns:
        Sorted, deduplicated list[str] of valid industry names.
    """
    seen: set[str] = set()
    for entry in taxonomy:
        for ind in entry.applicable_industries:
            if ind not in _INDUSTRY_WILDCARDS:
                seen.add(ind)
    return sorted(seen)
