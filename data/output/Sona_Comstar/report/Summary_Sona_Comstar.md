# Extraction Summary: Sona Comstar

**Generated**: 2026-06-28 17:43:25 UTC  
**Report ID**: e20711f1-7997-579b-9c4a-25ca9d80c85b  
**Total Run Time**: 10.1m

## Report Metadata

| Field | Value |
|---|---|
| Company | Sona Comstar |
| Industry | Manufacturing |
| Report Type | annual_report |
| Fiscal Year |  |
| Accounting Standard | IND-AS |
| Country | India |
| Language | en |

## Execution Timeline

| Agent / Phase | Duration |
| ------------- | -------- |
| report_ingestion | 7.0s |
| section_parser | 1.8s |
| vector_indexer | 57.9s |
| extraction_cascade | 8.1m |
| validation_retry_loop | 53.5s |
| consolidation | 0.0s |
| **Total** | **10.1m** |

## KPI Extraction Summary

**Total KPIs in scope**: 101  
**Resolved (found)**: 38  
**Needs Review**: 63

### By Retrieval Method (Final State)

| Method | KPIs Extracted |
| ------ | -------------- |
| Deterministic (Tier 1) | 2 |
| Semantic Retrieval (Tier 2) | 0 |
| LLM Extraction (Tier 3) | 42 |

### By Extraction Turn

| Turn | KPIs Found |
| ---- | ---------- |
| Turn 1 — Deterministic (Tier 1) | 2 |
| Turn 2 — Semantic Retrieval (Tier 2) | 0 |
| Turn 3 — LLM Extraction (Tier 3) | 28 |
| Retry Turn 1 — LLM Retry | 14 |

## KPI Value Changes Across Turns

_No KPIs passed through multiple tiers with differing values._

## Validation Run

**Retry turns used**: 1  
| Review Reason | Count | KPI IDs (sample) |
| ------------- | ----- | ---------------- |
| not_found_after_retries | 58 | KPI_001, KPI_020, KPI_027, KPI_030, KPI_033… |
| section_discrepancy | 3 | KPI_037, KPI_040, KPI_050 |
| validation_failed | 2 | KPI_010, KPI_011 |

## Token Usage

| Model | Provider | Input Tokens | Output Tokens | Total Tokens | API Calls |
| ----- | -------- | ------------ | ------------- | ------------ | --------- |
| gpt-4o-mini | openai | 524,817 | 40,386 | 565,203 | 43 |

**Overall Total**: 565,203 tokens (524,817 in + 40,386 out)

### Per-Agent Token Breakdown

| Agent | Model | Input Tokens | Output Tokens | Total Tokens | Calls |
| ----- | ----- | ------------ | ------------- | ------------ | ----- |
| discrepancy_resolver | gpt-4o-mini | 4,846 | 307 | 5,153 | 3 |
| llm_extractor | gpt-4o-mini | 517,081 | 39,917 | 556,998 | 38 |
| report_ingestion | gpt-4o-mini | 1,576 | 15 | 1,591 | 1 |
| section_parser | gpt-4o-mini | 1,314 | 147 | 1,461 | 1 |

## Taxonomy Alias Mismatches

KPIs where the LLM found the value under a term **not** listed in the taxonomy aliases.
Update `data/kpi/taxonomy_map.json` to add these aliases so future runs can use
cheaper deterministic matching.

| KPI ID | KPI Name | Known Aliases | Term Found in Document |
| ------ | -------- | ------------- | ---------------------- |
| KPI_003 | Net Sales | Net Sales | **Total revenue from operations** |
| KPI_004 | Gross Profit | Gross Profit | **Cost of material consumed** |
| KPI_006 | Operating Income | Operating Income | **Total revenue from operations** |
| KPI_008 | EBIT | EBIT | **Profit before income tax expense** |
| KPI_012 | Net Profit | Net Profit | **Net profit for the year** |
| KPI_014 | Profit Before Tax | Profit Before Tax | **Profit before income tax expense** |
| KPI_015 | Basic EPS | Basic EPS | **(a) Basic earnings per share (in INR)** |
| KPI_016 | Diluted EPS | Diluted EPS | **(b) Diluted earnings per share (in INR)** |
| KPI_017 | Operating Cash Flow | Operating Cash Flow | **Net cash flow generated from operating activities - Total (A)** |
| KPI_018 | Free Cash Flow | Free Cash Flow | **Net cash (used) in investment activities - Total (B)** |
| KPI_021 | Total Assets | Total Assets | **Total** |
| KPI_023 | Shareholders Equity | Shareholders Equity | **Net assets i.e. total assets minus total liabilities** |
| KPI_024 | Current Assets | Current Assets | **Total current assets** |
| KPI_025 | Current Liabilities | Current Liabilities | **- Current** |
| KPI_029 | Interest Coverage Ratio | Interest Coverage Ratio | **Interest on loans at amortised cost** |
| KPI_031 | Return on Equity | Return on Equity | **(b) Diluted earnings per share (in INR)** |
| KPI_034 | Inventory | Inventory | **Changes in inventories (Net)** |
| KPI_035 | Inventory Turnover | Inventory Turnover | **Cost of material consumed** |
| KPI_038 | Receivables Turnover | Receivables Turnover | **Power and fuel** |
| KPI_041 | Payables Turnover | Payables Turnover | **Total Trade payables** |
| KPI_042 | Days Payables Outstanding | Days Payables Outstanding | **Total outstanding dues of creditors other than micro enterprises and small enterprises** |
| KPI_045 | Book Value Per Share | Book Value Per Share | **Total current borrowings** |
| KPI_046 | Dividend Per Share | Dividend Per Share | **Final dividend of INR 1.60 per each 62,17,20,975 equity share** |
| KPI_048 | Net Debt | Net Debt | **Net debts** |
| KPI_089 | Employee Headcount | Employee Headcount | **number of permanent employees on the rolls of the Company** |
