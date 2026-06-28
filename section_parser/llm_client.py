"""Section-parser LLM singleton.

Uses the model/provider configured in section_parser.config so the alignment
step can run a different model from the ingestion/extraction agents
(e.g. a smaller model for heading classification).
"""

from common.llm_client import get_llm_client
from section_parser.config import settings

llm = get_llm_client(model=settings.LLM_MODEL, provider=settings.LLM_PROVIDER)
