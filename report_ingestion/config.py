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

    # Document conversion backend.
    # "langextract" (default): uses GPT-4o-mini via langextract to extract sections.
    # "docling":               uses Docling for structured conversion (see converter.py).
    # Change via CONVERTER_BACKEND env var without touching code.
    CONVERTER_BACKEND: str = "langextract"

    # LLM model used by the langextract backend for section extraction.
    # Change via LANGEXTRACT_MODEL_ID env var (e.g. "gpt-4o" for higher quality).
    LANGEXTRACT_MODEL_ID: str = "gpt-4o-mini"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = ReportIngestionSettings()
