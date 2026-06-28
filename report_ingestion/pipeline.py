import asyncio
import logging
import uuid
from pathlib import Path
from typing import Literal, Optional

log = logging.getLogger(__name__)

from report_ingestion import hitl_queue
from report_ingestion.classifiers.accounting_standard import (
    detect_accounting_standard_deterministic,
    detect_accounting_standard_from_map,
)
from report_ingestion.classifiers.company_name import extract_company_name_llm
from report_ingestion.classifiers.industry import detect_industry_from_map
from report_ingestion.classifiers.language import detect_language
from report_ingestion.classifiers.report_type import detect_report_type_deterministic
from report_ingestion.confidence import route_confidence
from report_ingestion.config import settings
from common.config import settings as common_settings
from report_ingestion.converter import (
    DocumentConversionError,
    convert_document,
    convert_document_fallback,
    get_classification_excerpt,
)
from report_ingestion.fallback import run_classification_fallback
from report_ingestion.industry_map import load_company_reference_map, lookup_company
from report_ingestion.persistence import save_agent_run
from report_ingestion.schemas import (
    AccountingStandardResult,
    ReportIngestionOutput,
    CompanyReferenceEntry,
    IndustryResult,
    ReportMetadata,
    ReportTypeResult,
)
from common.taxonomy_map import (
    get_canonical_section_vocabulary,
    get_industry_vocabulary,
    load_taxonomy_map,
)
from section_parser.pipeline import run_section_parser
from section_parser.schemas import SectionParserOutput
from vector_indexer.pipeline import run_vector_indexer
from common.output_writer import (
    format_report_ingestion_md,
    format_section_parser_md,
    format_vector_indexer_md,
    write_agent_output,
)
import shutil
from collections import defaultdict

from extraction_pipeline import run_extraction_pipeline
from validation_retry_loop import run_validation_retry_loop
from consolidation.pipeline import run_consolidation
from consolidation.summary_writer import write_summary_report
from common.token_tracker import TokenUsageTracker
from common.timing_tracker import TimingTracker
from common.validation_rules import load_validation_rules_map
from common.taxonomy_map import filter_applicable_taxonomy

_SUPPORTED_EXTENSIONS = {".pdf", ".docx"}

# ---------------------------------------------------------------------------
# Module-level singletons — lazy-loaded on first call, not at import time,
# so test mocks applied before the first call take effect.
# ---------------------------------------------------------------------------

_reference_map: list[CompanyReferenceEntry] | None = None
_valid_industries: list[str] | None = None
_canonical_vocabulary: list[str] | None = None


def _get_reference_map() -> list[CompanyReferenceEntry]:
    global _reference_map
    if _reference_map is None:
        _reference_map = load_company_reference_map(settings.INDUSTRY_MAP_PATH)
    return _reference_map


def _get_valid_industries() -> list[str]:
    """Return the unique industry vocabulary from the KPI taxonomy map.

    Delegates to common.taxonomy_map.get_industry_vocabulary() which strips the
    sentinel values "All" and "Industry Specific" — those are taxonomy-level
    wildcards, not valid constrained choices for the LLM.
    """
    global _valid_industries
    if _valid_industries is None:
        try:
            taxonomy = load_taxonomy_map(common_settings.TAXONOMY_MAP_PATH)
            _valid_industries = get_industry_vocabulary(taxonomy)
        except Exception:
            _valid_industries = []
    return _valid_industries


def _get_canonical_vocabulary() -> list[str]:
    """Return the canonical section names from the KPI taxonomy map."""
    global _canonical_vocabulary
    if _canonical_vocabulary is None:
        try:
            taxonomy = load_taxonomy_map(common_settings.TAXONOMY_MAP_PATH)
            _canonical_vocabulary = get_canonical_section_vocabulary(taxonomy)
        except Exception:
            _canonical_vocabulary = []
    return _canonical_vocabulary


# ---------------------------------------------------------------------------
# Zero-confidence sentinel results — used when all three stages fail for a
# field so route_confidence can still flag it for HITL rather than crashing.
# ---------------------------------------------------------------------------

def _missing_report_type() -> ReportTypeResult:
    return ReportTypeResult(
        report_type="annual_report", confidence=0.0, source="llm_fallback"
    )


def _missing_accounting_standard() -> AccountingStandardResult:
    return AccountingStandardResult(
        standard="OTHER", confidence=0.0, source="llm_fallback"
    )


def _missing_industry() -> IndustryResult:
    return IndustryResult(industry="unknown", confidence=0.0, source="llm_fallback")


# ---------------------------------------------------------------------------
# Core per-document pipeline
# ---------------------------------------------------------------------------

async def run_report_ingestion(file_path: str, report_id: str) -> ReportIngestionOutput:
    """Top-level orchestration for Agent 1: ingest a report and classify its metadata.

    Steps (in order):
        1. Try convert_document(file_path); on DocumentConversionError fall back
           to convert_document_fallback(file_path).
        2. Build the classification excerpt via get_classification_excerpt().
        3. Run concurrently via asyncio.gather:
               - detect_language(excerpt)
               - detect_report_type_deterministic(excerpt)
               - detect_accounting_standard_deterministic(excerpt)
               - extract_company_name_llm(excerpt)
        4. lookup_result = lookup_company(company_name, reference_map, fuzzy_cutoff)
           [reference_map is loaded once at startup, not per call]
        5. Resolve Stage B results:
               accounting_standard = (Stage A result) or
                   detect_accounting_standard_from_map(lookup_result)
               industry = detect_industry_from_map(lookup_result)
        6. Collect needed_fields = fields still None after steps 3 and 5.
        7. If needed_fields is non-empty: call run_classification_fallback() once.
        8. country = lookup_result.matched_entry.country if lookup_result else None.
        9. route_confidence(...) with all final results and the configured threshold.
        10. If flagged_fields is empty: return ReportIngestionOutput(status="ready", ...).
            Otherwise: call hitl_queue.enqueue_for_review(...) and return
            ReportIngestionOutput(status="awaiting_input", ...).

    Args:
        file_path: Absolute path to the uploaded report file (PDF/DOCX).
        report_id: Unique identifier used for HITL queue tracking and as the
            key for all downstream agents.

    Returns:
        ReportIngestionOutput with status "ready" (ReportMetadata populated) or
        "awaiting_input" (flagged_fields populated, report queued for review).
    """
    token_tracker = TokenUsageTracker.activate()
    timing_tracker = TimingTracker()

    # ------------------------------------------------------------------
    # Step 1: Document conversion
    # ------------------------------------------------------------------
    t1 = timing_tracker.start("report_ingestion")
    docling_document = None
    try:
        conversion = convert_document(file_path)
        narrative_markdown = conversion.narrative_markdown
        docling_document = conversion.docling_document
    except DocumentConversionError:
        narrative_markdown = convert_document_fallback(file_path)

    # ------------------------------------------------------------------
    # Step 2: Shared excerpt for all classifiers
    # ------------------------------------------------------------------
    excerpt = get_classification_excerpt(
        narrative_markdown, settings.CLASSIFICATION_EXCERPT_MAX_CHARS
    )

    # ------------------------------------------------------------------
    # Step 3: Stage A classifiers + company name LLM — all concurrent.
    # Sync classifiers run in a thread so they don't block the event loop
    # while the LLM call is in flight.
    # ------------------------------------------------------------------
    (
        language_result,
        report_type_a,
        accounting_standard_a,
        company_name_result,
    ) = await asyncio.gather(
        asyncio.to_thread(detect_language, excerpt),
        asyncio.to_thread(detect_report_type_deterministic, excerpt),
        asyncio.to_thread(detect_accounting_standard_deterministic, excerpt),
        extract_company_name_llm(excerpt),
    )

    # ------------------------------------------------------------------
    # Step 4: Shared company lookup (consumed by both Stage B classifiers)
    # ------------------------------------------------------------------
    lookup_result = lookup_company(
        company_name_result.company_name,
        _get_reference_map(),
        settings.INDUSTRY_MAP_FUZZY_CUTOFF,
    )

    # ------------------------------------------------------------------
    # Step 5: Stage B
    # ------------------------------------------------------------------
    accounting_standard_b = (
        accounting_standard_a or detect_accounting_standard_from_map(lookup_result)
    )
    industry_b = detect_industry_from_map(lookup_result)

    # ------------------------------------------------------------------
    # Step 6: Collect fields still unresolved after Stages A + B
    # ------------------------------------------------------------------
    needed_fields: list[Literal["report_type", "accounting_standard", "industry"]] = []
    if report_type_a is None:
        needed_fields.append("report_type")
    if accounting_standard_b is None:
        needed_fields.append("accounting_standard")
    if industry_b is None:
        needed_fields.append("industry")

    # ------------------------------------------------------------------
    # Step 7: Stage C — single batched LLM call for all remaining fields
    # ------------------------------------------------------------------
    report_type_final = report_type_a
    accounting_standard_final = accounting_standard_b
    industry_final: Optional[IndustryResult] = industry_b

    if needed_fields:
        fallback = await run_classification_fallback(
            excerpt, needed_fields, _get_valid_industries()
        )
        if report_type_final is None:
            report_type_final = fallback.report_type
        if accounting_standard_final is None:
            accounting_standard_final = fallback.accounting_standard
        if industry_final is None:
            industry_final = fallback.industry

    # Guard: if a field is still None after all three stages, substitute a
    # zero-confidence sentinel so route_confidence can flag it for HITL.
    if report_type_final is None:
        report_type_final = _missing_report_type()
    if accounting_standard_final is None:
        accounting_standard_final = _missing_accounting_standard()
    if industry_final is None:
        industry_final = _missing_industry()

    # ------------------------------------------------------------------
    # Step 8: Country from company lookup (no confidence check)
    # ------------------------------------------------------------------
    country = lookup_result.matched_entry.country if lookup_result else None

    # ------------------------------------------------------------------
    # Step 9: Confidence routing
    # ------------------------------------------------------------------
    report_metadata, flagged_fields = route_confidence(
        report_type=report_type_final,
        language=language_result,
        accounting_standard=accounting_standard_final,
        industry=industry_final,
        country=country,
        threshold=settings.CLASSIFICATION_CONFIDENCE_THRESHOLD,
    )

    # ------------------------------------------------------------------
    # Step 10: Return or queue for human review
    # ------------------------------------------------------------------
    if not flagged_fields:
        # Stamp report_id and company_name — confidence.py leaves both as placeholders.
        report_metadata = report_metadata.model_copy(update={
            "report_id": report_id,
            "company_name": company_name_result.company_name,
        })
        output = ReportIngestionOutput(
            status="ready",
            report_metadata=report_metadata,
            narrative_markdown=narrative_markdown,
        )
        timing_tracker.stop(t1)
        await save_agent_run(report_id, "report_ingestion", output)
        write_agent_output(
            format_report_ingestion_md(output, report_metadata),
            report_metadata, "report_ingestion", common_settings.OUTPUT_DIR,
        )

        # ------------------------------------------------------------------
        # Hand off to Agent 2 — only when metadata is fully resolved.
        # Tables/charts/footnotes are empty until Agent 1b is built.
        # ------------------------------------------------------------------
        try:
            t2 = timing_tracker.start("section_parser")
            agent2_output: SectionParserOutput = await run_section_parser(
                docling_document=docling_document,
                report_metadata=report_metadata,
                narrative_markdown=narrative_markdown,
                tables=[],
                charts=[],
                footnotes=[],
                canonical_vocabulary=_get_canonical_vocabulary(),
                remaining_retry_budget=2,
            )
            timing_tracker.stop(t2)
            await save_agent_run(report_id, "section_parser", agent2_output)
            write_agent_output(
                format_section_parser_md(agent2_output, report_metadata),
                report_metadata, "section_parser", common_settings.OUTPUT_DIR,
            )

            t3 = timing_tracker.start("vector_indexer")
            try:
                agent3_output = await run_vector_indexer(
                    sections=agent2_output.sections,
                    report_metadata=report_metadata,
                )
            except Exception as _vi_exc:
                log.warning(
                    "Vector indexer unavailable (DB down?) — skipping: %s. "
                    "Semantic retrieval will return no results this run.",
                    _vi_exc,
                )
                from vector_indexer.schemas import VectorIndexerOutput
                agent3_output = VectorIndexerOutput(report_id=report_id)
            timing_tracker.stop(t3)
            await save_agent_run(report_id, "vector_indexer", agent3_output)
            write_agent_output(
                format_vector_indexer_md(agent3_output, report_metadata),
                report_metadata, "vector_indexer", common_settings.OUTPUT_DIR,
            )

            t4 = timing_tracker.start("extraction_cascade")
            ledger = run_extraction_pipeline(
                sections=agent2_output.sections,
                report_metadata=report_metadata,
                output_dir=common_settings.OUTPUT_DIR,
            )
            timing_tracker.stop(t4)
            await save_agent_run(report_id, "extraction_pipeline", ledger)

            # Snapshot ledger state before validation (for summary turn counts)
            import copy
            ledger_before_validation = copy.deepcopy(ledger)

            # Build dicts needed by Agent 7/8
            sections_by_canonical_name: dict = defaultdict(list)
            for sec in agent2_output.sections:
                sections_by_canonical_name[sec.section_name_canonical].append(sec)

            taxonomy = load_taxonomy_map(common_settings.TAXONOMY_MAP_PATH)
            filtered_taxonomy = filter_applicable_taxonomy(taxonomy, report_metadata)
            taxonomy_by_id = {entry.kpi_id: entry for entry in filtered_taxonomy}

            try:
                rules = load_validation_rules_map(common_settings.VALIDATION_RULES_MAP_PATH)
            except Exception:
                log.warning("Could not load validation rules from %s — skipping tally checks.",
                            common_settings.VALIDATION_RULES_MAP_PATH)
                rules = []

            t7 = timing_tracker.start("validation_retry_loop")
            remaining_retry_budget = max(0, 2 - agent2_output.retry_turns_used)
            validation_output = run_validation_retry_loop(
                ledger=ledger,
                rules=rules,
                taxonomy_by_id=taxonomy_by_id,
                sections_by_canonical_name=dict(sections_by_canonical_name),
                footnotes_by_id={},
                report_id=report_id,
                fiscal_year=report_metadata.fiscal_year,
                remaining_retry_budget=remaining_retry_budget,
                confidence_threshold=common_settings.EXTRACTION_CONFIDENCE_THRESHOLD,
                material_keywords=common_settings.FOOTNOTE_MATERIALITY_KEYWORDS,
            )
            timing_tracker.stop(t7)
            await save_agent_run(report_id, "validation_retry_loop", validation_output)

            t9 = timing_tracker.start("consolidation")
            final_report = run_consolidation(
                ledger=validation_output.ledger,
                report_id=report_id,
                fiscal_year=report_metadata.fiscal_year,
                report_metadata=report_metadata,
                token_tracker=token_tracker,
                output_dir=common_settings.OUTPUT_DIR,
            )
            timing_tracker.stop(t9)
            await save_agent_run(report_id, "consolidation", final_report)

            # Write summary markdown report
            try:
                summary_path = write_summary_report(
                    final_output=final_report,
                    validation_output=validation_output,
                    ledger_before_validation=ledger_before_validation,
                    report_metadata=report_metadata,
                    taxonomy_by_id=taxonomy_by_id,
                    token_tracker=token_tracker,
                    timing_tracker=timing_tracker,
                    output_dir=common_settings.OUTPUT_DIR,
                )
                log.info("Summary report written → %s", summary_path)
            except Exception:
                log.exception("Failed to write summary report for report_id=%s", report_id)

            # Archive processed input file
            try:
                archive_dir = Path(common_settings.OUTPUT_DIR) / "archive"
                archive_dir.mkdir(parents=True, exist_ok=True)
                src = Path(file_path)
                dest = archive_dir / src.name
                shutil.move(str(src), str(dest))
                log.info("Archived input file %s → %s", src.name, dest)
            except Exception:
                log.exception("Failed to archive input file %s", file_path)

        except Exception:
            log.exception(
                "Agent 2/3/4 failed for report_id=%s; Agent 1 result preserved.", report_id
            )

        return output

    resolved_fields = {
        "report_type": report_type_final.report_type,
        "language": language_result.language,
        "accounting_standard": accounting_standard_final.standard,
        "industry": industry_final.industry,
        "country": country,
    }
    await hitl_queue.enqueue_for_review(report_id, resolved_fields, flagged_fields)
    output = ReportIngestionOutput(
        status="awaiting_input",
        narrative_markdown=narrative_markdown,
        flagged_fields=flagged_fields,
    )
    await save_agent_run(report_id, "report_ingestion", output)
    # Write Agent 1 output even when awaiting review — shows what was flagged.
    # route_confidence returns None for report_metadata in this branch, so
    # construct a minimal one from the best-guess classifier results for the
    # filename and format function.
    _meta_for_output = ReportMetadata(
        report_id=report_id,
        report_type=report_type_final.report_type,
        language=language_result.language,
        accounting_standard=accounting_standard_final.standard,
        industry=industry_final.industry,
        fiscal_year="unknown",
        company_name=company_name_result.company_name,
        country=country,
    )
    write_agent_output(
        format_report_ingestion_md(output, _meta_for_output),
        _meta_for_output, "report_ingestion", common_settings.OUTPUT_DIR,
    )
    return output


# ---------------------------------------------------------------------------
# Directory-level entry point
# ---------------------------------------------------------------------------

async def ingest_input_dir(input_dir: str | None = None) -> dict[str, ReportIngestionOutput]:
    """Process every supported report file found in the input directory.

    Scans ``input_dir`` (defaults to ``settings.INPUT_DIR``) for PDF and DOCX
    files and runs ``run_report_ingestion`` on each one concurrently. Assigns a UUID
    ``report_id`` to each file derived from its stem so results are stable
    across reruns of the same file set.

    Args:
        input_dir: Path to scan. Defaults to ``settings.INPUT_DIR``.

    Returns:
        Mapping of ``report_id`` → ``ReportIngestionOutput`` for every file processed.

    Raises:
        FileNotFoundError: If the input directory does not exist.
    """
    directory = Path(input_dir or settings.INPUT_DIR)

    if not directory.exists():
        raise FileNotFoundError(f"Input directory '{directory}' does not exist.")

    report_files = [
        f for f in sorted(directory.iterdir())
        if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTENSIONS
    ]

    if not report_files:
        return {}

    # Derive a stable UUID from each filename stem so reruns produce the same IDs.
    report_ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, f.stem)) for f in report_files]

    raw = await asyncio.gather(
        *(run_report_ingestion(str(f.resolve()), rid) for f, rid in zip(report_files, report_ids)),
        return_exceptions=True,
    )

    output: dict[str, ReportIngestionOutput] = {}
    for rid, file_path_obj, result in zip(report_ids, report_files, raw):
        if isinstance(result, BaseException):
            log.error("Failed to process '%s' (report_id=%s): %s", file_path_obj.name, rid, result)
        else:
            output[rid] = result
    return output
