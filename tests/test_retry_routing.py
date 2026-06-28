"""Unit tests for retry_controller/routing.py — determine_retry_tier()."""

import pytest

from common.schemas import ExtractionRecord
from retry_controller.routing import determine_retry_tier


def _record(status="flagged", method=None):
    return ExtractionRecord(
        kpi_id="K1",
        fiscal_year="FY2024",
        status=status,
        method=method,
        confidence=0.9,
    )


# ---------------------------------------------------------------------------
# not_found → always tier3_broader regardless of method
# ---------------------------------------------------------------------------

def test_not_found_no_method():
    assert determine_retry_tier(_record(status="not_found", method=None)) == "tier3_broader"


def test_not_found_with_deterministic_method():
    assert determine_retry_tier(_record(status="not_found", method="deterministic")) == "tier3_broader"


def test_not_found_with_llm_method():
    assert determine_retry_tier(_record(status="not_found", method="llm")) == "tier3_broader"


# ---------------------------------------------------------------------------
# flagged — route by method
# ---------------------------------------------------------------------------

def test_flagged_deterministic_to_tier2():
    assert determine_retry_tier(_record(status="flagged", method="deterministic")) == "tier1_to_tier2"


def test_flagged_semantic_to_tier3():
    assert determine_retry_tier(_record(status="flagged", method="semantic")) == "tier2_to_tier3"


def test_flagged_llm_recheck():
    assert determine_retry_tier(_record(status="flagged", method="llm")) == "tier3_recheck"


def test_flagged_none_method_defaults_to_recheck():
    # method=None with flagged status — treated as llm recheck
    assert determine_retry_tier(_record(status="flagged", method=None)) == "tier3_recheck"
