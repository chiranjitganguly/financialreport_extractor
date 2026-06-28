"""Agent 7 — Validator: final single-pass review checks.

Run ONCE after the retry loop converges — not on every turn.  These checks
apply only to records with status=="found" at the time they run; by
running after the loop they see the final settled state of every record.

Two checks in order:
  1. run_low_confidence_passthrough — confidence below threshold → needs_human_review
  2. run_footnote_materiality_check — material footnote keyword → needs_human_review

Order matters: whichever runs first will flip some "found" records to
needs_human_review, so the second check sees fewer "found" candidates.
This is intentional — one review reason per record is sufficient.
"""

from __future__ import annotations

import logging

from common.schemas import ExtractionAttempt, ExtractionLedger, FootnoteElement

log = logging.getLogger(__name__)


def run_low_confidence_passthrough(
    ledger: ExtractionLedger,
    threshold: float,
) -> ExtractionLedger:
    """Flag found records whose confidence is below threshold for human review.

    Only touches records with status=="found" — records already
    needs_human_review for another reason are left untouched.

    Args:
        ledger: ExtractionLedger after the retry loop.
        threshold: Confidence cutoff (from common.config.EXTRACTION_CONFIDENCE_THRESHOLD).

    Returns:
        Updated ledger.
    """
    for kpi_id, record in ledger.records.items():
        if record.status != "found":
            continue
        if record.confidence < threshold:
            record.status = "needs_human_review"
            record.review_reason = "low_confidence"
            record.attempts.append(
                ExtractionAttempt(
                    tier=record.method or "llm",
                    value=record.value,
                    confidence=record.confidence,
                    outcome="flagged",
                    note=(
                        f"confidence {record.confidence:.2f} < threshold {threshold:.2f} "
                        "— routed to human review"
                    ),
                )
            )
            log.debug(
                "Low-confidence passthrough: kpi_id=%s confidence=%.2f",
                kpi_id, record.confidence,
            )

    return ledger


def classify_footnote_materiality(
    footnote_texts: list[str],
    material_keywords: list[str],
) -> bool:
    """Return True if any material keyword appears (case-insensitive) in any footnote text.

    v1 keyword heuristic per the spec — LLM judgment is the documented upgrade path.
    A record with a material footnote carries a disclosure-completeness concern
    (adjusted/non-GAAP/restated language) even if the extracted value is otherwise correct.

    Args:
        footnote_texts: Text bodies of footnotes linked to the record.
        material_keywords: Keywords from config.FOOTNOTE_MATERIALITY_KEYWORDS.

    Returns:
        True if any keyword matched any footnote.
    """
    lowered_keywords = [kw.lower() for kw in material_keywords]
    for text in footnote_texts:
        lowered_text = text.lower()
        if any(kw in lowered_text for kw in lowered_keywords):
            return True
    return False


def run_footnote_materiality_check(
    ledger: ExtractionLedger,
    footnotes_by_id: dict[str, FootnoteElement],
    material_keywords: list[str],
) -> ExtractionLedger:
    """Flag found records with material footnote caveats for human review.

    Runs AFTER run_low_confidence_passthrough so it only sees records that
    survived that pass with status=="found".

    A record with a material footnote is terminal: never re-enters the retry
    loop (review_reason="footnoted_caveat" is in the terminal set checked by
    run_tally_checks).

    Args:
        ledger: ExtractionLedger (after low-confidence passthrough).
        footnotes_by_id: Dict from footnote marker → FootnoteElement, built
            from the report's full footnote list.
        material_keywords: From config.FOOTNOTE_MATERIALITY_KEYWORDS.

    Returns:
        Updated ledger.
    """
    for kpi_id, record in ledger.records.items():
        if record.status != "found":
            continue
        if not record.footnotes:
            continue

        footnote_texts = [
            footnotes_by_id[fn_id].text
            for fn_id in record.footnotes
            if fn_id in footnotes_by_id
        ]
        if not footnote_texts:
            continue

        if classify_footnote_materiality(footnote_texts, material_keywords):
            record.status = "needs_human_review"
            record.review_reason = "footnoted_caveat"
            record.attempts.append(
                ExtractionAttempt(
                    tier=record.method or "llm",
                    value=record.value,
                    confidence=record.confidence,
                    outcome="flagged",
                    note=(
                        f"material footnote detected (matched keyword in "
                        f"footnotes {record.footnotes})"
                    ),
                )
            )
            log.info(
                "Footnote materiality: kpi_id=%s flagged via footnotes=%s",
                kpi_id, record.footnotes,
            )

    return ledger
