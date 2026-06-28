"""End-to-end pipeline tests with all LLM calls mocked."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from unittest.mock import AsyncMock as _AsyncMock

from report_ingestion.converter import DocumentConversionError
from report_ingestion.schemas import (
    AccountingStandardResult,
    ReportIngestionOutput,
    CompanyNameResult,
    DoclingConversionResult,
    IndustryResult,
    LanguageResult,
    ReportTypeResult,
)

# ---------------------------------------------------------------------------
# Shared mock return values
# ---------------------------------------------------------------------------

_NARRATIVE = "British Telecom Group\nAnnual Report 2024\n\nPrepared under IFRS."
_REPORT_ID = "test-report-001"
_FILE_PATH = "/fake/path/report.pdf"

_LANG = LanguageResult(language="en", confidence=0.99)
_RT = ReportTypeResult(report_type="annual_report", confidence=1.0, source="document_marker")
_STD = AccountingStandardResult(standard="IFRS", confidence=1.0, source="document_statement")
_IND = IndustryResult(industry="Telecommunications", confidence=0.95, source="company_map")
_CN = CompanyNameResult(company_name="British Telecom Group", confidence=0.98)

_DOCLING_RESULT = DoclingConversionResult(
    narrative_markdown=_NARRATIVE,
    docling_document=MagicMock(),
    page_count=10,
)


def _make_lookup_result():
    from report_ingestion.schemas import CompanyLookupResult, CompanyReferenceEntry
    return CompanyLookupResult(
        matched_entry=CompanyReferenceEntry(
            company_name="British Telecom Group",
            industry="Telecommunications",
            accounting_standard="IFRS",
            country="United Kingdom",
        ),
        match_score=0.97,
    )


# ---------------------------------------------------------------------------
# Helpers — context manager that patches the full pipeline
# ---------------------------------------------------------------------------

def _base_patches(
    convert_side_effect=None,
    lang_return=_LANG,
    rt_return=_RT,
    std_a_return=_STD,
    cn_return=_CN,
    lookup_return=None,
    std_map_return=None,
    ind_map_return=_IND,
    fallback_return=None,
    enqueue_return=None,
):
    """Return a dict of patch targets → mock kwargs for the happy-path scenario."""
    if lookup_return is None:
        lookup_return = _make_lookup_result()

    from unittest.mock import patch as _patch
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        with (
            patch("report_ingestion.pipeline.convert_document",
                  side_effect=convert_side_effect,
                  return_value=_DOCLING_RESULT if convert_side_effect is None else None),
            patch("report_ingestion.pipeline.convert_document_fallback",
                  return_value=_NARRATIVE),
            patch("report_ingestion.pipeline.detect_language",
                  return_value=lang_return),
            patch("report_ingestion.pipeline.detect_report_type_deterministic",
                  return_value=rt_return),
            patch("report_ingestion.pipeline.detect_accounting_standard_deterministic",
                  return_value=std_a_return),
            patch("report_ingestion.pipeline.extract_company_name_llm",
                  new_callable=AsyncMock, return_value=cn_return),
            patch("report_ingestion.pipeline._get_reference_map", return_value=[]),
            patch("report_ingestion.pipeline.lookup_company", return_value=lookup_return),
            patch("report_ingestion.pipeline.detect_accounting_standard_from_map",
                  return_value=std_map_return),
            patch("report_ingestion.pipeline.detect_industry_from_map",
                  return_value=ind_map_return),
            patch("report_ingestion.pipeline._get_valid_industries", return_value=[]),
            patch("report_ingestion.pipeline.run_classification_fallback",
                  new_callable=AsyncMock, return_value=fallback_return),
            patch("report_ingestion.pipeline.save_agent_run",
                  new_callable=AsyncMock),
            patch("report_ingestion.pipeline.hitl_queue") as mock_hitl,
        ):
            mock_hitl.enqueue_for_review = AsyncMock(return_value=enqueue_return)
            yield mock_hitl

    return _ctx


# ---------------------------------------------------------------------------
# run_report_ingestion — happy path
# ---------------------------------------------------------------------------

class TestRunAgent1HappyPath:
    async def test_returns_ready_status(self):
        with _base_patches()():
            from report_ingestion.pipeline import run_report_ingestion
            result = await run_report_ingestion(_FILE_PATH, _REPORT_ID)
        assert result.status == "ready"

    async def test_report_metadata_populated(self):
        with _base_patches()():
            from report_ingestion.pipeline import run_report_ingestion
            result = await run_report_ingestion(_FILE_PATH, _REPORT_ID)
        assert result.report_metadata is not None
        assert result.report_metadata.report_id == _REPORT_ID
        assert result.report_metadata.report_type == "annual_report"
        assert result.report_metadata.language == "en"
        assert result.report_metadata.accounting_standard == "IFRS"
        assert result.report_metadata.industry == "Telecommunications"
        assert result.report_metadata.country == "United Kingdom"

    async def test_narrative_markdown_in_output(self):
        with _base_patches()():
            from report_ingestion.pipeline import run_report_ingestion
            result = await run_report_ingestion(_FILE_PATH, _REPORT_ID)
        assert result.narrative_markdown == _NARRATIVE

    async def test_no_flagged_fields(self):
        with _base_patches()():
            from report_ingestion.pipeline import run_report_ingestion
            result = await run_report_ingestion(_FILE_PATH, _REPORT_ID)
        assert result.flagged_fields == []

    async def test_hitl_enqueue_not_called(self):
        with _base_patches()() as mock_hitl:
            from report_ingestion.pipeline import run_report_ingestion
            await run_report_ingestion(_FILE_PATH, _REPORT_ID)
        mock_hitl.enqueue_for_review.assert_not_called()


# ---------------------------------------------------------------------------
# run_report_ingestion — HITL path (low-confidence field)
# ---------------------------------------------------------------------------

class TestRunAgent1HitlPath:
    async def test_returns_awaiting_input_when_low_confidence(self):
        low_conf_industry = IndustryResult(
            industry="unknown", confidence=0.0, source="llm_fallback"
        )
        with _base_patches(ind_map_return=low_conf_industry)():
            from report_ingestion.pipeline import run_report_ingestion
            result = await run_report_ingestion(_FILE_PATH, _REPORT_ID)
        assert result.status == "awaiting_input"

    async def test_flagged_fields_populated(self):
        low_conf_industry = IndustryResult(
            industry="unknown", confidence=0.0, source="llm_fallback"
        )
        with _base_patches(ind_map_return=low_conf_industry)():
            from report_ingestion.pipeline import run_report_ingestion
            result = await run_report_ingestion(_FILE_PATH, _REPORT_ID)
        assert len(result.flagged_fields) >= 1
        assert any(f.field_name == "industry" for f in result.flagged_fields)

    async def test_report_metadata_is_none(self):
        low_conf_industry = IndustryResult(
            industry="unknown", confidence=0.0, source="llm_fallback"
        )
        with _base_patches(ind_map_return=low_conf_industry)():
            from report_ingestion.pipeline import run_report_ingestion
            result = await run_report_ingestion(_FILE_PATH, _REPORT_ID)
        assert result.report_metadata is None

    async def test_enqueue_called_with_report_id(self):
        low_conf_industry = IndustryResult(
            industry="unknown", confidence=0.0, source="llm_fallback"
        )
        with _base_patches(ind_map_return=low_conf_industry)() as mock_hitl:
            from report_ingestion.pipeline import run_report_ingestion
            await run_report_ingestion(_FILE_PATH, _REPORT_ID)
        mock_hitl.enqueue_for_review.assert_called_once()
        call_args = mock_hitl.enqueue_for_review.call_args
        assert call_args[0][0] == _REPORT_ID


# ---------------------------------------------------------------------------
# run_report_ingestion — Docling fallback path
# ---------------------------------------------------------------------------

class TestRunAgent1DoclingFallback:
    async def test_fallback_used_when_docling_raises(self):
        with (
            patch("report_ingestion.pipeline.convert_document",
                  side_effect=DocumentConversionError("corrupt file")),
            patch("report_ingestion.pipeline.convert_document_fallback",
                  return_value=_NARRATIVE) as mock_fallback,
            patch("report_ingestion.pipeline.detect_language", return_value=_LANG),
            patch("report_ingestion.pipeline.detect_report_type_deterministic", return_value=_RT),
            patch("report_ingestion.pipeline.detect_accounting_standard_deterministic", return_value=_STD),
            patch("report_ingestion.pipeline.extract_company_name_llm",
                  new_callable=AsyncMock, return_value=_CN),
            patch("report_ingestion.pipeline._get_reference_map", return_value=[]),
            patch("report_ingestion.pipeline.lookup_company", return_value=_make_lookup_result()),
            patch("report_ingestion.pipeline.detect_accounting_standard_from_map", return_value=None),
            patch("report_ingestion.pipeline.detect_industry_from_map", return_value=_IND),
            patch("report_ingestion.pipeline._get_valid_industries", return_value=[]),
            patch("report_ingestion.pipeline.run_classification_fallback",
                  new_callable=AsyncMock),
            patch("report_ingestion.pipeline.save_agent_run", new_callable=AsyncMock),
            patch("report_ingestion.pipeline.hitl_queue"),
        ):
            from report_ingestion.pipeline import run_report_ingestion
            result = await run_report_ingestion(_FILE_PATH, _REPORT_ID)
            mock_fallback.assert_called_once_with(_FILE_PATH)
        assert result.narrative_markdown == _NARRATIVE


# ---------------------------------------------------------------------------
# run_report_ingestion — Stage C fallback triggered
# ---------------------------------------------------------------------------

class TestRunAgent1StageC:
    async def test_stage_c_called_when_stages_a_b_miss(self):
        from report_ingestion.schemas import ClassificationFallbackResult
        fallback_result = ClassificationFallbackResult(
            report_type=_RT,
            accounting_standard=_STD,
            industry=_IND,
        )
        with (
            patch("report_ingestion.pipeline.convert_document", return_value=_DOCLING_RESULT),
            patch("report_ingestion.pipeline.detect_language", return_value=_LANG),
            patch("report_ingestion.pipeline.detect_report_type_deterministic", return_value=None),
            patch("report_ingestion.pipeline.detect_accounting_standard_deterministic", return_value=None),
            patch("report_ingestion.pipeline.extract_company_name_llm",
                  new_callable=AsyncMock, return_value=_CN),
            patch("report_ingestion.pipeline._get_reference_map", return_value=[]),
            patch("report_ingestion.pipeline.lookup_company", return_value=None),
            patch("report_ingestion.pipeline.detect_accounting_standard_from_map", return_value=None),
            patch("report_ingestion.pipeline.detect_industry_from_map", return_value=None),
            patch("report_ingestion.pipeline._get_valid_industries", return_value=["Technology"]),
            patch("report_ingestion.pipeline.run_classification_fallback",
                  new_callable=AsyncMock, return_value=fallback_result) as mock_fallback,
            patch("report_ingestion.pipeline.save_agent_run", new_callable=AsyncMock),
            patch("report_ingestion.pipeline.hitl_queue"),
        ):
            from report_ingestion.pipeline import run_report_ingestion
            result = await run_report_ingestion(_FILE_PATH, _REPORT_ID)
            mock_fallback.assert_called_once()
        assert result.status == "ready"

    async def test_stage_c_not_called_when_all_resolved(self):
        with (
            patch("report_ingestion.pipeline.convert_document", return_value=_DOCLING_RESULT),
            patch("report_ingestion.pipeline.detect_language", return_value=_LANG),
            patch("report_ingestion.pipeline.detect_report_type_deterministic", return_value=_RT),
            patch("report_ingestion.pipeline.detect_accounting_standard_deterministic", return_value=_STD),
            patch("report_ingestion.pipeline.extract_company_name_llm",
                  new_callable=AsyncMock, return_value=_CN),
            patch("report_ingestion.pipeline._get_reference_map", return_value=[]),
            patch("report_ingestion.pipeline.lookup_company", return_value=_make_lookup_result()),
            patch("report_ingestion.pipeline.detect_accounting_standard_from_map", return_value=None),
            patch("report_ingestion.pipeline.detect_industry_from_map", return_value=_IND),
            patch("report_ingestion.pipeline._get_valid_industries", return_value=[]),
            patch("report_ingestion.pipeline.run_classification_fallback",
                  new_callable=AsyncMock) as mock_fallback,
            patch("report_ingestion.pipeline.save_agent_run", new_callable=AsyncMock),
            patch("report_ingestion.pipeline.hitl_queue"),
        ):
            from report_ingestion.pipeline import run_report_ingestion
            await run_report_ingestion(_FILE_PATH, _REPORT_ID)
            mock_fallback.assert_not_called()


# ---------------------------------------------------------------------------
# ingest_input_dir
# ---------------------------------------------------------------------------

class TestIngestInputDir:
    async def test_raises_for_nonexistent_directory(self):
        from report_ingestion.pipeline import ingest_input_dir
        with pytest.raises(FileNotFoundError):
            await ingest_input_dir("/nonexistent/path/that/does/not/exist")

    async def test_returns_empty_dict_for_empty_directory(self, tmp_path):
        from report_ingestion.pipeline import ingest_input_dir
        result = await ingest_input_dir(str(tmp_path))
        assert result == {}

    async def test_ignores_non_report_files(self, tmp_path):
        (tmp_path / "notes.txt").write_text("not a report")
        (tmp_path / "data.csv").write_text("col1,col2")
        from report_ingestion.pipeline import ingest_input_dir
        result = await ingest_input_dir(str(tmp_path))
        assert result == {}

    async def test_processes_pdf_files(self, tmp_path):
        (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4 fake")
        with _base_patches()():
            from report_ingestion.pipeline import ingest_input_dir
            result = await ingest_input_dir(str(tmp_path))
        assert len(result) == 1

    async def test_stable_report_ids_across_runs(self, tmp_path):
        (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4 fake")
        with _base_patches()():
            from report_ingestion.pipeline import ingest_input_dir
            result1 = await ingest_input_dir(str(tmp_path))
        with _base_patches()():
            result2 = await ingest_input_dir(str(tmp_path))
        assert list(result1.keys()) == list(result2.keys())
