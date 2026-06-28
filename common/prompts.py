"""Shared LangChain prompt templates used by more than one agent.

Agent-specific prompts live in the agent's own prompts.py.  Only prompts
consumed by common/ modules (discrepancy resolution, future shared calls)
belong here.
"""

from langchain_core.prompts import ChatPromptTemplate

DISCREPANCY_RESOLUTION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            (
                "You are a financial data extraction expert.  You will be given a KPI "
                "name, its definition, and two or more candidate values extracted from "
                "different sections of the same financial report.  Your task is to "
                "determine which candidate is the authoritative value for this KPI.\n\n"
                "Rules:\n"
                "- Prefer values from formal financial statements (Balance Sheet, "
                "Statement of Profit and Loss, Statement of Cash Flows) over narrative "
                "sections (MD&A, Financial Highlights).\n"
                "- Prefer table_cell sources over text or chart sources.\n"
                "- If one candidate is a rounded/approximated version of another, "
                "prefer the more precise one.\n"
                "- If the candidates represent different fiscal periods, the correct "
                "one is the one matching fiscal_year='{fiscal_year}'.\n"
                "- If genuinely ambiguous, choose the value from the section most "
                "closely aligned with the KPI's definition and set confidence below 0.7."
            ),
        ),
        (
            "human",
            (
                "KPI: {kpi_name}\n"
                "Definition: {definition}\n"
                "Fiscal year target: {fiscal_year}\n\n"
                "Candidate values:\n{candidates_text}\n\n"
                "Section content for context:\n{sections_context}\n\n"
                "Determine the authoritative value."
            ),
        ),
    ]
)
