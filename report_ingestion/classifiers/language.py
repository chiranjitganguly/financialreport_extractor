from lingua import LanguageDetectorBuilder

from report_ingestion.schemas import LanguageResult

# Built once at import time — lingua loads statistical models on construction.
_detector = LanguageDetectorBuilder.from_all_languages().build()


def detect_language(narrative_markdown: str) -> LanguageResult:
    """Detect the document language using lingua-py (deterministic, no LLM).

    Args:
        narrative_markdown: Pass get_classification_excerpt() output, not the
            full document — lingua-py does not need more than the excerpt.

    Returns:
        LanguageResult with an ISO 639-1 language code and lingua-py's native
        confidence score. This classifier always resolves; there is no fallback
        stage for language detection.
    """
    detected = _detector.detect_language_of(narrative_markdown)

    if detected is None:
        # lingua could not determine any language — return "und" (undetermined)
        # with zero confidence so route_confidence can flag it if needed.
        return LanguageResult(language="und", confidence=0.0)

    confidence = _detector.compute_language_confidence(narrative_markdown, detected)
    iso_code = detected.iso_code_639_1.name.lower()  # e.g. Language.ENGLISH → "en"
    return LanguageResult(language=iso_code, confidence=confidence)
