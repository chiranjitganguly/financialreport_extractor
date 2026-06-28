"""Shared deterministic matching logic — used by Tier 1 (Agent 4) and Tier 2 (Agent 5).

The main spec is explicit that Tier 2 reuses the SAME logic as Tier 1, scoped
to a retrieved chunk rather than a full section.  All matching code lives here
so neither tier duplicates it.
"""

from __future__ import annotations

import re
from typing import Optional

from rapidfuzz import fuzz

from common.schemas import CandidateValue, TableElement  # noqa: F401 (CandidateValue re-exported for callers)


# ---------------------------------------------------------------------------
# Confidence formula
# ---------------------------------------------------------------------------

def compute_match_confidence(
    num_candidates_considered: int,
    top_score: float,
    runner_up_score: Optional[float],
) -> float:
    """Shared confidence formula for all deterministic and semantic matches.

    Args:
        num_candidates_considered: How many rows/text-spans cleared the fuzzy
            cutoff — not the total rows in the table.
        top_score: The winning candidate's normalized match score (0–1).
        runner_up_score: The second-best candidate's score (0–1), if any.

    Returns:
        1.0  — unambiguous single match.
        0.5–0.7 — tie-broken, scaled by runner-up proximity: a closer
                  runner-up (smaller margin) produces lower confidence.
    """
    if num_candidates_considered == 1:
        return 1.0
    if runner_up_score is None or top_score <= 0:
        return 0.7
    # margin in [0, 1]; margin=0 → scores tied → 0.5; margin→1 → clear winner → 0.7
    margin = min(1.0, (top_score - runner_up_score) / max(top_score, 1e-6))
    return round(0.5 + 0.2 * margin, 4)


# ---------------------------------------------------------------------------
# Table matching
# ---------------------------------------------------------------------------

def _normalize_fiscal_year(label: str) -> str:
    """Strip common prefixes so 'Year ended March 31, 2025' matches 'FY2025'."""
    # Keep only the 4-digit year component for loose comparison
    m = re.search(r"(20\d{2})", label)
    return m.group(1) if m else label.strip()


def match_table_row(
    table: TableElement,
    kpi_name: str,
    aliases: list[str],
    fiscal_year: str,
    fuzzy_cutoff: float,
) -> Optional[CandidateValue]:
    """Fuzzy-match table rows against kpi_name + aliases; read the fiscal-year cell.

    Uses rapidfuzz WRatio — the same scorer as Agent 1's company-name lookup and
    Agent 2's section alignment, keeping one fuzzy-matching convention across the
    whole codebase.

    Args:
        table: A TableElement from a Section.
        kpi_name: The KPI's primary name.
        aliases: The KPI's aliases list.
        fiscal_year: Target period from ReportMetadata (e.g. "FY2025").
        fuzzy_cutoff: Minimum WRatio score (0–100) to accept a row as a match.

    Returns:
        CandidateValue if a row and fiscal-year cell are found; None otherwise.
    """
    terms = [kpi_name] + aliases
    fy_year = _normalize_fiscal_year(fiscal_year)

    scored: list[tuple[float, int]] = []  # (score, row_index)
    for idx, row in enumerate(table.rows):
        best = max(fuzz.WRatio(row.row_label, t) for t in terms)
        if best >= fuzzy_cutoff:
            scored.append((best, idx))

    if not scored:
        return None

    scored.sort(reverse=True)
    top_score, top_idx = scored[0]
    runner_up_score = scored[1][0] if len(scored) > 1 else None

    # Find the fiscal-year cell in the winning row
    row = table.rows[top_idx]
    matched_cell = None
    for cell in row.cells:
        col_year = _normalize_fiscal_year(cell.column_label)
        if col_year == fy_year or fiscal_year in cell.column_label:
            matched_cell = cell
            break

    if matched_cell is None:
        return None

    confidence = compute_match_confidence(
        num_candidates_considered=len(scored),
        top_score=top_score / 100.0,
        runner_up_score=runner_up_score / 100.0 if runner_up_score is not None else None,
    )

    return CandidateValue(
        value=matched_cell.value,
        section_name_canonical=table.section_name_canonical,
        page=table.page,
        source_element_type="table_cell",
        footnotes=list(matched_cell.footnote_refs),
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Narrative text matching
# ---------------------------------------------------------------------------

def _build_narrative_pattern(kpi_name: str, aliases: list[str]) -> re.Pattern[str]:
    """Compile a regex that matches a KPI label followed by a numeric value."""
    terms = sorted({kpi_name} | set(aliases), key=len, reverse=True)
    alternation = "|".join(re.escape(t) for t in terms)
    # After the label, allow optional colon/dash/whitespace, then a number
    # (with optional currency prefix, commas, decimal, and scale suffix).
    number_pat = r"([\-₹$€£]?[\d,]+(?:\.\d+)?(?:\s*(?:million|billion|crore|lakh|mn|bn|cr))?)"
    return re.compile(
        rf"(?i)(?:{alternation})\s*[:\-–]?\s*{number_pat}",
        re.MULTILINE,
    )


def match_narrative_text(
    content_markdown: str,
    kpi_name: str,
    aliases: list[str],
    section_page_range: tuple[int, int],
) -> Optional[CandidateValue]:
    """Regex-match a KPI name/alias followed by a number in narrative text.

    Page precision: content_markdown carries no per-line page numbers; the
    section's page_range[0] is used as an approximation.  This matches the
    same accepted limitation documented in Agent 3's chunking design.

    Args:
        content_markdown: Section.content_markdown.
        kpi_name: The KPI's primary name.
        aliases: The KPI's aliases.
        section_page_range: Section.page_range — used for the page field.

    Returns:
        CandidateValue(source_element_type="text") or None.
    """
    if not content_markdown.strip():
        return None

    pattern = _build_narrative_pattern(kpi_name, aliases)
    matches = pattern.findall(content_markdown)

    if not matches:
        return None

    # Deduplicate by value string; first match wins for the value itself.
    unique_values = list(dict.fromkeys(m.strip() for m in matches if m.strip()))
    best_value = unique_values[0]

    # Confidence: 1 distinct match → 1.0; multiple distinct values → tie-break
    confidence = compute_match_confidence(
        num_candidates_considered=len(unique_values),
        top_score=1.0,
        runner_up_score=0.9 if len(unique_values) > 1 else None,
    )

    return CandidateValue(
        value=best_value,
        section_name_canonical="",  # caller fills this in from the Section
        page=section_page_range[0],
        source_element_type="text",
        footnotes=[],
        confidence=confidence,
    )
