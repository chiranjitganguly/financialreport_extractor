import re
from typing import Optional

from report_ingestion.schemas import AccountingStandardResult, CompanyLookupResult

# Each entry: (compiled pattern, standard value).
# More specific multi-word phrases come before bare acronyms to avoid
# short-circuit on a standalone "IFRS" that might appear in a US-GAAP
# reconciliation footnote referencing both standards.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # IFRS
    (
        re.compile(r"international\s+financial\s+reporting\s+standards", re.IGNORECASE),
        "IFRS",
    ),
    (re.compile(r"\bIFRS\b"), "IFRS"),
    # US-GAAP — long-form phrases first
    (
        re.compile(
            r"generally\s+accepted\s+accounting\s+principles\s+in\s+the\s+united\s+states",
            re.IGNORECASE,
        ),
        "US-GAAP",
    ),
    (
        re.compile(
            r"accounting\s+principles\s+generally\s+accepted\s+in\s+the\s+united\s+states",
            re.IGNORECASE,
        ),
        "US-GAAP",
    ),
    (
        re.compile(
            r"u\.?s\.?\s+generally\s+accepted\s+accounting\s+principles",
            re.IGNORECASE,
        ),
        "US-GAAP",
    ),
    (re.compile(r"\bu\.?s\.?\s+gaap\b", re.IGNORECASE), "US-GAAP"),
    # IND-AS
    (re.compile(r"indian\s+accounting\s+standards", re.IGNORECASE), "IND-AS"),
    (re.compile(r"\bInd[\s-]AS\b", re.IGNORECASE), "IND-AS"),
]


def detect_accounting_standard_deterministic(excerpt: str) -> Optional[AccountingStandardResult]:
    """Stage A: regex search for explicit accounting-standard statements (no LLM).

    Looks for phrases such as "prepared in accordance with International Financial
    Reporting Standards" or "in conformity with U.S. GAAP". The document's own
    claim takes precedence over the company reference map (Stage B) because a
    specific filing can legitimately use a different standard than the company's
    usual one (e.g. a US-GAAP reconciliation note inside an otherwise-IFRS filing).

    Args:
        excerpt: Output of get_classification_excerpt(). If accounting-standard
            statements in sample documents tend to appear deeper in the notes
            section than the cover-page excerpt covers, widen the excerpt for
            this specific check rather than changing the shared default in
            get_classification_excerpt().

    Returns:
        AccountingStandardResult(confidence=1.0, source="document_statement") if
        a statement is matched. None if no statement is found, which falls through
        to detect_accounting_standard_from_map() (Stage B).
    """
    for pattern, standard in _PATTERNS:
        match = pattern.search(excerpt)
        if match:
            return AccountingStandardResult(
                standard=standard,  # type: ignore[arg-type]
                confidence=1.0,
                source="document_statement",
                evidence=match.group(0),
            )
    return None


def detect_accounting_standard_from_map(
    lookup_result: Optional[CompanyLookupResult],
) -> Optional[AccountingStandardResult]:
    """Stage B: derive accounting standard from the company reference map entry.

    Only produces a result when both conditions hold: (a) the company was matched
    above the fuzzy cutoff, and (b) that entry's ``accounting_standard`` field is
    not null (it is optional in the source data).

    Args:
        lookup_result: The SAME CompanyLookupResult already computed once for the
            Industry Selector — do NOT call lookup_company() again here.

    Returns:
        AccountingStandardResult(confidence=lookup_result.match_score,
        source="company_map") when both conditions are met. None otherwise, which
        falls through to run_classification_fallback() (Stage C).
    """
    if lookup_result is None:
        return None
    if lookup_result.matched_entry.accounting_standard is None:
        return None

    return AccountingStandardResult(
        standard=lookup_result.matched_entry.accounting_standard,
        confidence=lookup_result.match_score,
        source="company_map",
    )
