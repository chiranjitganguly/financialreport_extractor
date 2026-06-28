from typing import Optional

from report_ingestion.schemas import (
    AccountingStandardResult,
    FieldReview,
    IndustryResult,
    LanguageResult,
    ReportMetadata,
    ReportTypeResult,
)


def route_confidence(
    report_type: ReportTypeResult,
    language: LanguageResult,
    accounting_standard: AccountingStandardResult,
    industry: IndustryResult,
    country: Optional[str],
    threshold: float,
) -> tuple[Optional[ReportMetadata], list[FieldReview]]:
    """Check every classified field against the confidence threshold and route accordingly.

    ``country`` is passed through directly from the company lookup result and is
    NOT confidence-checked — no fallback or threshold applies to it (see design
    doc §1).

    Args:
        report_type: Final ReportTypeResult after Stages A/B/C.
        language: Final LanguageResult (always deterministic, always resolves).
        accounting_standard: Final AccountingStandardResult after Stages A/B/C.
        industry: Final IndustryResult after Stages B/C.
        country: Country of incorporation from the company lookup, or None if the
            company was not matched above the fuzzy cutoff.
        threshold: Minimum confidence required to accept a field without human
            review. Sourced from config.settings.CLASSIFICATION_CONFIDENCE_THRESHOLD
            (default 0.25).

    Returns:
        A two-tuple ``(report_metadata, flagged_fields)``:

        - If every field's confidence >= threshold: ``report_metadata`` is a
          complete ReportMetadata (``country`` included, possibly None) and
          ``flagged_fields`` is an empty list.
        - If any field is below threshold: ``report_metadata`` is None and
          ``flagged_fields`` lists every failing field with its best-guess value,
          confidence, and a human-readable reason string. The caller (pipeline.py)
          routes this to hitl_queue.enqueue_for_review().
    """
    candidates: list[tuple[str, str, float]] = [
        # (field_name, best-guess value as str, confidence)
        ("report_type",         report_type.report_type,        report_type.confidence),
        ("language",            language.language,               language.confidence),
        ("accounting_standard", accounting_standard.standard,    accounting_standard.confidence),
        ("industry",            industry.industry,               industry.confidence),
    ]

    flagged: list[FieldReview] = [
        FieldReview(
            field_name=name,        # type: ignore[arg-type]
            value=value,
            confidence=confidence,
            reason=f"{confidence:.4f} < threshold {threshold}",
        )
        for name, value, confidence in candidates
        if confidence < threshold
    ]

    if flagged:
        return None, flagged

    return (
        ReportMetadata(
            report_id="",  # populated by the caller (pipeline.py) which holds the report_id
            report_type=report_type.report_type,
            language=language.language,
            accounting_standard=accounting_standard.standard,
            industry=industry.industry,
            fiscal_year="",  # populated downstream once extracted from the document
            country=country,
        ),
        [],
    )
