# CLAUDE.md

Standing context for Claude Code working in this repository. This project implements the agentic KPI-extraction pipeline defined in `kpi-extraction-agentic-spec-FINAL.md` — read that file (and the per-agent design docs, e.g. `agent1-ingestion-classification-design.md`) before generating code for any agent; they are the source of truth for data contracts and behavior, this file is about *how* to build it.

---

## 1. Project Overview

- **Purpose**: extract a defined set of financial KPIs from company reports (annual report / quarterly report / regulatory filing), reading narrative text, tables, charts, and footnotes, validating results, and surfacing anything uncertain for human review.
- **Reference docs in this repo** (treat as authoritative for contracts/behavior):
  - `kpi-extraction-agentic-spec-FINAL.md` — full pipeline spec: all 9 agents + Agent 1b, shared data contracts, orchestration rules
  - `kpi_extraction_workflow.drawio` — visual architecture diagram
  - `agent1-ingestion-classification-design.md` — Agent 1 implementation design
  - `agent1-synthetic-data-generation.md` — Agent 1 test fixture generation instructions
  - `agent2-section-parser-design.md` — Agent 2 implementation design
  - `agent3-persistence-indexing-design.md` — Agent 3 implementation design
  - `agent4-5-6-extraction-cascade-design.md` — Agents 4, 5, 6 + Step 6a implementation design (current build focus)
- **Current build focus**: **Agents 4, 5, 6 (the extraction cascade)**, building on completed Agents 1–3. These three are designed and built together, not independently — Tier 2 explicitly reuses Tier 1's matching logic (`common/deterministic_matching.py`), and the discrepancy-resolution step is shared across all three (`common/discrepancy_resolution.py`). Don't implement Tier 2's matching logic separately from Tier 1's. Agents 7 and 8 (Validator, Retry Controller) are not built yet — the extraction cascade's entry point (`run_extraction_cascade()`) runs Tiers 1→2→3 exactly once with no retry loop; that loop belongs to Agent 7/8 later.
- **`common/` package**: now also includes the shared LLM client (`llm_client.py`, promoted from Agent 1 — if Agent 1 still has its own local copy, consolidate it now), `ExtractionRecord`/`ExtractionLedger` schemas, the Taxonomy Map applicability filter, shared deterministic matching logic, and the cross-section discrepancy resolver. This package is the connective tissue between agents — when in doubt about whether something is agent-local or shared, check here first; duplicated logic across agents is a signal something belongs in `common/` instead.

---

## 2. Tech Stack

| Layer | Choice |
|---|---|
| Language | Python |
| LLM | OpenAI GPT-4o |
| LLM framework | LangChain (`langchain`, `langchain-openai`, `langchain_core`) |
| Document conversion | Docling (tables, footnotes, figures, XBRL — shared with Agent 1b) |
| Raw text fallback | PyMuPDF |
| Language detection | lingua-py |
| Fuzzy string matching | rapidfuzz (industry map lookup) |
| Schema validation | Pydantic |
| Config | pydantic-settings |
| HITL queue (MVP) | Postgres + FastAPI |
| Document DB | Postgres (JSONB) — same instance as HITL queue, not a separate database |
| Vector DB | Postgres + `pgvector` extension — same instance again |
| Vector store integration | `langchain_postgres.PGVector` |
| Embeddings | OpenAI `text-embedding-3-small` via `langchain-openai` |
| DB toolkit | SQLAlchemy + Alembic for migrations |
| Testing | pytest |

Do not introduce a different LLM provider, a different web framework, or a different document-conversion library without being asked — these choices were made deliberately (see the design docs for the reasoning) and swapping them silently would invalidate decisions already made.

---

## 3. Data Contracts

Every agent's input/output must match the JSON contracts defined in `kpi-extraction-agentic-spec-FINAL.md` §2 (`ReportMetadata`, `TaxonomyEntry`, `TableElement`, `ChartElement`, `FootnoteElement`, `Section`, `ExtractionRecord`, `ExtractionLedger`). Implement these as Pydantic models in `schemas.py` for whichever agent you're building — field names, types, and nullability should mirror the spec exactly, not a reinterpretation of it. If a contract seems to need a change to make an agent work, that's a spec discussion, not a silent code-level deviation.

---

## 4. LLM Usage Conventions

- **Always use structured output**: every GPT-4o call goes through `ChatOpenAI(...).with_structured_output(SomePydanticModel)` — never parse free-form text completions with regex/string matching. Define the expected Pydantic schema first, then the call.
- **Temperature 0** for all classification/extraction calls in this pipeline — these are deterministic-intent tasks, not creative generation.
- **Self-reported confidence**: where a task needs a confidence score and there's no cleaner signal available (e.g. LLM-based classification fallbacks), include a `confidence: float` field directly in the structured output schema and have the model populate it. Treat this as a tunable heuristic, not a calibrated probability — don't write code that assumes it's perfectly accurate.
- **Constrained vocabulary**: whenever an LLM call must choose from a fixed set of valid values (industry list, accounting standard, report type), use a Pydantic `Literal[...]` (built dynamically from the Taxonomy Map / industry map where the valid set isn't static) in the structured output model — don't accept a free-form string and validate it after the fact.
- **Batch fallback calls**: when multiple fields on the same document need an LLM fallback simultaneously, combine them into a single structured-output call with a combined schema rather than firing one API call per field. Cost and latency matter here — GPT-4o is a paid API per call.
- **Retries**: use LangChain's `.with_retry()` on the runnable for transient failures (rate limits, timeouts). Don't hand-roll retry/backoff logic.
- **Never hardcode the API key** — `OPENAI_API_KEY` comes from environment/`.env`, loaded via `pydantic-settings`. Never write it into source, tests, or logs.

---

## 5. Configuration

All tunable values are settings, not constants buried in code:
- `CLASSIFICATION_CONFIDENCE_THRESHOLD` — default `0.25`, the bar below which any classified field routes to human review. Must be changeable via environment variable without a code change.
- Industry map file path — the company-name → industry lookup data is supplied externally (CSV/JSON); load it via a configured path, never embed sample/test data as the "real" map in source.
- Fuzzy-match cutoff for industry lookup — keep this as a setting too, not a hardcoded number, since it'll need empirical tuning against the real company-name map.

---

## 6. Code Style & Structure

- Pydantic models for every structured boundary (agent inputs/outputs, LLM structured-output schemas) — no passing around raw dicts between functions where a contract is defined.
- Deterministic-first pattern: every classifier that has a cheap deterministic stage (regex/keyword/exact-match) runs that before falling back to an LLM call. Don't reach for GPT-4o as the first attempt where a cheaper, more reliable check is described in the design doc.
- Async where independent work can run concurrently (e.g. Agent 1's four classifiers against the same extracted text) — use `asyncio.gather`, don't serialize independent work needlessly.
- One module per logical responsibility (see the suggested structure in `agent1-ingestion-classification-design.md` §9) — don't collapse multiple classifiers into one file for convenience.
- Prompts live in a dedicated `prompts.py`, not inlined as string literals inside classifier functions.
- **Company name extraction + reference-map lookup is a single shared step**, not duplicated per classifier. Both the Industry Selector and the Accounting Standard Detector consume the same `CompanyLookupResult` — call `extract_company_name_llm()` and `lookup_company()` exactly once per document in `pipeline.py`, and pass the result into both classifiers.
- **All Postgres access goes through `common/db.py`'s shared engine/session** — never open a separate connection or engine inside an individual agent's module. Any new table needs an Alembic migration, not a manual `CREATE TABLE` or an ORM `create_all()` call run ad hoc.
- **Document DB stores whole objects, not normalized rows.** A `Section` (including its nested tables/charts/footnotes) is one JSONB blob per row — don't normalize nested lists into child tables unless a future agent demonstrates an actual need for SQL-level querying over them (none does yet; they're all consumed as Python objects after a full-section fetch).
- **One fuzzy-matching convention for the whole codebase**: `rapidfuzz` with `WRatio` is used for company-name lookup (Agent 1), section alignment (Agent 2), and table-row matching (Agent 4/5) — don't introduce a different fuzzy-matching library or scorer for a new use case without a specific reason; consistency here makes tuning the cutoffs predictable across the codebase.
- **Tier 1 and Tier 2 share matching logic by design, not by convenience** — `common/deterministic_matching.py` is the single implementation of "how do we find a KPI's value in a table row or text span," called by both agents. If you find yourself writing matching logic inside `agent5_tier2/`, stop — it belongs in `common/` and Tier 2 should call it.

---

## 7. Testing

- **Never call the real OpenAI API in tests.** Mock `ChatOpenAI`/the structured-output call (e.g. via a fake runnable or recorded fixture responses) so the test suite is deterministic, fast, and doesn't incur API cost or require a key to run in CI.
- **Database tests run against a real Postgres test instance** (the `docker-compose.yml` service, pointed at a separate test database — e.g. `kpi_extraction_test`), not against mocked SQL. Mocking an ORM/SQL layer tends to test the mock, not the query — for persistence-layer code, a real (test) database catches real bugs that a mock won't. This is different from the LLM-mocking rule above; the two aren't in tension; just don't run tests against the production database.
- Unit test each classifier's deterministic stage (regex matching) independently of the LLM fallback stage.
- Test the confidence-routing logic (threshold comparison, HITL queue hand-off) with a range of confidence values, including exactly at the threshold boundary.
- Use `tests/fixtures/` for sample documents and recorded LLM responses rather than live-generating fixtures on each test run.

---

## 8. What Claude Should Do When Something Is Ambiguous

- If a data contract in the spec doesn't cover a case you've hit while implementing, say so explicitly rather than inventing a field or silently reshaping the contract.
- If a design doc and the main spec seem to disagree (e.g. a detail in `agent1-ingestion-classification-design.md` that isn't reflected back in the main spec's Agent 1 section), flag the discrepancy — don't pick one silently.
- Prefer asking over guessing for anything that affects a shared data contract (since other agents, not yet built, will depend on it being right). For implementation-only details with no downstream contract impact (e.g. internal variable names, which regex library), use reasonable judgment and proceed.
