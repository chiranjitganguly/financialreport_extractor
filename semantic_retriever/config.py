from pydantic_settings import BaseSettings


class SemanticRetrieverSettings(BaseSettings):
    SEMANTIC_TOP_K: int = 5
    # Reuses the same WRatio scorer and cutoff as Tier 1 — tunable independently
    # if empirical results show Tier 2 needs a different threshold.
    TABLE_ROW_FUZZY_CUTOFF: float = 85.0

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = SemanticRetrieverSettings()
