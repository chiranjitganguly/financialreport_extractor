from pydantic_settings import BaseSettings


class DeterministicExtractorSettings(BaseSettings):
    TABLE_ROW_FUZZY_CUTOFF: float = 85.0  # rapidfuzz WRatio 0-100

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = DeterministicExtractorSettings()
