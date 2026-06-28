"""Tests for industry_map.py — load_company_reference_map() and lookup_company()."""
import json
from pathlib import Path

import pytest

from report_ingestion.industry_map import load_company_reference_map, lookup_company
from report_ingestion.schemas import CompanyReferenceEntry

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "company_reference_map.json"


# ---------------------------------------------------------------------------
# load_company_reference_map
# ---------------------------------------------------------------------------

class TestLoadCompanyReferenceMap:
    def test_loads_valid_file(self):
        entries = load_company_reference_map(str(FIXTURE_PATH))
        assert len(entries) > 0
        assert all(isinstance(e, CompanyReferenceEntry) for e in entries)

    def test_all_required_fields_present(self):
        entries = load_company_reference_map(str(FIXTURE_PATH))
        for entry in entries:
            assert entry.company_name
            assert entry.industry
            assert entry.country

    def test_nullable_accounting_standard_accepted(self, tmp_path):
        f = tmp_path / "map.json"
        f.write_text(json.dumps([
            {"company_name": "No Standard Corp", "industry": "Retail",
             "accounting_standard": None, "country": "US"}
        ]))
        entries = load_company_reference_map(str(f))
        assert entries[0].accounting_standard is None

    def test_invalid_json_raises_value_error(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not valid json {{{")
        with pytest.raises(ValueError, match="not valid JSON"):
            load_company_reference_map(str(f))

    def test_root_not_a_list_raises_value_error(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text('{"company_name": "Corp", "industry": "tech", "country": "US"}')
        with pytest.raises(ValueError, match="JSON array"):
            load_company_reference_map(str(f))

    def test_schema_error_includes_entry_index(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text(json.dumps([
            {"company_name": "Good Corp", "industry": "tech", "country": "US"},
            {"industry": "tech"},  # missing company_name and country
        ]))
        with pytest.raises(ValueError, match="entry 1"):
            load_company_reference_map(str(f))

    def test_schema_error_includes_company_name_when_present(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text(json.dumps([
            {"company_name": "Bad Corp"},  # missing industry and country
        ]))
        with pytest.raises(ValueError, match="Bad Corp"):
            load_company_reference_map(str(f))

    def test_empty_array_returns_empty_list(self, tmp_path):
        f = tmp_path / "empty.json"
        f.write_text("[]")
        assert load_company_reference_map(str(f)) == []


# ---------------------------------------------------------------------------
# lookup_company
# ---------------------------------------------------------------------------

class TestLookupCompany:
    def test_exact_match_returns_result(self, sample_reference_map):
        result = lookup_company("Acme Manufacturing Inc", sample_reference_map, 85.0)
        assert result is not None
        assert result.matched_entry.company_name == "Acme Manufacturing Inc"

    def test_fuzzy_partial_name_matches(self, sample_reference_map):
        result = lookup_company("Acme Manufacturing", sample_reference_map, 75.0)
        assert result is not None
        assert "Acme" in result.matched_entry.company_name

    def test_match_score_normalised_between_0_and_1(self, sample_reference_map):
        result = lookup_company("Acme Manufacturing Inc", sample_reference_map, 85.0)
        assert result is not None
        assert 0.0 <= result.match_score <= 1.0

    def test_high_score_for_exact_match(self, sample_reference_map):
        result = lookup_company("Acme Manufacturing Inc", sample_reference_map, 85.0)
        assert result is not None
        assert result.match_score >= 0.9

    def test_no_match_below_cutoff_returns_none(self, sample_reference_map):
        # Use a string with no token overlap with any fixture entry so WRatio
        # cannot inflate the score via shared substrings like "Corp".
        result = lookup_company("Municipal Water Authority", sample_reference_map, 85.0)
        assert result is None

    def test_high_cutoff_rejects_near_match(self, sample_reference_map):
        result = lookup_company("Acme Mfg", sample_reference_map, 99.0)
        assert result is None

    def test_empty_reference_map_returns_none(self):
        result = lookup_company("Any Corp", [], 85.0)
        assert result is None

    def test_returned_entry_has_correct_industry(self, sample_reference_map):
        result = lookup_company("Tata Consultancy Services", sample_reference_map, 85.0)
        assert result is not None
        assert result.matched_entry.industry == "Technology"

    def test_returned_entry_has_correct_country(self, sample_reference_map):
        result = lookup_company("Tata Consultancy Services", sample_reference_map, 85.0)
        assert result is not None
        assert result.matched_entry.country == "India"

    def test_similar_names_match_best_candidate(self, sample_reference_map):
        # Both "British Telecom Group" and "British Telecom Holdings" exist —
        # exact name should match the correct one.
        result = lookup_company("British Telecom Group", sample_reference_map, 85.0)
        assert result is not None
        assert result.matched_entry.company_name == "British Telecom Group"
