from typing import Optional

from report_ingestion.schemas import CompanyLookupResult, IndustryResult


def detect_industry_from_map(
    lookup_result: Optional[CompanyLookupResult],
) -> Optional[IndustryResult]:
    """Stage B: derive industry from the company reference map entry (no Stage A).

    The company reference map is the primary source for industry; the LLM fallback
    in run_classification_fallback() is the only alternative. There is no
    deterministic Stage A for industry.

    Args:
        lookup_result: Output of lookup_company(), called once in pipeline.py with
            the company name from extract_company_name_llm().

    Returns:
        IndustryResult(confidence=lookup_result.match_score, source="company_map")
        if lookup_result is not None. None if the company was not found above the
        fuzzy cutoff, which falls through to run_classification_fallback() (Stage C).
    """
    if lookup_result is None:
        return None

    return IndustryResult(
        industry=lookup_result.matched_entry.industry,
        confidence=lookup_result.match_score,
        source="company_map",
    )
