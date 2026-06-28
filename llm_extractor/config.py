from pydantic_settings import BaseSettings


class LLMExtractorSettings(BaseSettings):
    # LLM used to extract KPIs from full section context.
    # Use a more capable model here than in earlier stages — this is the
    # last extraction attempt before a KPI is marked not_found.
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_PROVIDER: str = "openai"
    # Extraction confidence below this threshold → needs_human_review
    LOW_CONFIDENCE_THRESHOLD: float = 0.5

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = LLMExtractorSettings()
