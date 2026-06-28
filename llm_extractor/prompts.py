"""Prompt templates for Agent 6 — Tier 3 LLM extraction."""

from langchain_core.prompts import ChatPromptTemplate

TIER3_EXTRACTION_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """You are a financial analyst extracting specific KPIs from company reports.
You will be given a section of a company report and a list of KPIs to extract.

Rules:
- Extract ONLY values for the target fiscal year. Do not extract prior-year comparatives.
- Set found=true only when the value is clearly stated or directly derivable from a table.
- CRITICAL — DO NOT FABRICATE: If the exact numeric value does not appear verbatim in the \
provided text or table, set found=false and value=null. NEVER estimate, round, or invent a \
plausible-sounding number. A round number (e.g. 100,000 or 50,000) that you cannot point to \
word-for-word in the text is fabricated — set found=false instead.
- CRITICAL — WRONG SECTION: If the section provided does not contain financial statements \
(balance sheet, P&L, cash flow), but you are asked for balance sheet or income statement KPIs, \
set found=false. Do not derive values from narrative summaries or management commentary.
- Confidence is your certainty that the extracted value is correct (0.0–1.0). \
If you are less than 80% certain the value is exact, set confidence below 0.5.
- source_element_type: "table_cell" if from a table, "text" if from narrative, "chart" if from a chart.
- footnote_ids: list any footnote marker strings (e.g. ["1", "a"]) attached to the value.
- alias_used: the EXACT term or label you found the KPI under in the document (e.g. "Net Revenue" \
when extracting Revenue, or "PAT" when extracting Net Profit). If not found, set to null. \
This is critical for taxonomy improvement — report it even if it matches the KPI name exactly.
- Return EXACTLY one extraction result per KPI in the order listed.
- Do not add extra entries or merge two KPIs into one.""",
    ),
    (
        "human",
        """Target fiscal year: {fiscal_year}

Section: {section_name}
========================
{section_context}
========================

Extract the following KPIs from the section above:
{kpi_list}""",
    ),
])

# Used by the Retry Controller (Agent 8) for single-KPI re-extraction.
# Includes a validator_note field that folds in the specific discrepancy or
# "not found — look harder" instruction from Agent 7's tally check.
TIER3_RETRY_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """You are a financial analyst re-examining a company report section to find
a specific KPI.  A prior extraction attempt either failed or produced a value that
did not pass a validation check.

Rules:
- Extract ONLY values for the target fiscal year.
- Set found=true only when the value is clearly stated or directly derivable.
- CRITICAL — DO NOT FABRICATE: If the exact numeric value does not appear verbatim in the \
provided text or table, set found=false and value=null. NEVER estimate, round, or invent a \
plausible-sounding number. A round number (e.g. 100,000 or 50,000) that you cannot point to \
word-for-word in the text is fabricated — set found=false instead.
- CRITICAL — WRONG SECTION: If the section does not contain a financial statement (balance \
sheet, P&L, cash flow table), do not derive balance sheet or income statement figures from \
narrative commentary. Set found=false.
- Confidence is your certainty that the extracted value is correct (0.0–1.0). \
If you are less than 80% certain the value is exact, set confidence below 0.5.
- source_element_type: "table_cell" if from a table, "text" if from narrative, "chart" if from a chart.
- footnote_ids: list any footnote marker strings attached to the value.
- alias_used: the EXACT term or label you found the KPI under in the document. \
Report this even if it matches the KPI name — it helps improve the taxonomy.
- If you cannot find the value after careful examination, set found=false.""",
    ),
    (
        "human",
        """Target fiscal year: {fiscal_year}

KPI to find:
  ID         : {kpi_id}
  Name       : {kpi_name}
  Aliases    : {aliases}
  Definition : {definition}

Validator note (why this retry was triggered):
{validator_note}

Section: {section_name}
========================
{section_context}
========================

Re-examine the section above and return a single extraction result for the KPI.""",
    ),
])
