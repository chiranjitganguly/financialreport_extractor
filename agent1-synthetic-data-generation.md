# Agent 1 — Synthetic Test Data Generation Instructions

Two tiers of fixtures, since Agent 1's logic splits cleanly into "things that need real document structure" (Docling conversion) and "things that only need text" (every classifier). Build Tier 1 first — it's what most unit tests actually need.

---

## Tier 1: Plain-text fixtures (for classifier unit tests)

These bypass Docling entirely — they're just strings standing in for `get_classification_excerpt()` output, used to test each classifier's logic in isolation. Fast to generate, fast to run in CI.

### 1.1 Synthetic Company Reference Map

Create `tests/fixtures/company_reference_map.json` with deliberate edge cases — don't just generate clean, easy entries, since the point is to exercise the fuzzy-matching and null-handling logic:

| Case to include | Why |
|---|---|
| 5–10 entries with an **exact-name match** to what a synthetic report will use | Baseline — confirms the happy path works |
| 3–5 entries where the report's cover-page name **differs slightly** from the map's `company_name` (e.g. map has "British Telecom", report cover says "British Telecommunications plc") | Exercises the fuzzy-match cutoff — these should still resolve via `lookup_company()` |
| 2–3 entries with `accounting_standard: null` | Exercises Stage B falling through to Stage C for accounting standard specifically, while industry still resolves from the map |
| 1–2 entries with deliberately **similar names to each other** (e.g. "Atlas Energy Group" and "Atlas Energy Holdings") | Stress-tests whether the fuzzy matcher picks the right one, or whether `WRatio` vs `token_sort_ratio` matters in practice (see Open Items in the design doc) |
| At least 1 company that will be **referenced in a synthetic report but deliberately absent from the map** | Forces the "no match" path — both industry and accounting standard should fall through to Stage C (GPT-4o fallback) |

Generation approach: don't hand-write 20+ JSON entries — ask GPT-4o directly to generate the array, since it's a structured, repetitive task:

> "Generate a JSON array of 20 fictitious or real publicly-known companies, each with fields company_name, industry (one of: [paste your Taxonomy Map's industry vocabulary]), accounting_standard (IFRS, US-GAAP, IND-AS, or null — make about 4 of them null), and country. Include at least 3 pairs of companies with very similar names to each other (e.g. same root name, different suffix like 'Holdings' vs 'Group')."

Then manually edit in the specific near-miss-naming pairs from the table above if the generated set doesn't already cover them, since that's the part most worth controlling precisely.

### 1.2 Synthetic Classification Excerpts

For each classifier, generate short text snippets covering both the "Stage A succeeds" and "Stage A fails, must fall through" cases. Use GPT-4o to generate these — it's well-suited to producing plausible cover-page/notes-section prose:

**Report type excerpts** — generate pairs like:
- "Contains an explicit marker" — e.g. a cover page reading "ACME Corp — Annual Report 2025" → should resolve at Stage A.
- "No explicit marker" — e.g. a cover page with just a company logo description and date, no self-identifying title → forces the LLM fallback.

**Accounting standard excerpts** — three variants per synthetic company:
- A notes-to-accounts paragraph with an **explicit statement** ("These financial statements have been prepared in accordance with International Financial Reporting Standards (IFRS) as adopted by the EU...") → Stage A.
- A notes section **without** an explicit statement, for a company that IS in the reference map with a non-null `accounting_standard` → should resolve at Stage B.
- A notes section without an explicit statement, for a company **not** in the map → should resolve at Stage C (GPT-4o fallback) — and this is also a good case to verify the model's self-reported confidence behaves reasonably (i.e. doesn't claim high confidence when it's genuinely guessing).

**Company name extraction excerpts** — vary the cover-page format itself (some reports put the name in a large title, some in a footer/letterhead style, some only in a "Dear Shareholders" salutation) so `extract_company_name_llm()` is tested against realistic layout variance, not just one clean format.

**Language excerpts** — at least one non-English sample (e.g. a French or German cover page) to confirm `detect_language()` isn't just defaulting to English.

**Deliberately ambiguous case** — generate at least one excerpt designed to produce genuinely low confidence on a field (e.g. a company name extraction excerpt where the "company" mentioned is ambiguous between the reporting entity and a subsidiary, or a report-type excerpt with conflicting signals) — this is what exercises the HITL queue path. Given the configured threshold is 0.25 (quite permissive), this needs to be a *real* ambiguity, not just mildly unclear text, to actually trigger review.

Generation prompt pattern for all of the above:

> "Write a realistic [cover page / notes-to-accounts paragraph] for a fictitious company's [annual report / quarterly report], in the style of a real filing, that [does / does not] explicitly state its [report type / accounting standard]. Keep it under 150 words."

Save each as a small `.txt` fixture file under `tests/fixtures/excerpts/`, named descriptively (e.g. `accounting_standard_explicit_ifrs.txt`, `accounting_standard_no_statement_company_in_map.txt`, `company_name_ambiguous_subsidiary.txt`).

### 1.3 Recorded LLM Responses (for mocking)

Per the testing convention in `CLAUDE.md` (never call the real API in tests), pair each Tier 1 excerpt that exercises an LLM fallback path with a **recorded expected response** — the structured Pydantic output you'd expect GPT-4o to return for that input. Store these alongside the excerpts (e.g. `tests/fixtures/llm_responses/accounting_standard_fallback_example.json`) and use them as the mock return value when testing `run_classification_fallback()` and `extract_company_name_llm()`. Generate these by actually running the real call once during fixture creation (not in the test suite itself) and saving the output — this gives you a realistic recorded response rather than a hand-guessed one.

---

## Tier 2: End-to-end document fixtures (for full pipeline / Docling conversion tests)

These exercise `convert_document()` and the full `run_agent1()` flow, including the fallback to PyMuPDF.

### 2.1 Assembling synthetic PDFs

1. Use GPT-4o to generate fuller synthetic report content — not just an excerpt, but several sections worth (cover page, a short MD&A-style section, a notes-to-accounts excerpt) — for 3–5 synthetic reports covering the matrix of cases from Tier 1 (explicit vs. no accounting-standard statement, company in map vs. not, etc.).
2. Assemble each into an actual PDF using an open-source PDF generation library — **fpdf2** (LGPL-3.0) or **reportlab**'s open-source distribution (BSD) — simple text-to-PDF, no need for visual fidelity to a real report; the point is to exercise the conversion path, not test layout parsing quality.
3. Save under `tests/fixtures/sample_reports/`, named to match what they test (e.g. `annual_report_explicit_ifrs_company_in_map.pdf`).

### 2.2 Forcing the PyMuPDF fallback path

Generate at least one deliberately malformed/corrupted PDF (e.g. truncate a valid PDF's byte stream, or use a known-problematic minimal PDF structure) to verify `convert_document()` raises `DocumentConversionError` correctly and `pipeline.py` falls through to `convert_document_fallback()` rather than crashing.

### 2.3 What NOT to do

Don't generate large, fully-realistic 100+ page synthetic annual reports for this stage — Agent 1 only reads the first few thousand characters of any document. A short, structurally valid PDF with realistic cover-page and notes content is sufficient; spending effort on full-length synthetic financial statements is solving a problem Agent 1 doesn't have (that level of document fidelity matters much more for Agent 1b's table/chart extraction later, not for classification).

---

## Summary checklist

- [ ] `company_reference_map.json` — 15-20 entries with exact matches, near-miss-name pairs, null accounting standards, and one company deliberately absent
- [ ] Per-classifier text excerpts covering Stage A success, Stage A failure (→ B or C), and one genuinely ambiguous case per field that should trigger HITL
- [ ] Recorded LLM responses paired with each fallback-triggering excerpt, for mocking
- [ ] 3-5 short synthetic PDFs covering the same case matrix, for end-to-end pipeline tests
- [ ] 1 malformed PDF to test the PyMuPDF fallback path
