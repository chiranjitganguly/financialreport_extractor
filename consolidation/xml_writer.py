"""Agent 9 — Consolidation XML output writer.

Produces a single, human-readable XML file per report run containing:
  - Report metadata (company, industry, report type, fiscal year, standard)
  - Extraction summary counts
  - All resolved KPIs with KPI name, value, confidence, method, agent, section, page
  - All KPIs needing review with the same fields plus review reason and conflicts

Token usage is NOT included here — it lives in Summary_<Company>.md (summary_writer.py).

Output path: <OUTPUT_DIR>/<Company>/report/<Company>_<reporttype>_<fiscal_year>_final_report.xml
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from xml.dom import minidom

from common.schemas import ReportMetadata
from common.token_tracker import TokenUsageTracker
from consolidation.pipeline import FinalReportOutput

# Map extraction method → agent display name for the XML
_METHOD_TO_AGENT = {
    "deterministic": "deterministic_extractor",
    "semantic": "semantic_retriever",
    "llm": "llm_extractor",
}


def _sanitize(s: str) -> str:
    return re.sub(r"[^\w\-.]", "_", s or "unknown").strip("_") or "unknown"


def _build_output_path(report_metadata: ReportMetadata, output_dir: str) -> Path:
    company = _sanitize(report_metadata.company_name or "unknown")
    rtype = _sanitize(report_metadata.report_type or "unknown")
    fy = _sanitize(report_metadata.fiscal_year or "unknown")
    filename = f"{company}_{rtype}_{fy}_final_report.xml"
    path = Path(output_dir) / company / "report" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _sub(parent: ET.Element, tag: str, text: Optional[str] = None, **attrs) -> ET.Element:
    el = ET.SubElement(parent, tag, **attrs)
    if text is not None:
        el.text = str(text)
    return el


def _pretty_xml(root: ET.Element) -> str:
    raw = ET.tostring(root, encoding="unicode")
    return minidom.parseString(raw).toprettyxml(indent="  ", encoding=None)


def write_consolidation_xml(
    final_output: FinalReportOutput,
    report_metadata: ReportMetadata,
    token_tracker: Optional[TokenUsageTracker],
    output_dir: str,
) -> Path:
    """Generate and write the final XML report to disk.

    Args:
        final_output: Output of run_consolidation().
        report_metadata: ReportMetadata from Agent 1.
        token_tracker: Active TokenUsageTracker (may be None if tracking wasn't started).
        output_dir: Root output directory (data/output).

    Returns:
        Path to the written XML file.
    """
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    extraction_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    root = ET.Element("KPIExtractionReport", generated_at=now_iso)

    # ------------------------------------------------------------------ #
    # Report metadata
    # ------------------------------------------------------------------ #
    meta = _sub(root, "ReportMetadata")
    _sub(meta, "CompanyName", report_metadata.company_name or "")
    _sub(meta, "Industry", report_metadata.industry or "")
    _sub(meta, "ReportType", report_metadata.report_type or "")
    _sub(meta, "FiscalYear", report_metadata.fiscal_year or "")
    _sub(meta, "AccountingStandard", report_metadata.accounting_standard or "")
    _sub(meta, "Country", report_metadata.country or "")
    _sub(meta, "Language", report_metadata.language or "")
    _sub(meta, "ReportID", final_output.report_id)

    # ------------------------------------------------------------------ #
    # Extraction summary
    # ------------------------------------------------------------------ #
    summary = _sub(root, "ExtractionSummary")
    total = len(final_output.resolved) + len(final_output.needs_review)
    _sub(summary, "TotalKPIs", str(total))
    _sub(summary, "Resolved", str(len(final_output.resolved)))
    _sub(summary, "NeedsReview", str(len(final_output.needs_review)))

    # ------------------------------------------------------------------ #
    # Resolved KPIs
    # ------------------------------------------------------------------ #
    resolved_el = _sub(root, "ResolvedKPIs")
    for kpi in final_output.resolved:
        agent_display = _METHOD_TO_AGENT.get(kpi.method or "", kpi.method or "unknown")
        kpi_el = _sub(resolved_el, "KPI", id=kpi.kpi_id)
        _sub(kpi_el, "KPIName", kpi.kpi_name or "")
        _sub(kpi_el, "Value", str(kpi.value) if kpi.value is not None else "")
        _sub(kpi_el, "Confidence", f"{kpi.confidence:.4f}")
        _sub(kpi_el, "ExtractionMethod", kpi.method or "")
        _sub(kpi_el, "ExtractingAgent", agent_display)
        _sub(kpi_el, "ExtractionDate", extraction_date)
        _sub(kpi_el, "DocumentSection", kpi.section or "")
        _sub(kpi_el, "Page", str(kpi.page) if kpi.page is not None else "")
        _sub(kpi_el, "SourceElementType", kpi.source_element_type or "")
        if kpi.footnotes:
            fn_el = _sub(kpi_el, "Footnotes")
            for fn in kpi.footnotes:
                _sub(fn_el, "Footnote", fn)

    # ------------------------------------------------------------------ #
    # KPIs needing review
    # ------------------------------------------------------------------ #
    review_el = _sub(root, "KPIsNeedingReview")
    for kpi in final_output.needs_review:
        agent_display = _METHOD_TO_AGENT.get(kpi.method or "", "unknown")
        kpi_el = _sub(review_el, "KPI", id=kpi.kpi_id)
        _sub(kpi_el, "KPIName", kpi.kpi_name or "")
        _sub(kpi_el, "Value", str(kpi.value) if kpi.value is not None else "")
        _sub(kpi_el, "Confidence", f"{kpi.confidence:.4f}")
        _sub(kpi_el, "ReviewReason", kpi.review_reason or "")
        _sub(kpi_el, "DocumentSection", kpi.section or "")
        _sub(kpi_el, "Page", str(kpi.page) if kpi.page is not None else "")
        _sub(kpi_el, "SourceElementType", kpi.source_element_type or "")
        _sub(kpi_el, "ExtractionDate", extraction_date)
        if kpi.footnotes:
            fn_el = _sub(kpi_el, "Footnotes")
            for fn in kpi.footnotes:
                _sub(fn_el, "Footnote", fn)
        if kpi.conflicting_values:
            cv_el = _sub(kpi_el, "ConflictingValues")
            for cv in kpi.conflicting_values:
                _sub(cv_el, "Conflict",
                     str(cv.value),
                     section=cv.section or "",
                     method=cv.method or "",
                     source=cv.source_element_type or "")
        if kpi.attempts:
            att_el = _sub(kpi_el, "AttemptHistory")
            for att in kpi.attempts:
                a_el = _sub(att_el, "Attempt", tier=att.tier, outcome=att.outcome)
                _sub(a_el, "Value", str(att.value) if att.value is not None else "")
                _sub(a_el, "Confidence", f"{att.confidence:.4f}")
                if att.note:
                    _sub(a_el, "Note", att.note)

    # Token usage is intentionally excluded from the XML report.
    # Full token breakdown (by agent, by model, overall) is in the
    # Summary_<Company>.md file written by consolidation/summary_writer.py.

    # ------------------------------------------------------------------ #
    # Write to disk
    # ------------------------------------------------------------------ #
    out_path = _build_output_path(report_metadata, output_dir)
    xml_str = _pretty_xml(root)
    out_path.write_text(xml_str, encoding="utf-8")
    return out_path
