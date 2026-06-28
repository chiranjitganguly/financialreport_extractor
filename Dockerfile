FROM python:3.11-slim

# System libraries required by PyMuPDF, Docling, and lingua
RUN apt-get update && apt-get install -y --no-install-recommends \
        libmagic1 \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies before copying source so this layer is cached
# independently of code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY common/ ./common/
COPY report_ingestion/ ./report_ingestion/
COPY section_parser/ ./section_parser/
COPY vector_indexer/ ./vector_indexer/
COPY deterministic_extractor/ ./deterministic_extractor/
COPY semantic_retriever/ ./semantic_retriever/
COPY llm_extractor/ ./llm_extractor/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY data/ ./data/
COPY extraction_pipeline.py .
COPY main.py .

# Docling downloads its ML models on first use.  Point the cache at a
# directory that can be mounted as a volume so models persist across
# container restarts and are not re-downloaded every time.
ENV DOCLING_ARTIFACTS_PATH=/app/.docling_cache
ENV TRANSFORMERS_CACHE=/app/.hf_cache
VOLUME ["/app/.docling_cache", "/app/.hf_cache"]

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
