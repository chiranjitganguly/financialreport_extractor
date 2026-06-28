from pydantic_settings import BaseSettings


class VectorIndexerSettings(BaseSettings):
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 50

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = VectorIndexerSettings()
