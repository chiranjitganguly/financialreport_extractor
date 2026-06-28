"""Section-parser LLM factory.

Returns a fresh LLM client on each call so that the active TokenUsageTracker
(if any) is picked up at call time rather than at module import time.
"""

from langchain_core.language_models import BaseChatModel

from common.llm_client import get_llm_client
from section_parser.config import settings


def get_llm() -> BaseChatModel:
    return get_llm_client(
        model=settings.LLM_MODEL,
        provider=settings.LLM_PROVIDER,
        agent_name="section_parser",
    )
