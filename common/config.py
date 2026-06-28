"""Shared infrastructure settings for the KPI-extraction pipeline.

Settings that are consumed by *multiple* agents (API keys, database URL,
taxonomy path, output directory) live here so that common/ modules do not
need to import from any specific agent package.

Each agent's own config.py inherits these via pydantic-settings' env-file
merging — all settings read from the same .env file, so there is no
duplication at runtime.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class CommonSettings(BaseSettings):
    # LLM credentials — providers pick up their keys automatically from these
    # standard env vars when api_key is not passed explicitly to LangChain.
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""

    # Database (shared across all agents that read/write Postgres)
    DATABASE_URL: str = "postgresql://localhost:5432/kpi_extractor"

    # Taxonomy map path (shared: report_ingestion loads it, extraction_pipeline uses it)
    TAXONOMY_MAP_PATH: str = "data/kpi/taxonomy_map.json"

    # Output directory for per-agent Markdown trace files
    OUTPUT_DIR: str = "data/output"

    # LLM used by cross-agent utilities (discrepancy resolution).
    # Set DISCREPANCY_LLM_PROVIDER=anthropic / google to swap models without
    # touching any agent-specific config.
    DISCREPANCY_LLM_MODEL: str = "gpt-4o-mini"
    DISCREPANCY_LLM_PROVIDER: str = "openai"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = CommonSettings()
