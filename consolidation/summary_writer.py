"""Agent 9 — Consolidation Summary Report writer.

Produces a Markdown file named Summary_<CompanyName>.md inside
<output_dir>/report/ containing:
  - Execution timeline (per agent + total run time, including per-turn breakdown)
  - KPI counts grouped by retrieval method
  - KPIs found per extraction turn (Tier 1 / Tier 2 / Tier 3 / retry turns)
  - Value changes across turns for KPIs that passed through multiple tiers
  - Validation run results
  - Token usage table by model
  - Taxonomy alias mismatch table (LLM alias ≠ taxonomy aliases)
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from common.schemas import ExtractionLedger, ReportMetadata, TaxonomyEntry
from common.timing_tracker import TimingTracker
from common.token_tracker import TokenUsageTracker
from consolidation.pipeline import FinalReportOutput
from validation_retry_loop import ValidationRetryOutput


def _sanitize(s: str) -> str:
    return re.sub(r"[^\w\-.]", "_", s or "unknown").strip("_") or "unknown"


def _build_output_path(company_name: str, output_dir: str) -> Path:
    safe = _sanitize(company_name)
    path = Path(output_dir) / safe / "report" / f"Summary_{safe}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    sep = ["-" * max(len(h), 3) for h in headers]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def write_summary_report(
    final_output: FinalReportOutput,
    validation_output: ValidationRetryOutput,
    ledger_before_validation: ExtractionLedger,
    report_metadata: ReportMetadata,
    taxonomy_by_id: dict[str, TaxonomyEntry],
    token_tracker: Optional[TokenUsageTracker],
    timing_tracker: Optional[TimingTracker],
    output_dir: str,
) -> Path:
    """Generate and write the summary Markdown report.

    Args:
        final_output: Output of run_consolidation() (resolved + needs_review).
        validation_output: Output of run_validation_retry_loop().
        ledger_before_validation: ExtractionLedger after extraction cascade,
            BEFORE validation retry — used to show per-tier extraction counts.
        report_metadata: ReportMetadata from Agent 1.
        taxonomy_by_id: Full taxonomy keyed by kpi_id (for alias mismatch check).
        token_tracker: Active TokenUsageTracker (may be None).
        timing_tracker: Active TimingTracker (may be None).
        output_dir: Root output directory (data/output).

    Returns:
        Path to the written Markdown file.
    """
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    company = report_metadata.company_name or "Unknown"
    final_ledger = validation_output.ledger

    lines: list[str] = []

    # ------------------------------------------------------------------ #
    # Header
    # ------------------------------------------------------------------ #
    lines += [
        f"# Extraction Summary: {company}",
        "",
        f"**Generated**: {now_iso}  ",
        f"**Report ID**: {final_output.report_id}  ",
        f"**Total Run Time**: {timing_tracker.total_elapsed_str() if timing_tracker else 'N/A'}",
        "",
    ]

    # ------------------------------------------------------------------ #
    # Metadata
    # ------------------------------------------------------------------ #
    lines += [
        "## Report Metadata",
        "",
        f"| Field | Value |",
        f"|---|---|",
        f"| Company | {company} |",
        f"| Industry | {report_metadata.industry or ''} |",
        f"| Report Type | {report_metadata.report_type or ''} |",
        f"| Fiscal Year | {report_metadata.fiscal_year or ''} |",
        f"| Accounting Standard | {report_metadata.accounting_standard or ''} |",
        f"| Country | {report_metadata.country or ''} |",
        f"| Language | {report_metadata.language or ''} |",
        "",
    ]

    # ------------------------------------------------------------------ #
    # Execution timeline
    # ------------------------------------------------------------------ #
    lines.append("## Execution Timeline")
    lines.append("")
    if timing_tracker:
        timing_rows = [
            [t.agent_name, t.duration_str]
            for t in timing_tracker.get_all()
        ]
        timing_rows.append(["**Total**", f"**{timing_tracker.total_elapsed_str()}**"])
        lines.append(_md_table(["Agent / Phase", "Duration"], timing_rows))
    else:
        lines.append("_Timing data not available._")
    lines.append("")

    # ------------------------------------------------------------------ #
    # KPI extraction summary
    # ------------------------------------------------------------------ #
    total_kpis = len(final_output.resolved) + len(final_output.needs_review)
    lines += [
        "## KPI Extraction Summary",
        "",
        f"**Total KPIs in scope**: {total_kpis}  ",
        f"**Resolved (found)**: {len(final_output.resolved)}  ",
        f"**Needs Review**: {len(final_output.needs_review)}",
        "",
    ]

    # By retrieval method — use the FINAL ledger state
    method_counts: dict[str, int] = {"deterministic": 0, "semantic": 0, "llm": 0}
    for record in final_ledger.records.values():
        if record.method in method_counts:
            method_counts[record.method] += 1

    lines += [
        "### By Retrieval Method (Final State)",
        "",
        _md_table(
            ["Method", "KPIs Extracted"],
            [
                ["Deterministic (Tier 1)", str(method_counts["deterministic"])],
                ["Semantic Retrieval (Tier 2)", str(method_counts["semantic"])],
                ["LLM Extraction (Tier 3)", str(method_counts["llm"])],
            ],
        ),
        "",
    ]

    # By extraction turn — use attempts to determine which tier first found each KPI
    turn_counts = _count_by_extraction_turn(ledger_before_validation, final_ledger, validation_output.turns_used)
    turn_rows = [
        ["Turn 1 — Deterministic (Tier 1)", str(turn_counts.get("deterministic", 0))],
        ["Turn 2 — Semantic Retrieval (Tier 2)", str(turn_counts.get("semantic", 0))],
        ["Turn 3 — LLM Extraction (Tier 3)", str(turn_counts.get("llm_initial", 0))],
    ]
    for i in range(1, validation_output.turns_used + 1):
        turn_rows.append([f"Retry Turn {i} — LLM Retry", str(turn_counts.get(f"retry_{i}", 0))])

    lines += [
        "### By Extraction Turn",
        "",
        _md_table(["Turn", "KPIs Found"], turn_rows),
        "",
    ]

    # ------------------------------------------------------------------ #
    # Value changes across turns
    # ------------------------------------------------------------------ #
    lines.append("## KPI Value Changes Across Turns")
    lines.append("")
    change_rows = _build_value_change_rows(final_ledger)
    if change_rows:
        lines.append(_md_table(
            ["KPI ID", "KPI Name", "Tier 1 (Det)", "Tier 2 (Sem)", "Tier 3 (LLM)", "Retry Turn(s)", "Final Value"],
            change_rows,
        ))
    else:
        lines.append("_No KPIs passed through multiple tiers with differing values._")
    lines.append("")

    # ------------------------------------------------------------------ #
    # Validation run
    # ------------------------------------------------------------------ #
    lines += [
        "## Validation Run",
        "",
        f"**Retry turns used**: {validation_output.turns_used}  ",
    ]
    review_by_reason: dict[str, list[str]] = {}
    for record in final_ledger.records.values():
        if record.status == "needs_human_review" and record.review_reason:
            review_by_reason.setdefault(record.review_reason, []).append(record.kpi_id)

    if review_by_reason:
        vrows = [[reason, str(len(ids)), ", ".join(ids[:5]) + ("…" if len(ids) > 5 else "")]
                 for reason, ids in sorted(review_by_reason.items())]
        lines.append(_md_table(["Review Reason", "Count", "KPI IDs (sample)"], vrows))
    else:
        lines.append("_All KPIs passed validation or were not attempted._")
    lines.append("")

    # ------------------------------------------------------------------ #
    # Token usage
    # ------------------------------------------------------------------ #
    lines.append("## Token Usage")
    lines.append("")
    if token_tracker:
        by_model = token_tracker.summary_by_model()
        totals = token_tracker.overall_totals()
        model_rows = [
            [s.model, s.provider, f"{s.input_tokens:,}", f"{s.output_tokens:,}", f"{s.total_tokens:,}", str(s.calls)]
            for s in by_model
        ]
        lines.append(_md_table(
            ["Model", "Provider", "Input Tokens", "Output Tokens", "Total Tokens", "API Calls"],
            model_rows,
        ))
        lines += [
            "",
            f"**Overall Total**: {totals['total_tokens']:,} tokens "
            f"({totals['input_tokens']:,} in + {totals['output_tokens']:,} out)",
            "",
        ]

        # Per-agent breakdown
        lines.append("### Per-Agent Token Breakdown")
        lines.append("")
        agent_rows = [
            [u.agent_name, u.model, f"{u.input_tokens:,}", f"{u.output_tokens:,}", f"{u.total_tokens:,}", str(u.calls)]
            for u in token_tracker.get_all()
        ]
        if agent_rows:
            lines.append(_md_table(
                ["Agent", "Model", "Input Tokens", "Output Tokens", "Total Tokens", "Calls"],
                agent_rows,
            ))
        lines.append("")
    else:
        lines.append("_Token tracking was not active for this run._")
        lines.append("")

    # ------------------------------------------------------------------ #
    # Taxonomy alias mismatches
    # ------------------------------------------------------------------ #
    lines += [
        "## Taxonomy Alias Mismatches",
        "",
        "KPIs where the LLM found the value under a term **not** listed in the taxonomy aliases.",
        "Update `data/kpi/taxonomy_map.json` to add these aliases so future runs can use",
        "cheaper deterministic matching.",
        "",
    ]
    mismatch_rows = _find_alias_mismatches(final_ledger, taxonomy_by_id)
    if mismatch_rows:
        lines.append(_md_table(
            ["KPI ID", "KPI Name", "Known Aliases", "Term Found in Document"],
            mismatch_rows,
        ))
    else:
        lines.append("_No alias mismatches detected._")
    lines.append("")

    # ------------------------------------------------------------------ #
    # Write file
    # ------------------------------------------------------------------ #
    out_path = _build_output_path(company, output_dir)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _count_by_extraction_turn(
    ledger_before: ExtractionLedger,
    ledger_final: ExtractionLedger,
    retry_turns_used: int,
) -> dict[str, int]:
    """Count how many KPIs were first resolved at each extraction turn.

    Uses the attempts list to determine which tier produced the first
    'found' or 'flagged' outcome.  Retry turns are those with tier='llm'
    appearing after index 2 in the attempts list.
    """
    counts: dict[str, int] = {
        "deterministic": 0,
        "semantic": 0,
        "llm_initial": 0,
    }
    for i in range(1, retry_turns_used + 1):
        counts[f"retry_{i}"] = 0

    for record in ledger_final.records.values():
        if record.method is None:
            continue
        # The first successful attempt tier tells us which turn found it.
        successful = [a for a in record.attempts if a.outcome in ("found", "flagged")]
        if not successful:
            # method set but no attempt logged — count by method
            if record.method == "deterministic":
                counts["deterministic"] += 1
            elif record.method == "semantic":
                counts["semantic"] += 1
            else:
                counts["llm_initial"] += 1
            continue

        first = successful[0]
        if first.tier == "deterministic":
            counts["deterministic"] += 1
        elif first.tier == "semantic":
            counts["semantic"] += 1
        else:
            # LLM — determine if initial or retry by counting prior llm attempts
            llm_attempts = [a for a in record.attempts if a.tier == "llm"]
            first_llm_idx = next(
                (i for i, a in enumerate(llm_attempts) if a.outcome in ("found", "flagged")), 0
            )
            if first_llm_idx == 0:
                counts["llm_initial"] += 1
            else:
                retry_key = f"retry_{min(first_llm_idx, retry_turns_used)}"
                counts[retry_key] = counts.get(retry_key, 0) + 1

    return counts


def _build_value_change_rows(ledger: ExtractionLedger) -> list[list[str]]:
    """Return rows for KPIs whose value changed between extraction attempts."""
    rows: list[list[str]] = []
    for record in ledger.records.values():
        if len(record.attempts) < 2:
            continue
        values_by_tier: dict[str, str] = {}
        for att in record.attempts:
            if att.value is not None and att.outcome in ("found", "flagged"):
                values_by_tier[att.tier] = str(att.value)

        if len(set(values_by_tier.values())) < 2:
            continue  # no change across tiers

        det_val = values_by_tier.get("deterministic", "—")
        sem_val = values_by_tier.get("semantic", "—")
        llm_vals = [str(att.value) for att in record.attempts
                    if att.tier == "llm" and att.value is not None and att.outcome in ("found", "flagged")]
        llm_initial = llm_vals[0] if llm_vals else "—"
        retry_vals = ", ".join(llm_vals[1:]) if len(llm_vals) > 1 else "—"
        final_val = str(record.value) if record.value is not None else "—"

        rows.append([
            record.kpi_id,
            record.kpi_name or "",
            det_val,
            sem_val,
            llm_initial,
            retry_vals,
            final_val,
        ])
    return rows


def _find_alias_mismatches(
    ledger: ExtractionLedger,
    taxonomy_by_id: dict[str, TaxonomyEntry],
) -> list[list[str]]:
    """Find KPIs extracted by LLM using a term not in the taxonomy aliases."""
    rows: list[list[str]] = []
    for record in ledger.records.values():
        if record.method != "llm" or not record.alias_used:
            continue
        entry = taxonomy_by_id.get(record.kpi_id)
        if entry is None:
            continue
        known = {a.lower().strip() for a in entry.aliases} | {entry.kpi_name.lower().strip()}
        if record.alias_used.lower().strip() not in known:
            rows.append([
                record.kpi_id,
                entry.kpi_name,
                ", ".join(entry.aliases) if entry.aliases else "(none)",
                f"**{record.alias_used}**",
            ])
    return rows
