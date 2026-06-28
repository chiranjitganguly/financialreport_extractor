from pydantic_settings import BaseSettings


class SectionParserSettings(BaseSettings):
    SECTION_ALIGNMENT_FUZZY_CUTOFF: float = 85.0
    SECTION_ALIGNMENT_CONFIDENCE_THRESHOLD: float = 0.25
    SECTION_HEADING_LEVEL_CUTOFF: int = 2
    # LLM used for section-name alignment when fuzzy matching falls below threshold.
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_PROVIDER: str = "openai"
    model_config = {"env_file": ".env", "extra": "ignore"}


settings = SectionParserSettings()
