"""Report-ingestion LLM singleton.

Uses the model/provider configured in report_ingestion.config so that
classification and company-name extraction can use a different model from
the rest of the pipeline (e.g. a faster/cheaper model for classification).
"""

from common.llm_client import get_llm_client
from report_ingestion.config import settings

llm = get_llm_client(model=settings.LLM_MODEL, provider=settings.LLM_PROVIDER)
