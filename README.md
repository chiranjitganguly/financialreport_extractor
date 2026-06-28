# KPI Extractor — Agentic Pipeline

Extracts financial KPIs from annual reports, quarterly filings, and regulatory documents. The pipeline is built as a sequence of specialised agents; each one operates on the structured output of the previous.

| Agent | Responsibility | Status |
|---|---|---|
| **Agent 1** — Ingestion & Classification | Document conversion, language detection, report-level metadata (report type, accounting standard, industry) | Complete |
| **Agent 2** — Section Parser & Splitter | Splits the converted document into canonical financial sections (Balance Sheet, MDA, Cash Flows, …), assigns tables, charts, and footnotes to sections | Complete |
| **Agent 3** — Persistence & Indexing | Persists each Section to Postgres (JSONB), chunks all content (text, tables, charts, footnotes), embeds with `text-embedding-3-small`, stores in pgvector | Complete |
| Agents 4–9 | KPI extraction, validation, HITL escalation, ledger assembly | Planned |

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| [OrbStack](https://orbstack.dev) | Latest | Runs Docker containers on Mac |
| Docker Compose | Bundled with OrbStack | |
| OpenAI API key | — | GPT-4o access required |

---

## Project layout

```
common/
  schemas.py              # shared Pydantic contracts used by all agents
  taxonomy_map.py         # taxonomy loader (canonical section vocab, industry vocab)
  db.py                   # shared SQLAlchemy engine + SessionLocal (Agent 3+)
  storage_models.py       # SQLAlchemy ORM models (SectionRow, …)

agent1_ingestion/
  pipeline.py             # run_agent1(), ingest_input_dir()
  classifiers.py          # report type, accounting standard, industry
  converter.py            # Docling primary + PyMuPDF fallback
  confidence_routing.py   # threshold check → HITL flag
  hitl_queue.py           # Postgres HITL queue + agent_runs table
  persistence.py          # save_agent_run() / load_agent_run()
  schemas.py              # Agent 1 I/O schemas (re-exports common.schemas)
  config.py               # all settings via pydantic-settings
  prompts.py              # LLM prompt templates

agent2_section_parser/
  pipeline.py             # run_agent2()
  splitter.py             # split document → RawSection list
  alignment.py            # Stage A fuzzy + Stage B LLM batch alignment
  element_assignment.py   # assign tables/charts/footnotes to sections by page
  schemas.py              # Agent 2 I/O schemas (Agent2Input, Agent2Output, …)
  prompts.py              # LLM prompt templates

agent3_persistence/
  pipeline.py             # run_agent3()
  documents.py            # persist_section(), get_section(), get_sections_by_report()
  chunking.py             # chunk_narrative_text(), chunk_table(), chunk_chart(), chunk_footnote()
  embeddings.py           # embed_and_store_chunks() — PGVector + text-embedding-3-small
  schemas.py              # TextChunk, Agent3Output

alembic/
  versions/
    0001_enable_pgvector_and_sections_table.py  # CREATE EXTENSION vector + sections table
  env.py                  # Alembic runtime config (reads DATABASE_URL from settings)
alembic.ini

data/
  company_map/
    industry_map.json     # company → industry/accounting-standard reference map
  kpi/
    taxonomy_map.json     # KPI taxonomy (101 entries, canonical section vocabulary)
  input/                  # drop annual report PDFs/DOCX here
  output/                 # per-report agent outputs written here at runtime

tests/
  fixtures/               # synthetic tables, charts, footnotes for Agent 2 tests
  test_pipeline.py        # Agent 1 pipeline (91 tests)
  test_classifiers.py
  test_confidence_routing.py
  test_industry_map.py
  test_splitter.py        # Agent 2 splitter (17 tests)
  test_alignment.py       # Agent 2 alignment — mocked LLM (15 tests)
  test_element_assignment.py  # element-to-section assignment (22 tests)
  test_agent2_pipeline.py     # Agent 2 end-to-end pipeline (16 tests)
  test_chunking.py            # Agent 3 chunkers — pure unit (43 tests)
  test_embeddings.py          # Agent 3 embeddings — mocked PGVector (12 tests)
  test_agent3_pipeline.py     # Agent 3 orchestration — mocked I/O (18 tests)
  test_documents.py           # Agent 3 Document DB — real Postgres (integration)

main.py                   # FastAPI entrypoint
docker-compose.yml
Dockerfile
.env                      # local secrets — never committed
```

---

## Setup

### 1. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in your OpenAI API key:

```
OPENAI_API_KEY=sk-...
```

All other values have working defaults and do not need to change for a standard local run.

### 2. Add annual reports

Drop PDF or DOCX files into `data/input/`:

```bash
cp /path/to/report.pdf data/input/
```

The application processes every `.pdf` and `.docx` file found in that directory when `/ingest` is called.

---

## Running the application

### Start all services

```bash
docker compose up --build
```

This starts:
- **app** — FastAPI server on `http://localhost:8000`
- **postgres** — PostgreSQL 16 on `localhost:5432` (database: `kpi_extractor`)

The app container waits for Postgres to be healthy before starting.

> **First run note:** Docling downloads its ML models on first use (~1–2 GB). They are cached in a Docker volume (`docling_cache`) and are not re-downloaded on subsequent starts.

### Trigger ingestion (Agent 1)

Once the server is running, POST to `/ingest` to process all reports in `data/input/`:

```bash
curl -X POST http://localhost:8000/ingest
```

Returns a JSON map of `report_id → Agent1Output` for every file processed. Each output includes:
- `report_metadata` — report type, accounting standard, industry, language, country, fiscal year
- `status` — `ready` or `awaiting_input`
- `flagged_fields` — fields below the confidence threshold, queued for human review
- `narrative_markdown` — full document text extracted by Docling (or PyMuPDF fallback)

### Check a report's status

```bash
curl http://localhost:8000/reports/<report_id>
```

Returns the most recent persisted Agent 1 run. Status is either `ready` (metadata fully classified) or `awaiting_input` (one or more fields fell below the confidence threshold and need human review).

### Submit human corrections (HITL)

For reports with `status: awaiting_input`, supply corrected field values:

```bash
curl -X POST http://localhost:8000/reports/<report_id>/resolve \
  -H "Content-Type: application/json" \
  -d '{"corrections": {"industry": "Technology", "report_type": "annual_report"}}'
```

Only the flagged fields need to be included in `corrections`.

---

## Stopping the application

```bash
docker compose down
```

To also remove the database volume (resets all stored data):

```bash
docker compose down -v
```

---

## Local development (without Docker)

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

For local dev, update `DATABASE_URL` in `.env` to point at a local Postgres instance:

```
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kpi_extractor
```

Then run the server directly:

```bash
uvicorn main:app --reload
```

### Database migrations (required before first run)

Agent 3 uses the Postgres `sections` table and pgvector. Run the Alembic migration once after the database is up:

```bash
# Against the application database
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kpi_extractor \
  alembic upgrade head

# Against the test database (needed before running test_documents.py)
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kpi_extraction_test
DATABASE_URL=$TEST_DATABASE_URL alembic upgrade head
```

Or run from inside the app container:

```bash
docker compose exec app alembic upgrade head
```

### Running the test suite

No real API calls are made in tests — all LLM interactions are mocked.

```bash
# All agents (excludes DB integration tests unless TEST_DATABASE_URL is set)
pytest -m "not integration"

# Agent 1 only
pytest tests/test_pipeline.py tests/test_classifiers.py tests/test_confidence_routing.py tests/test_industry_map.py -v

# Agent 2 only
pytest tests/test_splitter.py tests/test_alignment.py tests/test_element_assignment.py tests/test_agent2_pipeline.py -v

# Agent 3 — unit and mocked tests only (no DB needed)
pytest tests/test_chunking.py tests/test_embeddings.py tests/test_agent3_pipeline.py -v

# Agent 3 — DB integration tests (requires Postgres with kpi_extraction_test database)
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kpi_extraction_test \
  pytest tests/test_documents.py -v
```

---

## Agent 2 — Section Parser & Splitter

Agent 2 takes Agent 1's output (the converted markdown document plus extracted tables, charts, and footnotes) and produces a list of `Section` objects where each section is aligned to a canonical name from the KPI taxonomy.

### How it works

**Stage A — Fuzzy alignment (no LLM)**
Each raw section heading is matched against the canonical vocabulary using rapidfuzz `WRatio`. Sections that score above `SECTION_ALIGNMENT_FUZZY_CUTOFF` (default 85/100) are immediately resolved.

**Stage B — Batch LLM fallback**
All headings that did not clear Stage A are sent in a single GPT-4o call with a structured-output schema. Batching is intentional — one API call regardless of how many sections are unresolved.

**Stage C — Concurrent retry**
Sections whose Stage B confidence is below `SECTION_ALIGNMENT_CONFIDENCE_THRESHOLD` (default 0.25) are retried with a richer prompt that includes the section's content excerpt. Retries run concurrently via `asyncio.gather`. The retry result is kept only if confidence improved.

**Best-guess stamping**
Sections still below threshold after retry are stamped `alignment_source: best_guess_unresolved` and set `has_unresolved_sections: True` on the output — signalling that human review may be needed.

**Element assignment**
Tables, charts, and footnotes are assigned to sections by page number. Page containment is checked first; if an element falls in a gap between sections, it is assigned to the nearest preceding section.

### Section vocabulary

Canonical section names come from `data/kpi/taxonomy_map.json`:

```
Balance Sheet
Financial Highlights
Management Discussion and Analysis
Notes to Financial Statements
Statement of Cash Flows
Statement of Profit and Loss
```

Any section heading that cannot be confidently aligned to one of these is mapped to `OTHER`.

### Output contract

`Agent2Output` fields:

| Field | Type | Description |
|---|---|---|
| `report_id` | `str` | Propagated from Agent 1 |
| `sections` | `list[Section]` | Aligned sections with elements attached |
| `retry_turns_used` | `int` | Number of retry attempts consumed |
| `has_unresolved_sections` | `bool` | True if any section is `best_guess_unresolved` |

Each `Section` carries:

| Field | Description |
|---|---|
| `section_name_raw` | Original heading as it appeared in the document |
| `section_name_canonical` | Aligned canonical name (or `OTHER`) |
| `alignment_confidence` | 0–1 float; source-dependent (fuzzy score or LLM self-reported) |
| `alignment_source` | `fuzzy_match`, `llm_fallback`, or `best_guess_unresolved` |
| `content_markdown` | Section body text |
| `tables` | Tables whose page falls within this section |
| `charts` | Charts whose page falls within this section |
| `footnotes` | Footnotes whose page falls within this section |
| `page_range` | `(first_page, last_page)` tuple |

---

## Configuration reference

All values can be overridden via `.env` without code changes.

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | Required. GPT-4o API key |
| `DATABASE_URL` | `postgresql://postgres:postgres@postgres:5432/kpi_extractor` | Postgres connection string |
| `INDUSTRY_MAP_PATH` | `data/company_map/industry_map.json` | Company → industry reference map |
| `TAXONOMY_MAP_PATH` | `data/kpi/taxonomy_map.json` | KPI taxonomy (section vocab + industry vocab) |
| `INPUT_DIR` | `data/input` | Directory scanned for report files |
| `OUTPUT_DIR` | `data/output` | Per-report agent outputs written here |
| `CLASSIFICATION_CONFIDENCE_THRESHOLD` | `0.25` | Agent 1: fields below this go to human review |
| `INDUSTRY_MAP_FUZZY_CUTOFF` | `85.0` | Agent 1: minimum rapidfuzz score for company-name matching |
| `CLASSIFICATION_EXCERPT_MAX_CHARS` | `6000` | Characters of document text sent to Agent 1 classifiers |
| `SECTION_ALIGNMENT_FUZZY_CUTOFF` | `85.0` | Agent 2: minimum rapidfuzz score for Stage A section alignment |
| `SECTION_ALIGNMENT_CONFIDENCE_THRESHOLD` | `0.25` | Agent 2: sections below this after retry are marked best-guess |
| `SECTION_HEADING_LEVEL_CUTOFF` | `2` | Agent 2: H1/H2 create sections; H3+ treated as body text |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Agent 3: OpenAI embeddings model |
| `CHUNK_SIZE` | `500` | Agent 3: maximum characters per narrative text chunk |
| `CHUNK_OVERLAP` | `50` | Agent 3: overlap between consecutive narrative chunks |

---

## Agent 3 — Persistence & Indexing

Agent 3 receives Agent 2's `Section` list and handles all storage. It runs immediately after Agent 2 and is entirely synchronous (no LLM calls).

### What it does

**Step 1 — Document DB (Postgres JSONB)**
Each `Section` is written as a single JSONB blob to the `sections` table. The full object — including nested tables, charts, and footnotes — is stored intact so downstream agents can reconstruct it with a single `SELECT`. Document keys (UUIDs) are returned in `Agent3Output.document_keys`.

**Step 2 — Vector DB (pgvector)**
All content is broken into `TextChunk` objects by source type:
- **Narrative text** → split by `RecursiveCharacterTextSplitter` at `CHUNK_SIZE` characters
- **Table rows** → one chunk per row, serialised as `"{row_label} — {col}: {val}; ..."`
- **Chart interpretations** → one chunk per chart
- **Footnotes** → one chunk per footnote

All chunks across all sections are embedded in a single batched `add_texts()` call. Each chunk carries metadata (`report_id`, `section_name_canonical`, `source_element_type`, page information) so Agents 5+ can filter vector search results to a specific report.

### Storage schema

| Table | Purpose |
|---|---|
| `sections` | One JSONB row per Section; primary key is a UUID returned as `document_key` |
| `langchain_pg_embedding` | PGVector collection `kpi_chunks`; one row per TextChunk |

### Fetching stored sections

```bash
# Get all sections stored for a report
curl http://localhost:8000/reports/<report_id>/sections
```
