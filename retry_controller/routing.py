"""Agent 8 — Retry Controller: tier routing.

Determines which extraction tier should handle a retry for a flagged or
never-found KPI.  The design doc's "escalate one tier up" interpretation:

  deterministic-flagged  → semantic retrieval retry
  semantic-flagged       → llm extraction retry
  llm-flagged            → llm recheck with the tally discrepancy in the prompt
  not_found (any tier)   → llm broader (look harder; no specific discrepancy to describe)
"""

from __future__ import annotations

from typing import Literal

from common.schemas import ExtractionRecord

RetryTier = Literal["tier1_to_tier2", "tier2_to_tier3", "tier3_recheck", "tier3_broader"]


def determine_retry_tier(record: ExtractionRecord) -> RetryTier:
    """Map a flagged/not_found record to the retry strategy to use.

    Routing table (see design doc §2):
        method="deterministic" + status="flagged"  → tier1_to_tier2
        method="semantic"      + status="flagged"  → tier2_to_tier3
        method="llm"           + status="flagged"  → tier3_recheck
        status="not_found" (any/no method)         → tier3_broader

    Args:
        record: An ExtractionRecord with status "flagged" or "not_found".

    Returns:
        RetryTier literal indicating which extraction path to use.
    """
    if record.status == "not_found":
        return "tier3_broader"

    # status == "flagged"
    if record.method == "deterministic":
        return "tier1_to_tier2"
    if record.method == "semantic":
        return "tier2_to_tier3"
    # method == "llm" or None (treat as llm recheck)
    return "tier3_recheck"
