# Agent 1: Ingestion & Classification — Implementation Design (v3 — code-ready)

**Status**: Final for code generation. Every function below specifies its arguments, return type, and logic precisely enough to hand directly to Claude Code. Supersedes v2 — the company reference map's expanded schema changes the Accounting Standard Detector's design, not just the Industry Selector's.

---

## 1. What changed in this revision

The industry map is now a JSON file with **four fields per entry**: `company_name`, `industry`, `accounting_standard` (optional), `country`. This means:

- **Company name extraction + fuzzy lookup is now a single shared step**, consumed by *both* the Industry Selector and the Accounting Standard Detector — not an Industry-only step. Don't implement two separate lookups.
- **Accounting Standard Detector gains a middle stage**: document statement (Stage A, highest trust — a specific filing can use a different standard than the company's usual one, e.g. a reconciliation note) → company map (Stage B, when present — it's optional in the source data) → GPT-4o fallback (Stage C).
- **`country` has no fallback classification.** If the company lookup matches, populate it; if not, leave it `null`. No other source is defined for it, and nothing downstream currently consumes it (added to `ReportMetadata` for potential future jurisdiction-specific logic — see the FINAL spec).

---

## 2. Company Reference Map — schema and loading

### 2.1 Schema

```json
[
  {
    "company_name": "British Telecom",
    "industry": "telecom",
    "accounting_standard": "IFRS",
    "country": "United Kingdom"
  },
  {
    "company_name": "Example Manufacturing Co",
    "industry": "industrials",
    "accounting_standard": null,
    "country": "United States"
  }
]
```
`company_name`, `industry`, `country` are required per entry; `accounting_standard` is nullable.

### 2.2 `schemas.py`

```python
from pydantic import BaseModel
from typing import Optional, Literal

class CompanyReferenceEntry(BaseModel):
    company_name: str
    industry: str
    accounting_standard: Optional[Literal["IFRS", "US-GAAP", "IND-AS", "OTHER"]] = None
    country: str
```

### 2.3 `industry_map.py`

```python
def load_company_reference_map(path: str) -> list[CompanyReferenceEntry]:
    """
    Load and validate the JSON reference file once at process/pipeline startup
    (not per-document) — fail fast on a schema error here, not mid-pipeline.

    Args:
        path: filesystem path to the JSON file. Sourced from
              config.settings.INDUSTRY_MAP_PATH (see §6) — never hardcode the path.

    Returns:
        list[CompanyReferenceEntry]

    Raises:
        ValueError: if the file isn't valid JSON or any entry fails Pydantic validation.
                    Raise immediately with the offending entry's index/company_name in
                    the message — don't silently skip bad entries.
    """


class CompanyLookupResult(BaseModel):
    matched_entry: CompanyReferenceEntry
    match_score: float  # normalized 0-1, derived from the fuzzy ratio (see below)


def lookup_company(
    company_name: str,
    reference_map: list[CompanyReferenceEntry],
    fuzzy_cutoff: float,
) -> Optional[CompanyLookupResult]:
    """
    Fuzzy-match the extracted company name against every entry's `company_name`
    field and return the best match if it clears the cutoff.

    Args:
        company_name: the candidate name extracted from the document
                      (output of extract_company_name_llm(), see §3.2)
        reference_map: the loaded list from load_company_reference_map()
        fuzzy_cutoff: minimum acceptable match quality, 0-100 scale (rapidfuzz's
                      native scale) — sourced from
                      config.settings.INDUSTRY_MAP_FUZZY_CUTOFF (default 85, see §6)

    Returns:
        CompanyLookupResult if the best match's rapidfuzz.fuzz.ratio(...) score
        is >= fuzzy_cutoff, with match_score = that ratio / 100.
        None if no entry clears the cutoff (triggers Stage C fallback downstream
        for both industry and accounting standard).

    Implementation note: use `rapidfuzz.process.extractOne(company_name,
    [e.company_name for e in reference_map], scorer=fuzz.WRatio)` — WRatio
    handles partial/word-order differences better than plain ratio for company
    names (e.g. "British Telecommunications plc" vs "British Telecom").
    """
```

---

## 3. Document Conversion

### 3.1 `converter.py`

```python
class DoclingConversionResult(BaseModel):
    narrative_markdown: str       # full narrative text, page-anchored, tables/figures excluded
    docling_document: object      # raw DoclingDocument, passed through to Agent 1b — not used further in Agent 1
    page_count: int

def convert_document(file_path: str) -> DoclingConversionResult:
    """
    Primary document conversion via Docling.

    Args:
        file_path: absolute path to the uploaded report (PDF/DOCX)

    Returns:
        DoclingConversionResult

    Raises:
        DocumentConversionError: if Docling cannot process the file at all
        (corrupt file, unsupported format edge case). Caller (pipeline.py)
        catches this and falls back to convert_document_fallback().
    """

def convert_document_fallback(file_path: str) -> str:
    """
    PyMuPDF raw-text fallback, used only when convert_document() raises.

    Args:
        file_path: same input file

    Returns:
        raw_text: unstructured text, page breaks marked but no guaranteed
                   heading/layout structure. Sufficient for the classifiers
                   below (they only need text), not sufficient for Agent 2's
                   section splitting — flag this report for a quality check
                   if this path is taken, since downstream sections will be
                   degraded.
    """
```

### 3.2 Text excerpting helper

All classifiers below operate on a **truncated excerpt**, not the full document — keep this as one shared utility so every caller truncates the same way.

```python
def get_classification_excerpt(narrative_markdown: str, max_chars: int = 6000) -> str:
    """
    Returns the first `max_chars` characters of the narrative markdown —
    covers the cover page, title block, and typically the start of the
    business description / notes section for most report layouts.

    Args:
        narrative_markdown: full converted text
        max_chars: default 6000 (~1500 tokens) — generous enough to usually
                   catch an accounting-standard statement, which sometimes
                   appears a few pages in rather than on the cover.

    Returns:
        str, truncated at a paragraph boundary where possible (don't cut
        mid-sentence if avoidable — affects extraction quality)
    """
```

---

## 4. Classifiers

### 4.1 Language Detector — `classifiers/language.py` (deterministic, no LLM)

```python
class LanguageResult(BaseModel):
    language: str   # ISO 639-1 code
    confidence: float

def detect_language(narrative_markdown: str) -> LanguageResult:
    """
    Args:
        narrative_markdown: pass get_classification_excerpt() output, not the
                            full document — lingua-py doesn't need more.
    Returns:
        LanguageResult — lingua-py's native confidence score, no further
        processing. Always resolves; there is no fallback stage for language.
    """
```

### 4.2 Company Name Extraction — `classifiers/company_name.py` (shared step)

```python
class CompanyNameResult(BaseModel):
    company_name: str
    confidence: float

def extract_company_name_llm(excerpt: str) -> CompanyNameResult:
    """
    GPT-4o structured-output call. This is a SHARED step — call it once per
    document, then pass the result into both detect_industry_from_map() and
    detect_accounting_standard_from_map() (via lookup_company()). Do not
    call this separately for industry vs. accounting standard.

    Args:
        excerpt: get_classification_excerpt() output

    Returns:
        CompanyNameResult

    Prompt guidance: ask specifically for the *reporting entity's* legal or
    common name as it appears on the cover/title page — not a subsidiary or
    auditor name that might also appear in the excerpt.
    """
```

### 4.3 Report Type Selector — `classifiers/report_type.py`

```python
class ReportTypeResult(BaseModel):
    report_type: Literal["annual_report", "quarterly_report", "regulatory_filing"]
    confidence: float
    source: Literal["document_marker", "llm_fallback"]
    evidence: Optional[str] = None

def detect_report_type_deterministic(excerpt: str) -> Optional[ReportTypeResult]:
    """
    Stage A only. Regex/keyword search for explicit markers: form-type
    strings ("10-K", "10-Q", "Form 20-F"), or self-identifying title text
    ("Annual Report", "Quarterly Report", "Interim Report").

    Args:
        excerpt: get_classification_excerpt() output

    Returns:
        ReportTypeResult(confidence=1.0, source="document_marker") if a
        marker is matched, else None (falls through to the batched LLM
        fallback in fallback.py — there is no separate Stage B function
        for this field; it's folded into run_classification_fallback()).
    """
```

### 4.4 Accounting Standard Detector — `classifiers/accounting_standard.py`

```python
class AccountingStandardResult(BaseModel):
    standard: Literal["IFRS", "US-GAAP", "IND-AS", "OTHER"]
    confidence: float
    source: Literal["document_statement", "company_map", "llm_fallback"]
    evidence: Optional[str] = None

def detect_accounting_standard_deterministic(excerpt: str) -> Optional[AccountingStandardResult]:
    """
    Stage A. Regex for explicit statements ("prepared in accordance with
    International Financial Reporting Standards", "in conformity with U.S.
    GAAP", etc.) — this is the document's own claim and takes precedence
    over the company map below, since a specific filing can legitimately
    use a different standard than the company's usual one (e.g. a US-GAAP
    reconciliation note inside an otherwise-IFRS filing).

    Args:
        excerpt: get_classification_excerpt() output. If accounting-standard
                 statements in your sample documents tend to appear deeper
                 in the notes section than the cover-page excerpt covers,
                 widen the excerpt for this specific check rather than
                 increasing the shared default in §3.2.

    Returns:
        AccountingStandardResult(confidence=1.0, source="document_statement")
        or None.
    """

def detect_accounting_standard_from_map(
    lookup_result: Optional[CompanyLookupResult],
) -> Optional[AccountingStandardResult]:
    """
    Stage B. Uses the company reference map's `accounting_standard` field —
    only available when both (a) the company was matched above the fuzzy
    cutoff, and (b) that entry's `accounting_standard` isn't null (it's
    optional in the source data).

    Args:
        lookup_result: the SAME CompanyLookupResult already computed once
                       for the Industry Selector (§4.5) — do not re-run
                       lookup_company() here.

    Returns:
        AccountingStandardResult(confidence=lookup_result.match_score,
        source="company_map") if lookup_result is not None AND
        lookup_result.matched_entry.accounting_standard is not None.
        Otherwise None (falls through to Stage C).
    """
```

### 4.5 Industry Selector — `classifiers/industry.py`

```python
class IndustryResult(BaseModel):
    industry: str
    confidence: float
    source: Literal["company_map", "llm_fallback"]

def detect_industry_from_map(
    lookup_result: Optional[CompanyLookupResult],
) -> Optional[IndustryResult]:
    """
    Stage B (there is no Stage A for industry — the company map is the
    primary source, LLM is the only fallback).

    Args:
        lookup_result: output of lookup_company() (§2.3), called once
                       with the company name from extract_company_name_llm()

    Returns:
        IndustryResult(confidence=lookup_result.match_score,
        source="company_map") if lookup_result is not None.
        None if lookup_result is None (company not found above the fuzzy
        cutoff) — falls through to Stage C (LLM fallback).
    """
```

### 4.6 Batched LLM Fallback — `fallback.py`

```python
class ClassificationFallbackResult(BaseModel):
    report_type: Optional[ReportTypeResult] = None
    accounting_standard: Optional[AccountingStandardResult] = None
    industry: Optional[IndustryResult] = None

def run_classification_fallback(
    excerpt: str,
    needed_fields: list[Literal["report_type", "accounting_standard", "industry"]],
    valid_industries: list[str],
) -> ClassificationFallbackResult:
    """
    ONE batched GPT-4o structured-output call covering every field that
    didn't resolve in Stages A/B above. Call this at most once per document
    — if needed_fields is empty, don't call it at all.

    Args:
        excerpt: get_classification_excerpt() output (widen it here too if
                 accounting_standard is in needed_fields and standard
                 statements tend to sit outside the default excerpt window)
        needed_fields: exactly which fields to ask for — build the Pydantic
                       schema passed to with_structured_output() dynamically
                       so the model isn't asked to reclassify something
                       already resolved. (Simplest implementation: build one
                       schema with all three as Optional fields, but state in
                       the prompt which fields are actually needed and instruct
                       the model to leave the others null — confirm this
                       matches your LangChain version's structured-output
                       behavior, since some versions handle Optional fields
                       in with_structured_output more reliably than others.)
        valid_industries: the Taxonomy Map's full industry vocabulary, used
                          to build a `Literal[...]` type for the industry
                          field dynamically (only relevant if "industry" is
                          in needed_fields)

    Returns:
        ClassificationFallbackResult with `source="llm_fallback"` set on
        every populated sub-result, and the model's self-reported confidence
        per field (see CLAUDE.md §4 on the self-reported-confidence pattern).
    """
```

---

## 5. Confidence Routing — `confidence.py`

```python
class FieldReview(BaseModel):
    field_name: Literal["report_type", "language", "accounting_standard", "industry"]
    value: str
    confidence: float
    reason: str   # e.g. "0.18 < threshold 0.25"

def route_confidence(
    report_type: ReportTypeResult,
    language: LanguageResult,
    accounting_standard: AccountingStandardResult,
    industry: IndustryResult,
    country: Optional[str],
    threshold: float,
) -> tuple[Optional["ReportMetadata"], list[FieldReview]]:
    """
    Args:
        report_type, language, accounting_standard, industry: final results
                     after Stages A/B/C have all been attempted as needed
        country: from the company lookup if matched, else None — NOT
                  confidence-checked (no fallback/threshold applies to it,
                  per §1)
        threshold: config.settings.CLASSIFICATION_CONFIDENCE_THRESHOLD
                   (default 0.25)

    Returns:
        (report_metadata, flagged_fields):
        - If every field's confidence >= threshold: report_metadata is a
          complete ReportMetadata (country included, possibly null), and
          flagged_fields is [].
        - If any field is below threshold: report_metadata is None,
          flagged_fields lists every field that failed the check (field_name,
          its current best-guess value, its confidence, and a human-readable
          reason string). The caller (pipeline.py) routes this to
          hitl_queue.enqueue_for_review().
    """
```

---

## 6. Configuration — `config.py`

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    OPENAI_API_KEY: str
    INDUSTRY_MAP_PATH: str
    CLASSIFICATION_CONFIDENCE_THRESHOLD: float = 0.25
    INDUSTRY_MAP_FUZZY_CUTOFF: float = 85.0   # rapidfuzz 0-100 scale
    CLASSIFICATION_EXCERPT_MAX_CHARS: int = 6000

    class Config:
        env_file = ".env"
```
All four tunables are read from environment/`.env` — none should appear as a bare literal anywhere else in the codebase.

---

## 7. HITL Queue — `hitl_queue.py`

```python
def enqueue_for_review(
    report_id: str,
    resolved_fields: dict,       # whatever WAS resolved, for context
    flagged_fields: list[FieldReview],
) -> None:
    """
    Inserts into the `review_queue` table and sets `reports.status =
    'awaiting_input'` for this report_id. Does not block — pipeline.py
    returns immediately after calling this (async queue-and-resume per
    the main spec).
    """

def resolve_review(report_id: str, corrections: dict[str, str]) -> "ReportMetadata":
    """
    Called by the FastAPI correction endpoint, not by the pipeline directly.

    Args:
        report_id: which report is being resumed
        corrections: field_name -> human-corrected value, only for the
                     fields that were actually flagged

    Returns:
        completed ReportMetadata (merging corrections with whatever had
        already passed confidence routing), and flips status to 'resumed'
        so the Orchestrator can pick the report back up.
    """
```

---

## 8. Top-Level Orchestration — `pipeline.py`

```python
class Agent1Output(BaseModel):
    status: Literal["ready", "awaiting_input"]
    report_metadata: Optional["ReportMetadata"] = None
    narrative_markdown: str
    flagged_fields: list[FieldReview] = []

async def run_agent1(file_path: str, report_id: str) -> Agent1Output:
    """
    Args:
        file_path: path to the uploaded report
        report_id: unique id, used for HITL queue tracking and as the key
                   for everything downstream

    Steps (in order):
        1. Try convert_document(file_path); on DocumentConversionError,
           fall back to convert_document_fallback(file_path).
        2. excerpt = get_classification_excerpt(narrative_markdown)
        3. Run concurrently (asyncio.gather):
           - detect_language(excerpt)
           - detect_report_type_deterministic(excerpt)
           - detect_accounting_standard_deterministic(excerpt)
           - extract_company_name_llm(excerpt)
        4. lookup_result = lookup_company(company_name.company_name,
           reference_map, fuzzy_cutoff)   [reference_map loaded once at
           startup, not per-call]
        5. accounting_standard = (Stage A result) or
           detect_accounting_standard_from_map(lookup_result)
           industry = detect_industry_from_map(lookup_result)
        6. needed_fields = whichever of [report_type, accounting_standard,
           industry] are still None after steps 3 and 5
        7. If needed_fields is non-empty: call run_classification_fallback()
           once with that list, and fill in the corresponding results
        8. country = lookup_result.matched_entry.country if lookup_result
           else None
        9. route_confidence(...) with all final field results + threshold
        10. If flagged_fields is empty: return Agent1Output(status="ready",
            report_metadata=..., narrative_markdown=...)
            Else: call hitl_queue.enqueue_for_review(...), return
            Agent1Output(status="awaiting_input", flagged_fields=...,
            narrative_markdown=...)

    Returns:
        Agent1Output
    """
```

---

## 9. Updated module structure

```
agent1_ingestion/
  __init__.py
  config.py                  # §6
  schemas.py                  # ReportMetadata, CompanyReferenceEntry, CompanyLookupResult,
                              #   all classifier Result models, FieldReview, Agent1Output
  converter.py                # §3
  industry_map.py             # §2 — load_company_reference_map(), lookup_company()
  prompts.py                  # ChatPromptTemplate defs for: company name extraction,
                              #   report type fallback, accounting standard fallback,
                              #   industry fallback (combined into the batched call's prompt)
  llm_client.py                # shared ChatOpenAI(model="gpt-4o", temperature=0).with_retry()
  classifiers/
    language.py                # §4.1
    company_name.py             # §4.2
    report_type.py              # §4.3
    accounting_standard.py      # §4.4
    industry.py                 # §4.5
  fallback.py                  # §4.6 — batched LLM call
  confidence.py                # §5
  hitl_queue.py                # §7
  pipeline.py                  # §8
tests/
  fixtures/
    company_reference_map.json   # synthetic — see synthetic data doc
    sample_reports/              # synthetic — see synthetic data doc
  test_industry_map.py
  test_classifiers.py
  test_confidence_routing.py
  test_pipeline.py               # with all LLM calls mocked
```

---

## 10. Open items carried into this revision

- **`with_structured_output` and dynamic Optional fields**: confirm your installed LangChain/langchain-openai version handles a schema with several `Optional[...]` fields cleanly when only some are populated by the model — behavior here has changed across versions. If it's unreliable, fall back to building three separate minimal schemas selected at call time based on `needed_fields`, rather than one big sometimes-null schema.
- **Excerpt window for accounting-standard statements**: the default 6000-char excerpt may not reach a standard statement that's buried in notes-to-accounts on some report layouts. Recommend testing against a few real sample reports early and widening `CLASSIFICATION_EXCERPT_MAX_CHARS` (or building a separate, larger excerpt specifically for the accounting-standard check) if this proves to be a systematic miss.
- **rapidfuzz scorer choice**: `WRatio` is the suggested default for company-name matching, but worth validating against the real reference map — if it produces false-positive matches between similarly-named but distinct companies, `token_sort_ratio` may behave more conservatively.
