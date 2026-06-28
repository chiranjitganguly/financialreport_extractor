"""Extraction pipeline — orchestrates deterministic, semantic, and LLM extraction stages.

  - Deterministic extraction — exact/fuzzy table and text matching (deterministic_extractor/)
  - Semantic retrieval — vector-similarity search against the indexed chunks (semantic_retriever/)
  - LLM extraction — full-section context reading by an LLM (llm_extractor/)

Called by report_ingestion/pipeline.py after vector_indexer has persisted and
indexed all sections.
"""

from __future__ import annotations

import logging

from common.config import settings as common_settings
from common.output_writer import format_ledger_md, write_agent_output
from common.schemas import ExtractionLedger, ReportMetadata, Section, TaxonomyEntry
from common.taxonomy_map import (
    filter_applicable_taxonomy,
    initialize_extraction_ledger,
    load_taxonomy_map,
)
from deterministic_extractor.extraction import run_deterministic_extraction
from semantic_retriever.config import settings as semantic_retriever_settings
from semantic_retriever.retrieval import run_semantic_retrieval
from llm_extractor.extraction import run_llm_extraction

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_extraction_pipeline(
    sections: list[Section],
    report_metadata: ReportMetadata,
    output_dir: str | None = None,
) -> ExtractionLedger:
    """Run the full three-tier extraction cascade for one report.

    Args:
        sections: All sections produced by Agent 2 for this report.
        report_metadata: Full metadata from Agent 1.
        output_dir: If provided, write a Markdown trace file after each tier.

    Returns:
        Final ExtractionLedger after all three tiers.
    """
    taxonomy = load_taxonomy_map(common_settings.TAXONOMY_MAP_PATH)
    filtered = filter_applicable_taxonomy(taxonomy, report_metadata)
    ledger = initialize_extraction_ledger(filtered, report_metadata.fiscal_year)

    log.info(
        "report_id=%s — starting cascade: %d applicable KPIs, %d sections",
        report_metadata.report_id, len(filtered), len(sections),
    )

    # Stage 1 — deterministic extraction
    ledger = run_deterministic_extraction(
        ledger=ledger,
        filtered_taxonomy=filtered,
        sections=sections,
        fiscal_year=report_metadata.fiscal_year,
    )

    still_missing = sum(1 for r in ledger.records.values() if r.status == "not_found")
    log.info("After deterministic extraction: %d KPIs still not_found — forwarding to semantic retrieval", still_missing)
    if output_dir:
        write_agent_output(
            format_ledger_md(ledger, filtered, "deterministic_extractor"),
            report_metadata, "deterministic_extractor", output_dir,
        )

    # Stage 2 — semantic vector retrieval
    ledger = run_semantic_retrieval(
        ledger=ledger,
        filtered_taxonomy=filtered,
        report_id=report_metadata.report_id,
        fiscal_year=report_metadata.fiscal_year,
        top_k=semantic_retriever_settings.SEMANTIC_TOP_K,
    )

    still_missing = sum(1 for r in ledger.records.values() if r.status == "not_found")
    log.info("After semantic retrieval: %d KPIs still not_found — forwarding to LLM extraction", still_missing)
    if output_dir:
        write_agent_output(
            format_ledger_md(ledger, filtered, "semantic_retriever"),
            report_metadata, "semantic_retriever", output_dir,
        )

    # Stage 3 — LLM extraction
    ledger = run_llm_extraction(
        ledger=ledger,
        filtered_taxonomy=filtered,
        sections=sections,
        fiscal_year=report_metadata.fiscal_year,
    )

    if output_dir:
        write_agent_output(
            format_ledger_md(ledger, filtered, "llm_extractor"),
            report_metadata, "llm_extractor", output_dir,
        )

    found = sum(1 for r in ledger.records.values() if r.status == "found")
    review = sum(1 for r in ledger.records.values() if r.status == "needs_human_review")
    missing = sum(1 for r in ledger.records.values() if r.status == "not_found")
    log.info(
        "Cascade complete for report_id=%s: found=%d review=%d not_found=%d",
        report_metadata.report_id, found, review, missing,
    )

    return ledger
