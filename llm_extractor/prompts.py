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
- Confidence is your certainty that the extracted value is correct (0.0–1.0).
- source_element_type: "table_cell" if from a table, "text" if from narrative, "chart" if from a chart.
- footnote_ids: list any footnote marker strings (e.g. ["1", "a"]) attached to the value.
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
