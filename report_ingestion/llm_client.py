"""Report-ingestion LLM factory.

Returns a fresh LLM client on each call so that the active TokenUsageTracker
(if any) is picked up at call time rather than at module import time.
Callers use `get_llm()` to get a token-tracked client.
"""

from langchain_core.language_models import BaseChatModel

from common.llm_client import get_llm_client
from report_ingestion.config import settings


def get_llm() -> BaseChatModel:
    return get_llm_client(
        model=settings.LLM_MODEL,
        provider=settings.LLM_PROVIDER,
        agent_name="report_ingestion",
    )
