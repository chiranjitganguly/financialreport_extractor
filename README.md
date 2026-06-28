# KPI Extractor — Agentic Pipeline

Extracts a defined set of financial KPIs from company reports (annual reports, quarterly filings, regulatory documents). The pipeline reads narrative text, tables, charts, and footnotes; validates extracted values; escalates uncertain results for human review; and produces a structured XML report with a companion Markdown summary per processed document.

---

## Table of Contents

1. [Architecture overview](#1-architecture-overview)
2. [Pipeline agents](#2-pipeline-agents)
3. [Project layout](#3-project-layout)
4. [Prerequisites](#4-prerequisites)
5. [Environment setup](#5-environment-setup)
6. [Database setup](#6-database-setup)
7. [Running end-to-end](#7-running-end-to-end)
8. [Output files](#8-output-files)
9. [Configuration reference](#9-configuration-reference)
10. [Running tests](#10-running-tests)
11. [Updating the taxonomy](#11-updating-the-taxonomy)
12. [HITL review workflow](#12-hitl-review-workflow)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Architecture overview

```
PDF / DOCX
    │
    ▼
Agent 1 ── Ingestion & Classification
    │         document conversion (Docling → PyMuPDF fallback)
    │         language, report type, accounting standard, industry, company name
    ▼
Agent 2 ── Section Parser & Splitter
    │         splits document into canonical financial sections
    │         fuzzy match → LLM fallback → retry (2-turn shared budget)
    ▼
Agent 3 ── Persistence & Indexing
    │         stores each Section as JSONB in Postgres
    │         chunks & embeds content into pgvector for semantic search
    ▼
Agent 4 ── Deterministic Extractor (Tier 1)
    │         exact / fuzzy table and text matching, no LLM
    ▼
Agent 5 ── Semantic Retriever (Tier 2)
    │         vector-similarity search across indexed chunks
    ▼
Agent 6 ── LLM Extractor (Tier 3)
    │         full-section GPT-4o call, batched per section
    │         reports alias used → taxonomy improvement signals
    ▼
Agent 7 ── Validator
    │         tally rules (gross profit, margins, EPS bounds …)
    │         low-confidence passthrough, footnote materiality check
    ▼
Agent 8 ── Retry Controller
    │         routes failing KPIs back through Tier 2 or Tier 3
    │         consumes the shared 2-turn budget with Agent 2
    ▼
Agent 9 ── Consolidation
              partitions ledger → resolved / needs_review
              writes final XML report + Markdown summary
              archives processed input file
```

The extraction is **deterministic-first**: Tier 1 (regex/fuzzy table matching) runs before any LLM call, minimising API cost. Only KPIs that Tier 1 cannot resolve progress to Tier 2 (semantic retrieval), and only those that Tier 2 cannot resolve go to Tier 3 (LLM). The Markdown summary shows how many KPIs were resolved at each tier so you can tune the deterministic matchers over time.

---

## 2. Pipeline agents

| Agent | Module | Responsibility |
|---|---|---|
| **Agent 1** | `report_ingestion/` | Document conversion, metadata classification |
| **Agent 2** | `section_parser/` | Section splitting and canonical alignment |
| **Agent 3** | `vector_indexer/` | Postgres + pgvector persistence |
| **Agent 4** | `deterministic_extractor/` | Tier 1: exact/fuzzy matching |
| **Agent 5** | `semantic_retriever/` | Tier 2: vector search |
| **Agent 6** | `llm_extractor/` | Tier 3: GPT-4o structured extraction |
| **Agent 7** | `validator/` | Tally rules + low-confidence review |
| **Agent 8** | `retry_controller/` | Retry routing (Tier 2 → Tier 3 escalation) |
| **Agent 9** | `consolidation/` | Final report XML + Markdown summary |

Shared infrastructure lives in `common/`:

| Module | Purpose |
|---|---|
| `common/schemas.py` | All Pydantic wire contracts (`ExtractionRecord`, `Section`, `TaxonomyEntry`, …) |
| `common/taxonomy_map.py` | KPI taxonomy loader, `filter_applicable_taxonomy`, `initialize_extraction_ledger` |
| `common/llm_client.py` | Multi-provider LLM factory (OpenAI / Anthropic / Google) with token tracking |
| `common/token_tracker.py` | Context-scoped token usage tracker (captures input + output tokens per agent) |
| `common/timing_tracker.py` | Wall-clock timer per agent/phase |
| `common/deterministic_matching.py` | Shared fuzzy matching logic (Tier 1 + Tier 2) |
| `common/discrepancy_resolution.py` | Cross-section discrepancy resolver (Step 6a) |
| `common/validation_rules.py` | Validation rules loader |
| `common/output_writer.py` | Per-agent Markdown trace files |
| `common/db.py` | Shared SQLAlchemy engine + session |

---

## 3. Project layout

```
data/
  input/                        ← drop report PDFs / DOCX here before running
  output/
    <CompanyName>/              ← all outputs for one company (created at runtime)
      <Company>_<type>_<fy>_report_ingestion.md
      <Company>_<type>_<fy>_section_parser.md
      <Company>_<type>_<fy>_vector_indexer.md
      <Company>_<type>_<fy>_deterministic_extractor.md
      <Company>_<type>_<fy>_semantic_retriever.md
      <Company>_<type>_<fy>_llm_extractor.md
      report/
        <Company>_<type>_<fy>_final_report.xml   ← structured KPI output
        Summary_<Company>.md                     ← token usage, timing, mismatches
    archive/                    ← processed input files moved here automatically
  company_map/
    industry_map.json           ← company name → industry / accounting standard map
  kpi/
    taxonomy_map.json           ← 101-KPI taxonomy (canonical sections, aliases, …)
  validation_rules/
    validation_rules.json       ← tally + plausibility rules (Agent 7)

common/                         ← shared schemas, clients, utilities
report_ingestion/               ← Agent 1
section_parser/                 ← Agent 2
vector_indexer/                 ← Agent 3
deterministic_extractor/        ← Agent 4
semantic_retriever/             ← Agent 5
llm_extractor/                  ← Agent 6
validator/                      ← Agent 7
retry_controller/               ← Agent 8
consolidation/                  ← Agent 9
extraction_pipeline.py          ← orchestrates Agents 4–6
validation_retry_loop.py        ← orchestrates Agents 7–8
main.py                         ← FastAPI entry point
alembic/                        ← database migrations
tests/                          ← pytest test suite
docker-compose.yml
Dockerfile
requirements.txt
.env                            ← local secrets (never committed)
```

---

## 4. Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11+ | Tested on 3.11 |
| PostgreSQL | 16 | With `pgvector` extension — use the Docker image below |
| [OrbStack](https://orbstack.dev) or Docker Desktop | Latest | Runs the Postgres container |
| OpenAI API key | — | GPT-4o / GPT-4o-mini access required |

---

## 5. Environment setup

### 5.1 Clone and create a virtual environment

```bash
git clone <repo-url>
cd Agentic_Report_Extractor
python3.11 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 5.2 Configure environment variables

Create a `.env` file at the project root:

```bash
cp .env.example .env   # if an example file exists, otherwise create it manually
```

Minimum required content:

```dotenv
# Required
OPENAI_API_KEY=sk-proj-...

# Database (use localhost for local dev; use "postgres" inside Docker)
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kpi_extractor

# Data paths (defaults — change only if you move the data directory)
INDUSTRY_MAP_PATH=data/company_map/industry_map.json
TAXONOMY_MAP_PATH=data/kpi/taxonomy_map.json
VALIDATION_RULES_MAP_PATH=data/validation_rules/validation_rules.json
INPUT_DIR=data/input
OUTPUT_DIR=data/output

# Confidence thresholds (defaults — tune after reviewing summary reports)
CLASSIFICATION_CONFIDENCE_THRESHOLD=0.25
EXTRACTION_CONFIDENCE_THRESHOLD=0.25
INDUSTRY_MAP_FUZZY_CUTOFF=85.0
```

> **Security**: `.env` contains your API key. Never commit it. It is already listed in `.gitignore`.

### 5.3 Start the database

```bash
docker compose up postgres -d
```

This starts PostgreSQL 16 with the `pgvector` extension on `localhost:5432`.  
Database name: `kpi_extractor`, user/password: `postgres/postgres`.

Verify it is healthy:

```bash
docker compose ps
# The "postgres" service should show "(healthy)"
```

---

## 6. Database setup

Run the Alembic migration once after Postgres is up. This creates the `sections` table and enables the `vector` extension:

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kpi_extractor \
  alembic upgrade head
```

To reset the database completely and re-run from scratch:

```bash
docker compose down -v          # removes the postgres_data volume
docker compose up postgres -d   # starts fresh
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kpi_extractor \
  alembic upgrade head
```

---

## 7. Running end-to-end

### 7.1 Place reports in the input directory

```bash
cp /path/to/AnnualReport2024.pdf data/input/
```

Any `.pdf` or `.docx` file placed here will be processed when ingestion is triggered. You can drop multiple files and they will all be processed concurrently.

> After a successful run the input file is automatically **moved to `data/output/archive/`**. To re-process the same file, copy it back to `data/input/`.

### 7.2 Option A — via the FastAPI server (recommended for production)

Start the server:

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kpi_extractor \
  uvicorn main:app --reload
```

Trigger ingestion (processes all files in `data/input/`):

```bash
curl -X POST http://localhost:8000/ingest
```

The response is a JSON map of `report_id → status`. Each `report_id` is a deterministic UUID derived from the filename — running the same file again produces the same ID.

Check the status of a specific report:

```bash
curl http://localhost:8000/reports/<report_id>
```

### 7.3 Option B — run directly from Python (for development)

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kpi_extractor \
  python -c "
import asyncio, logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
from report_ingestion.pipeline import ingest_input_dir
results = asyncio.run(ingest_input_dir())
for rid, out in results.items():
    print(f'{rid}: {out.status}')
"
```

### 7.4 Option C — Docker Compose (full stack)

```bash
docker compose up --build
```

This starts both the FastAPI app and Postgres. The app container runs the Alembic migration on startup, so no manual migration step is needed.

Trigger ingestion:

```bash
curl -X POST http://localhost:8000/ingest
```

Stop everything:

```bash
docker compose down
```

### 7.5 Processing a single file (Python)

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kpi_extractor \
  python -c "
import asyncio, logging, uuid
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
from report_ingestion.pipeline import run_report_ingestion
report_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'MyReport2024'))
result = asyncio.run(run_report_ingestion('data/input/MyReport2024.pdf', report_id))
print('status:', result.status)
"
```

---

## 8. Output files

All outputs are written under `data/output/<CompanyName>/` — **nothing lives outside that folder**.

### 8.1 Per-agent trace files (Markdown)

Written after each agent completes. Useful for debugging:

| File | Contents |
|---|---|
| `<Company>_<type>_<fy>_report_ingestion.md` | Metadata classification: report type, language, accounting standard, industry, confidence scores, flagged fields |
| `<Company>_<type>_<fy>_section_parser.md` | Sections found, alignment source (fuzzy / LLM), confidence, page ranges |
| `<Company>_<type>_<fy>_vector_indexer.md` | Sections indexed, chunk counts, embedding model used |
| `<Company>_<type>_<fy>_deterministic_extractor.md` | KPIs resolved by table/text matching in Tier 1 |
| `<Company>_<type>_<fy>_semantic_retriever.md` | KPIs resolved by vector search in Tier 2 |
| `<Company>_<type>_<fy>_llm_extractor.md` | KPIs resolved by LLM in Tier 3 |

### 8.2 Final XML report

```
data/output/<Company>/report/<Company>_<type>_<fy>_final_report.xml
```

Top-level structure:

```xml
<KPIExtractionReport generated_at="2026-06-28T12:00:00Z">
  <ReportMetadata>
    <CompanyName>Sona Comstar</CompanyName>
    <Industry>Manufacturing</Industry>
    <ReportType>annual_report</ReportType>
    <FiscalYear>FY2024</FiscalYear>
    <AccountingStandard>IND-AS</AccountingStandard>
    <Country>India</Country>
    <Language>en</Language>
    <ReportID>e20711f1-…</ReportID>
  </ReportMetadata>

  <ExtractionSummary>
    <TotalKPIs>101</TotalKPIs>
    <Resolved>95</Resolved>
    <NeedsReview>6</NeedsReview>
  </ExtractionSummary>

  <ResolvedKPIs>
    <KPI id="KPI_004">
      <KPIName>Gross Profit</KPIName>
      <Value>21456.86</Value>
      <Confidence>1.0000</Confidence>
      <ExtractionMethod>llm</ExtractionMethod>
      <ExtractingAgent>llm_extractor</ExtractingAgent>
      <ExtractionDate>2026-06-28</ExtractionDate>
      <DocumentSection>Statement of Profit and Loss</DocumentSection>
      <Page>130</Page>
      <SourceElementType>table_cell</SourceElementType>
    </KPI>
    …
  </ResolvedKPIs>

  <KPIsNeedingReview>
    <KPI id="KPI_019">
      <KPIName>Capital Expenditure</KPIName>
      <Value>3241.50</Value>
      <Confidence>0.7200</Confidence>
      <ReviewReason>section_discrepancy</ReviewReason>
      <DocumentSection>Statement of Cash Flows</DocumentSection>
      <Page>145</Page>
      <SourceElementType>table_cell</SourceElementType>
      <ExtractionDate>2026-06-28</ExtractionDate>
      <ConflictingValues>…</ConflictingValues>
      <AttemptHistory>…</AttemptHistory>
    </KPI>
    …
  </KPIsNeedingReview>
</KPIExtractionReport>
```

`ReviewReason` values:

| Value | Meaning |
|---|---|
| `low_confidence` | LLM confidence below `EXTRACTION_CONFIDENCE_THRESHOLD` |
| `section_discrepancy` | Same KPI found with different values in different sections |
| `footnoted_caveat` | Value has a material footnote (adjusted, non-GAAP, restated, …) |
| `validation_failed` | Tally rule failed and retry budget was exhausted |
| `not_found_after_retries` | KPI not found after all three tiers and retries |

### 8.3 Summary Markdown report

```
data/output/<Company>/report/Summary_<Company>.md
```

Contains:

- **Report metadata** — company, industry, fiscal year, accounting standard
- **Execution timeline** — duration per agent and total run time
- **KPI extraction summary** — counts by retrieval method (Tier 1 / Tier 2 / Tier 3) and by extraction turn (including retry turns)
- **Value changes across turns** — table of KPIs whose value changed between tiers
- **Validation run** — retry turns used, review reasons with KPI counts
- **Token usage** — input tokens, output tokens, total, and API call count broken down by model and by agent
- **Taxonomy alias mismatches** — KPIs the LLM found under a term not in the taxonomy aliases; add these aliases to `data/kpi/taxonomy_map.json` so future runs can resolve them deterministically (cheaper, faster)

### 8.4 Archive

```
data/output/archive/<original-filename>.pdf
```

Input files are moved here automatically after a successful run.

---

## 9. Configuration reference

All values are read from `.env` via `pydantic-settings`. None require a code change to tune.

### Agent 1 — Ingestion & Classification

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | **Required.** OpenAI API key |
| `LLM_MODEL` | `gpt-4o-mini` | Model used for classification fallback |
| `LLM_PROVIDER` | `openai` | Provider: `openai`, `anthropic`, or `google` |
| `CLASSIFICATION_CONFIDENCE_THRESHOLD` | `0.25` | Fields below this score route to human review |
| `INDUSTRY_MAP_PATH` | `data/company_map/industry_map.json` | Company → industry reference map |
| `INDUSTRY_MAP_FUZZY_CUTOFF` | `85.0` | Minimum rapidfuzz score for company-name lookup |
| `CLASSIFICATION_EXCERPT_MAX_CHARS` | `6000` | Characters of document text sent to classifiers |

### Agent 2 — Section Parser

| Variable | Default | Description |
|---|---|---|
| `SECTION_ALIGNMENT_FUZZY_CUTOFF` | `85.0` | Minimum rapidfuzz score for Stage A section alignment |
| `SECTION_ALIGNMENT_CONFIDENCE_THRESHOLD` | `0.25` | Sections below this after retry are marked `best_guess_unresolved` |
| `SECTION_HEADING_LEVEL_CUTOFF` | `2` | H1/H2 headings create sections; H3+ become body text |

### Agent 3 — Persistence & Indexing

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@postgres:5432/kpi_extractor` | Postgres connection string. Use `localhost` for local dev; `postgres` inside Docker |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI embedding model |
| `CHUNK_SIZE` | `500` | Max characters per narrative text chunk |
| `CHUNK_OVERLAP` | `50` | Overlap between consecutive chunks |

### Agents 4–6 — Extraction Cascade

| Variable | Default | Description |
|---|---|---|
| `TAXONOMY_MAP_PATH` | `data/kpi/taxonomy_map.json` | KPI taxonomy (sections, aliases, definitions) |
| `EXTRACTION_CONFIDENCE_THRESHOLD` | `0.25` | LLM extractions below this score go to human review |
| `DISCREPANCY_LLM_MODEL` | `gpt-4o-mini` | Model for cross-section discrepancy resolution (Step 6a) |

### Agent 7 — Validator

| Variable | Default | Description |
|---|---|---|
| `VALIDATION_RULES_MAP_PATH` | `data/validation_rules/validation_rules.json` | Tally and plausibility rules |
| `FOOTNOTE_MATERIALITY_KEYWORDS` | `["adjusted","excludes","restated","non-gaap",…]` | Keywords that trigger `footnoted_caveat` review |

---

## 10. Running tests

The test suite has **438 tests** across all agents. LLM calls are always mocked — no real API calls, no API key required for tests.

### 10.1 Quick run — all tests

```bash
pytest
```

Or more verbosely:

```bash
pytest -v --tb=short
```

### 10.2 Exclude the database integration test

The `test_documents.py` test requires a running Postgres instance with a `kpi_extraction_test` database. Skip it when Postgres is not running:

```bash
pytest --ignore=tests/test_documents.py
```

Or use the marker:

```bash
pytest -m "not integration"
```

### 10.3 Run tests for a specific agent

```bash
# Agent 1 — Ingestion & Classification
pytest tests/test_pipeline.py tests/test_classifiers.py \
       tests/test_confidence_routing.py tests/test_industry_map.py -v

# Agent 2 — Section Parser
pytest tests/test_splitter.py tests/test_alignment.py \
       tests/test_element_assignment.py tests/test_agent2_pipeline.py -v

# Agent 3 — Persistence & Indexing (unit + mocked; no DB needed)
pytest tests/test_chunking.py tests/test_embeddings.py \
       tests/test_agent3_pipeline.py -v

# Agent 3 — Database integration (requires Postgres with kpi_extraction_test DB)
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kpi_extraction_test \
  pytest tests/test_documents.py -v

# Agents 4–6 — Extraction cascade
pytest tests/test_deterministic_matching.py tests/test_tier1.py \
       tests/test_tier2.py tests/test_tier3.py \
       tests/test_extraction_cascade.py \
       tests/test_discrepancy_resolution.py -v

# Agents 7–8 — Validation & Retry
pytest tests/test_tally.py tests/test_final_review.py \
       tests/test_retry_routing.py tests/test_validation_retry_loop.py -v

# Agent 9 — Consolidation
pytest tests/test_consolidation.py -v
```

### 10.4 Set up the test database (for test_documents.py)

```bash
# Create the test database
docker compose exec postgres psql -U postgres -c "CREATE DATABASE kpi_extraction_test;"

# Run migrations against the test database
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kpi_extraction_test \
  alembic upgrade head
```

Then run the DB integration test:

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kpi_extraction_test \
  pytest tests/test_documents.py -v
```

### 10.5 Test design rules

- **No real LLM calls**: all `ChatOpenAI` / `get_llm_client()` calls are mocked with `unittest.mock.patch` and `RunnableLambda` fakes. The test suite runs offline and does not consume OpenAI credits.
- **Real Postgres for DB tests**: `test_documents.py` runs against a real (test) database — not a mocked ORM. This catches query bugs that an in-memory mock would miss.
- **Fixtures in `tests/fixtures/`**: sample documents, recorded LLM responses, and synthetic taxonomy entries are stored here. Do not generate fixtures live in tests.

---

## 11. Updating the taxonomy

The taxonomy file `data/kpi/taxonomy_map.json` drives which KPIs are extracted, which document sections they are searched in, and what aliases the deterministic matcher uses. Keeping it up to date reduces LLM calls and improves accuracy.

### Adding a new KPI

Add an entry to `data/kpi/taxonomy_map.json`:

```json
{
  "kpi_id": "KPI_102",
  "kpi_name": "Research and Development Expense",
  "definition": "Total expenditure on research and development activities in the fiscal year.",
  "canonical_sections": ["Statement of Profit and Loss", "Notes to Financial Statements"],
  "applicable_industries": ["All"],
  "applicable_report_types": ["Annual Report", "Quarterly Report"],
  "applicable_accounting_standards": ["All"],
  "aliases": ["R&D expense", "R&D costs", "research expense", "development costs"]
}
```

- `kpi_id` must be unique.
- `canonical_sections` must match entries in the section vocabulary exactly (case-sensitive).
- `applicable_industries` / `applicable_report_types` / `applicable_accounting_standards`: use `"All"` to apply to every report regardless of category.
- `aliases`: **the more aliases you add, the more Tier 1 (deterministic) can resolve without an LLM call**. After each pipeline run, check the `## Taxonomy Alias Mismatches` section of the Summary Markdown — it lists terms the LLM found the KPI under that are not yet in the aliases list.

### Acting on alias mismatches

The `Summary_<Company>.md` report contains a table like:

```
| KPI ID | KPI Name    | Known Aliases              | Term Found in Document |
|--------|-------------|----------------------------|------------------------|
| KPI_012| Net Profit  | Net Profit, PAT, Net Income| **Profit After Tax**   |
```

Add `"Profit After Tax"` to the `aliases` list for `KPI_012` in `taxonomy_map.json`. On the next run this KPI will be resolved by Tier 1 (deterministic) instead of Tier 3 (LLM), saving API cost.

---

## 12. HITL review workflow

Some reports trigger human-in-the-loop (HITL) review when Agent 1 classifies a field with confidence below `CLASSIFICATION_CONFIDENCE_THRESHOLD`. The report is queued and will not proceed to later agents until corrections are submitted.

### Identifying flagged reports

```bash
curl http://localhost:8000/reports/<report_id>
# status: "awaiting_input"
# flagged_fields: ["industry", "accounting_standard"]
```

### Submitting corrections

```bash
curl -X POST http://localhost:8000/reports/<report_id>/resolve \
  -H "Content-Type: application/json" \
  -d '{
    "corrections": {
      "industry": "Automotive Components",
      "accounting_standard": "IND-AS"
    }
  }'
```

Only include the fields listed in `flagged_fields`. Corrections are stored in the `hitl_review_queue` table and merge with the best-guess classifier results to form the final `ReportMetadata`.

---

## 13. Troubleshooting

### `[Errno 8] nodename nor servname provided` on local dev

The `.env` file ships with `DATABASE_URL=postgresql://postgres:postgres@postgres:5432/kpi_extractor`. The hostname `postgres` resolves inside Docker but not from your local machine. For local Python runs set:

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kpi_extractor
```

or prefix every command:

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kpi_extractor python -c "..."
```

### `ModuleNotFoundError: No module named 'docling'`

Docling is the primary document converter. If it is not installed (it has heavy ML dependencies), the pipeline automatically falls back to PyMuPDF. To install it:

```bash
pip install docling
```

Docling downloads its ML models on first use (~1–2 GB) and caches them in `.docling_cache/`. This only happens once.

### `FATAL: database "kpi_extractor" does not exist`

The database has not been created yet. Run:

```bash
docker compose up postgres -d
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/kpi_extractor \
  alembic upgrade head
```

### `ValueError: Taxonomy map at '...' is empty`

`data/kpi/taxonomy_map.json` is missing or empty. The file must exist and contain at least one KPI entry. Check the path matches `TAXONOMY_MAP_PATH` in `.env`.

### `Could not load validation rules` warning

`data/validation_rules/validation_rules.json` is missing or uses a different schema. The pipeline continues with no tally checks — this is not fatal. To fix, ensure the file is a JSON array of objects with the fields: `rule_id`, `description`, `rule_type`, `formula`, `participating_kpi_ids`, `tolerance`.

### `FiscalYear` is empty in the output XML

The pipeline could not detect a fiscal year from the document. The report classifier does not currently extract `fiscal_year` deterministically — it is populated only if the LLM fallback returns it. The file is still processed and KPIs are extracted; only the filename and metadata field will show blank.

### `taxonomy filter returned 0 applicable KPIs`

The `report_type` or `accounting_standard` detected by Agent 1 does not match the values in the taxonomy. Normalized comparison is used (case-insensitive, strips spaces/dashes/underscores), but mismatches can still occur. Check what Agent 1 classified (`data/output/<Company>/<Company>_report_ingestion.md`) and compare to the `applicable_report_types` / `applicable_accounting_standards` fields in `data/kpi/taxonomy_map.json`.

### Tests fail with `AttributeError: module 'section_parser.alignment' has no attribute 'llm'`

The module-level `llm` singleton was replaced with a lazy `get_llm()` factory. Tests must patch `section_parser.alignment.get_llm` (not `.llm`). All tests in this repo already use the correct patch target.

---

## Appendix — Data contracts

The canonical data contracts shared across all agents are defined in `common/schemas.py`. Key types:

| Type | Description |
|---|---|
| `ReportMetadata` | Output of Agent 1: company, industry, report type, language, accounting standard, fiscal year |
| `TaxonomyEntry` | One KPI from the taxonomy: id, name, definition, sections, aliases, applicability filters |
| `Section` | Output of Agent 2: canonical name, content markdown, tables, charts, footnotes, page range |
| `ExtractionRecord` | One KPI's extraction result: value, method, confidence, status, attempts, alias used |
| `ExtractionLedger` | Map of `kpi_id → ExtractionRecord` passed through the full cascade |
| `ValidationRule` | Tally or plausibility rule evaluated by Agent 7 |
