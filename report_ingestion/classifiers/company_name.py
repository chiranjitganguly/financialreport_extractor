from report_ingestion.llm_client import get_llm
from report_ingestion.prompts import COMPANY_NAME_PROMPT
from report_ingestion.schemas import CompanyNameResult


async def extract_company_name_llm(excerpt: str) -> CompanyNameResult:
    """Extract the reporting entity's name via a GPT-4o structured-output call.

    This is a SHARED step — call it exactly once per document in pipeline.py,
    then pass the result into both detect_industry_from_map() and
    detect_accounting_standard_from_map() via lookup_company(). Do NOT call
    this separately for industry vs. accounting standard.

    Args:
        excerpt: Output of get_classification_excerpt().

    Returns:
        CompanyNameResult with the reporting entity's legal or common name as
        it appears on the cover/title page (not a subsidiary or auditor name)
        and the model's self-reported confidence.
    """
    chain = COMPANY_NAME_PROMPT | get_llm().with_structured_output(CompanyNameResult).with_retry()
    return await chain.ainvoke({"excerpt": excerpt})
