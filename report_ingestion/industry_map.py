import json
from pathlib import Path
from typing import Optional

from pydantic import ValidationError
from rapidfuzz import fuzz, process

from report_ingestion.schemas import CompanyLookupResult, CompanyReferenceEntry


def load_company_reference_map(path: str) -> list[CompanyReferenceEntry]:
    """Load and validate the JSON company reference file at process/pipeline startup.

    Called once at startup (not per-document) so that schema errors surface
    immediately rather than mid-pipeline.

    Args:
        path: Filesystem path to the JSON reference file. Must come from
            config.settings.INDUSTRY_MAP_PATH — never hardcode the path.

    Returns:
        Validated list of CompanyReferenceEntry objects.

    Raises:
        ValueError: If the file is not valid JSON or any entry fails Pydantic
            validation. The error message includes the offending entry's index
            and company_name — bad entries are never silently skipped.
    """
    raw = Path(path).read_text(encoding="utf-8")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Company reference map at '{path}' is not valid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError(
            f"Company reference map at '{path}' must be a JSON array, got {type(data).__name__}"
        )

    entries: list[CompanyReferenceEntry] = []
    for idx, item in enumerate(data):
        try:
            entries.append(CompanyReferenceEntry.model_validate(item))
        except ValidationError as exc:
            name = item.get("company_name", "<unknown>") if isinstance(item, dict) else "<unknown>"
            raise ValueError(
                f"Company reference map entry {idx} ('{name}') failed validation: {exc}"
            ) from exc

    return entries


def lookup_company(
    company_name: str,
    reference_map: list[CompanyReferenceEntry],
    fuzzy_cutoff: float,
) -> Optional[CompanyLookupResult]:
    """Fuzzy-match a company name against the reference map and return the best match.

    Uses ``rapidfuzz.process.extractOne`` with ``WRatio`` scorer, which handles
    partial matches and word-order differences better than plain ratio for company
    names (e.g. "British Telecommunications plc" vs "British Telecom").

    Args:
        company_name: Candidate name extracted from the document — output of
            extract_company_name_llm().
        reference_map: Loaded list from load_company_reference_map().
        fuzzy_cutoff: Minimum acceptable match quality on the rapidfuzz 0–100
            scale. Sourced from config.settings.INDUSTRY_MAP_FUZZY_CUTOFF
            (default 85) — never hardcode the value here.

    Returns:
        CompanyLookupResult with match_score = (rapidfuzz ratio / 100) if the
        best match clears ``fuzzy_cutoff``. None if no entry clears the cutoff,
        which triggers the LLM fallback (Stage C) downstream for both industry
        and accounting standard.
    """
    if not reference_map:
        return None

    names = [entry.company_name for entry in reference_map]
    match = process.extractOne(
        company_name,
        names,
        scorer=fuzz.WRatio,
        score_cutoff=fuzzy_cutoff,
    )

    if match is None:
        return None

    _matched_name, score, index = match
    return CompanyLookupResult(
        matched_entry=reference_map[index],
        match_score=score / 100.0,
    )
