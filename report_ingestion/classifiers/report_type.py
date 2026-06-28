import re
from typing import Optional

from report_ingestion.schemas import ReportTypeResult

# Each entry: (compiled pattern, report_type value).
# Ordered most-specific first so a "Form 10-K" hit resolves before a bare
# "Annual Report" hit on the same document.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # SEC / regulatory form codes
    (re.compile(r"\b(?:form\s+)?10-?K\b", re.IGNORECASE), "annual_report"),
    (re.compile(r"\b(?:form\s+)?10-?Q\b", re.IGNORECASE), "quarterly_report"),
    (re.compile(r"\b(?:form\s+)?20-?F\b", re.IGNORECASE), "annual_report"),
    (re.compile(r"\b(?:form\s+)?40-?F\b", re.IGNORECASE), "annual_report"),
    (re.compile(r"\b(?:form\s+)?(?:6-?K|8-?K)\b", re.IGNORECASE), "regulatory_filing"),
    # Self-identifying title text — compound forms before bare "annual/quarterly"
    # so "Semi-Annual Report" doesn't match the bare \bannual pattern first.
    (re.compile(r"\bsemi[\s-]annual\s+report\b", re.IGNORECASE), "quarterly_report"),
    (re.compile(r"\bhalf[\s-]year(?:ly)?\s+report\b", re.IGNORECASE), "quarterly_report"),
    (re.compile(r"\binterim\s+report\b", re.IGNORECASE), "quarterly_report"),
    (re.compile(r"\bquarterly\s+report\b", re.IGNORECASE), "quarterly_report"),
    (re.compile(r"\bannual\s+report\b", re.IGNORECASE), "annual_report"),
    (re.compile(r"\bprospectus\b", re.IGNORECASE), "regulatory_filing"),
    (re.compile(r"\bregulatory\s+filing\b", re.IGNORECASE), "regulatory_filing"),
]


def detect_report_type_deterministic(excerpt: str) -> Optional[ReportTypeResult]:
    """Stage A: regex/keyword search for explicit report-type markers (no LLM).

    Looks for SEC form-type strings ("10-K", "10-Q", "Form 20-F") and
    self-identifying title text ("Annual Report", "Quarterly Report",
    "Interim Report").

    Args:
        excerpt: Output of get_classification_excerpt().

    Returns:
        ReportTypeResult(confidence=1.0, source="document_marker") if a marker
        is found. None if no marker is matched, which falls through to
        run_classification_fallback() (Stage C — there is no separate Stage B
        for report type).
    """
    for pattern, report_type in _PATTERNS:
        match = pattern.search(excerpt)
        if match:
            return ReportTypeResult(
                report_type=report_type,  # type: ignore[arg-type]
                confidence=1.0,
                source="document_marker",
                evidence=match.group(0),
            )
    return None
