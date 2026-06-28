from pydantic_settings import BaseSettings


class ReportIngestionSettings(BaseSettings):
    INDUSTRY_MAP_PATH: str = "data/company_map/industry_map.json"
    INPUT_DIR: str = "data/input"
    CLASSIFICATION_CONFIDENCE_THRESHOLD: float = 0.25
    INDUSTRY_MAP_FUZZY_CUTOFF: float = 85.0
    CLASSIFICATION_EXCERPT_MAX_CHARS: int = 6000
    # LLM used for company-name extraction and classification fallback.
    # Swap LLM_PROVIDER=anthropic / google without touching any code.
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_PROVIDER: str = "openai"
    model_config = {"env_file": ".env", "extra": "ignore"}


settings = ReportIngestionSettings()
