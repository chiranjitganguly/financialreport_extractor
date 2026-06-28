"""
LangChain prompt templates for Agent 2 (Section Parser & Splitter).

Two prompts:
  SECTION_ALIGNMENT_PROMPT       — Stage B batched LLM fallback in
                                    batch_align_sections_llm()
  SECTION_REALIGNMENT_PROMPT     — per-section retry in
                                    realign_section_low_confidence()
"""

from langchain_core.prompts import ChatPromptTemplate

# ---------------------------------------------------------------------------
# Stage B: batch alignment prompt
#
# Used by batch_align_sections_llm() for all sections whose fuzzy-match score
# fell below SECTION_ALIGNMENT_FUZZY_CUTOFF. Sent in a single batched call —
# the full list of (section_name_raw, content_excerpt) pairs is serialised into
# the human message via {sections_json}. canonical_vocabulary is the sorted,
# deduplicated union of all canonical_sections entries from the Taxonomy Map
# plus the sentinel "OTHER".
# ---------------------------------------------------------------------------

SECTION_ALIGNMENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a financial document analyst. Your task is to map raw section "
            "headers from a company report to the canonical taxonomy vocabulary "
            "provided. Return one alignment per section. For each section, choose "
            "the closest canonical name from the vocabulary, or use 'OTHER' if the "
            "section has no KPI relevance (e.g. Chairman's Letter, ESG narrative, "
            "Corporate Governance commentary). Include a confidence score (0.0–1.0) "
            "reflecting how certain you are of the mapping.",
        ),
        (
            "human",
            "Canonical vocabulary (choose exactly one per section, or 'OTHER'):\n"
            "{canonical_vocabulary}\n\n"
            "Sections to align (JSON list of objects with 'section_name_raw' and "
            "'content_excerpt'):\n"
            "{sections_json}\n\n"
            "For each section, return its section_name_raw echoed back, the chosen "
            "section_name_canonical, and your confidence (0.0–1.0).",
        ),
    ]
)

# ---------------------------------------------------------------------------
# Retry prompt: realignment with more context
#
# Used by realign_section_low_confidence() when the first alignment pass
# returned a confidence below SECTION_ALIGNMENT_CONFIDENCE_THRESHOLD.
# Passes a fuller content excerpt plus the previous low-confidence guess so
# the model can confirm or override it with better grounding.
# ---------------------------------------------------------------------------

SECTION_REALIGNMENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a financial document analyst reviewing a previously uncertain "
            "section classification. A prior pass classified the section with low "
            "confidence. Examine the fuller content excerpt provided and either "
            "confirm the previous guess or select a better canonical name. Return "
            "section_name_canonical (one of the vocabulary entries or 'OTHER') and "
            "a confidence score (0.0–1.0).",
        ),
        (
            "human",
            "Canonical vocabulary (choose exactly one, or 'OTHER'):\n"
            "{canonical_vocabulary}\n\n"
            "Raw section header: {section_name_raw}\n\n"
            "Previous classification: '{previous_canonical}' "
            "(confidence: {previous_confidence:.2f})\n\n"
            "Fuller content excerpt:\n{content_excerpt}\n\n"
            "Confirm or correct the classification and provide your confidence "
            "(0.0–1.0).",
        ),
    ]
)
