"""Write per-agent Markdown output files for traceability.

Each agent writes one file per processed report using the naming convention:
    <CompanyName>_<reporttype>_<fiscal_year>_<agent_name>.md

Format functions accept agent output objects via duck typing (Any) so this
module can live in common/ without importing from agent-specific packages and
creating circular dependencies.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from common.schemas import ExtractionLedger, ReportMetadata, TaxonomyEntry

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path utilities
# ---------------------------------------------------------------------------

def _sanitize(s: str) -> str:
    """Strip characters unsafe in filenames; collapse whitespace to underscores."""
    s = re.sub(r"[^\w\s\-]", "", s)
    s = re.sub(r"[\s]+", "_", s.strip())
    return s


def build_output_path(
    report_metadata: ReportMetadata,
    agent_name: str,
    output_dir: str,
) -> Path:
    company = _sanitize(report_metadata.company_name or "unknown")
    report_type = _sanitize(report_metadata.report_type)
    fy = _sanitize(report_metadata.fiscal_year)
    filename = f"{company}_{report_type}_{fy}_{agent_name}.md"
    return Path(output_dir) / company / filename


def write_agent_output(
    content: str,
    report_metadata: ReportMetadata,
    agent_name: str,
    output_dir: str,
) -> Path:
    """Write content to the standard output path and return it."""
    path = build_output_path(report_metadata, agent_name, output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    log.info("Wrote %s output → %s", agent_name, path)
    return path


# ---------------------------------------------------------------------------
# Agent 1 formatter
# ---------------------------------------------------------------------------

def format_report_ingestion_md(output: Any, report_metadata: ReportMetadata) -> str:
    """Format Agent 1 classification output as Markdown.

    Args:
        output: ReportIngestionOutput instance (duck-typed to avoid circular import).
        report_metadata: Populated ReportMetadata (may be None if awaiting review).
    """
    lines: list[str] = ["# Report Ingestion — Document Ingestion & Classification", ""]

    lines += ["## Report Metadata", ""]
    lines += [
        "| Field | Value |",
        "|---|---|",
        f"| Company Name | {report_metadata.company_name or '—'} |",
        f"| Report ID | {report_metadata.report_id} |",
        f"| Report Type | {report_metadata.report_type} |",
        f"| Fiscal Year | {report_metadata.fiscal_year} |",
        f"| Industry | {report_metadata.industry} |",
        f"| Accounting Standard | {report_metadata.accounting_standard} |",
        f"| Language | {report_metadata.language} |",
        f"| Country | {report_metadata.country or '—'} |",
        f"| Status | {getattr(output, 'status', '—')} |",
        "",
    ]

    flagged: list[Any] = getattr(output, "flagged_fields", []) or []
    lines.append("## Flagged Fields (Awaiting Human Review)")
    lines.append("")
    if flagged:
        lines += [
            "| Field | Value | Confidence | Reason |",
            "|---|---|---|---|",
        ]
        for f in flagged:
            lines.append(
                f"| {f.field_name} | {f.value} | {f.confidence:.2f} | {f.reason} |"
            )
    else:
        lines.append("*None — all fields above the confidence threshold.*")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LangExtract section extraction formatter
# ---------------------------------------------------------------------------

def format_langextract_md(narrative_markdown: str, report_metadata: ReportMetadata) -> str:
    """Wrap the langextract section markdown with a standard report header.

    Args:
        narrative_markdown: H2-structured markdown produced by langextract_converter.
        report_metadata: Populated ReportMetadata for the header block.

    Returns:
        Complete markdown document ready to write to disk.
    """
    header = "\n".join([
        "# LangExtract — Document Section Extraction",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Company | {report_metadata.company_name or '—'} |",
        f"| Report Type | {report_metadata.report_type} |",
        f"| Fiscal Year | {report_metadata.fiscal_year} |",
        f"| Extraction Model | gpt-4o-mini (langextract) |",
        "",
        "---",
        "",
    ])
    return header + narrative_markdown


# ---------------------------------------------------------------------------
# Agent 2 formatter
# ---------------------------------------------------------------------------

def format_section_parser_md(output: Any, report_metadata: ReportMetadata) -> str:
    """Format Agent 2 section-parser output as Markdown.

    Args:
        output: SectionParserOutput instance (duck-typed).
        report_metadata: Report context for the header.
    """
    sections: list[Any] = getattr(output, "sections", []) or []
    retry_turns: int = getattr(output, "retry_turns_used", 0)
    has_unresolved: bool = getattr(output, "has_unresolved_sections", False)

    unresolved_count = sum(
        1 for s in sections if getattr(s, "alignment_source", "") == "best_guess_unresolved"
    )

    lines: list[str] = [
        "# Section Parser — Section Splitting & Taxonomy Alignment",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total Sections | {len(sections)} |",
        f"| Unresolved Sections | {unresolved_count} |",
        f"| Has Unresolved | {'Yes' if has_unresolved else 'No'} |",
        f"| Retry Turns Used | {retry_turns} |",
        "",
        "## Sections",
        "",
        "| # | Raw Name | Canonical Name | Alignment | Confidence | Pages | Tables | Charts | Footnotes |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for i, s in enumerate(sections, 1):
        pr = getattr(s, "page_range", (0, 0))
        pages = f"{pr[0]}–{pr[1]}"
        lines.append(
            f"| {i} "
            f"| {getattr(s, 'section_name_raw', '')} "
            f"| {getattr(s, 'section_name_canonical', '')} "
            f"| {getattr(s, 'alignment_source', '')} "
            f"| {getattr(s, 'alignment_confidence', 0.0):.2f} "
            f"| {pages} "
            f"| {len(getattr(s, 'tables', []))} "
            f"| {len(getattr(s, 'charts', []))} "
            f"| {len(getattr(s, 'footnotes', []))} |"
        )

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent 3 formatter
# ---------------------------------------------------------------------------

def format_vector_indexer_md(output: Any, report_metadata: ReportMetadata) -> str:
    """Format Agent 3 persistence output as Markdown.

    Args:
        output: VectorIndexerOutput instance (duck-typed).
        report_metadata: Report context for the header.
    """
    doc_keys: list[str] = getattr(output, "document_keys", []) or []
    chunks: int = getattr(output, "chunks_embedded", 0)

    lines: list[str] = [
        "# Vector Indexer — Persistence & Embedding",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Sections Persisted | {len(doc_keys)} |",
        f"| Total Chunks Embedded | {chunks} |",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Extraction ledger formatter (Agents 4, 5, 6)
# ---------------------------------------------------------------------------

_STATUS_ICON = {
    "found": "✓",
    "not_found": "—",
    "needs_human_review": "⚠",
    "flagged": "⚑",
}

_TIER_LABEL = {
    "deterministic_extractor": "# Deterministic Extractor — Exact & Fuzzy Table/Text Matching",
    "semantic_retriever": "# Semantic Retriever — Vector Similarity Search (Cumulative)",
    "llm_extractor": "# LLM Extractor — Full-Section Context Extraction (Final Results)",
}


def format_ledger_md(
    ledger: ExtractionLedger,
    filtered_taxonomy: list[TaxonomyEntry],
    agent_name: str,
) -> str:
    """Format an ExtractionLedger as a Markdown table for one tier's output file.

    Args:
        ledger: Current state of the extraction ledger.
        filtered_taxonomy: Taxonomy entries applicable to this report (for KPI names).
        agent_name: One of "deterministic_extractor", "semantic_retriever", "llm_extractor".
    """
    heading = _TIER_LABEL.get(agent_name, f"# {agent_name}")
    kpi_name_by_id = {e.kpi_id: e.kpi_name for e in filtered_taxonomy}

    records = list(ledger.records.values())
    found_count = sum(1 for r in records if r.status == "found")
    review_count = sum(1 for r in records if r.status == "needs_human_review")
    not_found_count = sum(1 for r in records if r.status == "not_found")

    lines: list[str] = [
        heading,
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|---|---|",
        f"| Found | {found_count} |",
        f"| Needs Human Review | {review_count} |",
        f"| Not Found | {not_found_count} |",
        f"| Total KPIs | {len(records)} |",
        "",
        "## Extraction Results",
        "",
        "| KPI ID | KPI Name | Status | Method | Value | Section | Page | Confidence | Review Reason |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for kpi_id, rec in sorted(ledger.records.items()):
        icon = _STATUS_ICON.get(rec.status, "?")
        kpi_name = kpi_name_by_id.get(kpi_id, kpi_id)
        value = str(rec.value) if rec.value is not None else "—"
        section = rec.section or "—"
        page = str(rec.page) if rec.page is not None else "—"
        method = rec.method or "—"
        confidence = f"{rec.confidence:.2f}" if rec.confidence else "—"
        review_reason = rec.review_reason or "—"
        lines.append(
            f"| {kpi_id} "
            f"| {kpi_name} "
            f"| {icon} {rec.status} "
            f"| {method} "
            f"| {value} "
            f"| {section} "
            f"| {page} "
            f"| {confidence} "
            f"| {review_reason} |"
        )

    lines.append("")

    # Append detailed attempts for flagged / review entries
    review_entries = [(kpi_id, rec) for kpi_id, rec in ledger.records.items()
                      if rec.status in ("needs_human_review", "flagged") and rec.attempts]
    if review_entries:
        lines += ["## Flagged / Review Details", ""]
        for kpi_id, rec in review_entries:
            kpi_name = kpi_name_by_id.get(kpi_id, kpi_id)
            lines.append(f"### {kpi_name} (`{kpi_id}`)")
            lines.append("")
            if rec.review_reason:
                lines.append(f"**Review reason:** {rec.review_reason}")
                lines.append("")
            if rec.conflicting_values:
                lines += ["**Conflicting values:**", ""]
                lines += [
                    "| Section | Value | Method | Source |",
                    "|---|---|---|---|",
                ]
                for cv in rec.conflicting_values:
                    lines.append(f"| {cv.section} | {cv.value} | {cv.method} | {cv.source_element_type} |")
                lines.append("")
            lines += [
                "**Extraction attempts:**",
                "",
                "| Tier | Outcome | Confidence | Note |",
                "|---|---|---|---|",
            ]
            for att in rec.attempts:
                val = str(att.value) if att.value is not None else "—"
                lines.append(
                    f"| {att.tier} | {att.outcome} | {att.confidence:.2f} | {att.note} |"
                )
            lines.append("")

    return "\n".join(lines)
