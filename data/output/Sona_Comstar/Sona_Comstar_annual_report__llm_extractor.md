# LLM Extractor — Full-Section Context Extraction (Final Results)

## Summary

| Metric | Count |
|---|---|
| Found | 41 |
| Needs Human Review | 3 |
| Not Found | 57 |
| Total KPIs | 101 |

## Extraction Results

| KPI ID | KPI Name | Status | Method | Value | Section | Page | Confidence | Review Reason |
|---|---|---|---|---|---|---|---|---|
| KPI_001 | Revenue | ✓ found | deterministic | 1 | Management Discussion and Analysis | 164 | 1.00 | — |
| KPI_002 | Revenue Growth | ✓ found | llm | 25.99% | Management Discussion and Analysis | — | 0.90 | — |
| KPI_003 | Net Sales | ✓ found | llm | 41,236.74 | Management Discussion and Analysis | — | 0.90 | — |
| KPI_004 | Gross Profit | ✓ found | llm | 19,779.88 | Management Discussion and Analysis | — | 0.90 | — |
| KPI_005 | Gross Margin | ✓ found | llm | 48.00% | Management Discussion and Analysis | — | 0.90 | — |
| KPI_006 | Operating Income | ✓ found | llm | 44,494.60 | Management Discussion and Analysis | 0 | 1.00 | — |
| KPI_007 | Operating Margin | ✓ found | llm | 10.00% | Management Discussion and Analysis | 0 | 1.00 | — |
| KPI_008 | EBIT | ✓ found | llm | 8,416.18 | Management Discussion and Analysis | 0 | 1.00 | — |
| KPI_009 | EBIT Margin | ✓ found | llm | 18.92% | Management Discussion and Analysis | 0 | 1.00 | — |
| KPI_010 | EBITDA | ✓ found | llm | 10,000.00 | Management Discussion and Analysis | 0 | 1.00 | — |
| KPI_011 | EBITDA Margin | ✓ found | llm | 22.50% | Management Discussion and Analysis | 0 | 1.00 | — |
| KPI_012 | Net Profit | ✓ found | llm | 6,464.15 | Management Discussion and Analysis | — | 0.90 | — |
| KPI_013 | Net Profit Margin | ✓ found | llm | 15.67% | Management Discussion and Analysis | — | 0.90 | — |
| KPI_014 | Profit Before Tax | ✓ found | llm | 8,572.52 | Management Discussion and Analysis | — | 0.90 | — |
| KPI_015 | Basic EPS | ✓ found | llm | 10.40 | OTHER | 75 | 1.00 | — |
| KPI_016 | Diluted EPS | ✓ found | llm | 10.40 | OTHER | 75 | 1.00 | — |
| KPI_017 | Operating Cash Flow | ✓ found | llm | 6,145.89 | OTHER | 78 | 1.00 | — |
| KPI_018 | Free Cash Flow | ✓ found | llm | (15,712.19) | OTHER | 151 | 1.00 | — |
| KPI_019 | Capital Expenditure | ✓ found | llm | 968.64 | Notes to Financial Statements | 123 | 1.00 | — |
| KPI_020 | Cash Conversion Ratio | — not_found | — | — | — | — | — | — |
| KPI_021 | Total Assets | ✓ found | llm | 69,764.41 | OTHER | 70 | 1.00 | — |
| KPI_022 | Total Liabilities | ✓ found | llm | 11,349.42 | OTHER | 70 | 1.00 | — |
| KPI_023 | Shareholders Equity | ✓ found | llm | 58,414.99 | OTHER | 70 | 1.00 | — |
| KPI_024 | Current Assets | ✓ found | llm | 28,859.38 | OTHER | 70 | 1.00 | — |
| KPI_025 | Current Liabilities | ✓ found | llm | 8,446.72 | OTHER | 70 | 1.00 | — |
| KPI_026 | Current Ratio | ✓ found | llm | 3.41 | OTHER | 70 | 1.00 | — |
| KPI_027 | Quick Ratio | — not_found | — | — | — | — | — | — |
| KPI_028 | Debt to Equity Ratio | ✓ found | deterministic | , | Management Discussion and Analysis | 126 | 1.00 | — |
| KPI_029 | Interest Coverage Ratio | ✓ found | llm | 11.91 | Management Discussion and Analysis | — | 0.90 | — |
| KPI_030 | Return on Assets | — not_found | — | — | — | — | — | — |
| KPI_031 | Return on Equity | ✓ found | llm | 11.00% | OTHER | 70 | 1.00 | — |
| KPI_032 | Return on Capital Employed | ✓ found | llm | 18.04% | OTHER | 140 | 1.00 | — |
| KPI_033 | Asset Turnover | — not_found | — | — | — | — | — | — |
| KPI_034 | Inventory | ✓ found | llm | 2,684.07 | Management Discussion and Analysis | — | 0.90 | — |
| KPI_035 | Inventory Turnover | ✓ found | llm | 21,419.57 | Management Discussion and Analysis | 0 | 1.00 | — |
| KPI_036 | Days Inventory Outstanding | — not_found | — | — | — | — | — | — |
| KPI_037 | Trade Receivables | ⚠ needs_human_review | llm | 10,760.33 | Management Discussion and Analysis | 126 | 0.90 | section_discrepancy |
| KPI_038 | Receivables Turnover | ✓ found | llm | 1,024.18 | Management Discussion and Analysis | — | 0.90 | — |
| KPI_039 | Days Sales Outstanding | — not_found | — | — | — | — | — | — |
| KPI_040 | Trade Payables | ⚠ needs_human_review | llm | 4,143.72 | Management Discussion and Analysis | 126 | 0.70 | section_discrepancy |
| KPI_041 | Payables Turnover | ✓ found | llm | 4,143.72 | Management Discussion and Analysis | — | 0.90 | — |
| KPI_042 | Days Payables Outstanding | ✓ found | llm | 62.32 | OTHER | 70 | 1.00 | — |
| KPI_043 | Working Capital | ✓ found | llm | 20,412.88 | OTHER | 70 | 1.00 | — |
| KPI_044 | Working Capital Turnover | — not_found | — | — | — | — | — | — |
| KPI_045 | Book Value Per Share | ✓ found | llm | 93.88 | Management Discussion and Analysis | — | 0.90 | — |
| KPI_046 | Dividend Per Share | ✓ found | llm | 1.60 | Management Discussion and Analysis | — | 0.90 | — |
| KPI_047 | Dividend Payout Ratio | ✓ found | llm | 30.00% | Management Discussion and Analysis | — | 0.90 | — |
| KPI_048 | Net Debt | ✓ found | llm | 1,896.27 | Management Discussion and Analysis | — | 0.90 | — |
| KPI_049 | Net Debt to EBITDA | ✓ found | llm | 0.29 | Management Discussion and Analysis | — | 0.90 | — |
| KPI_050 | Cash and Cash Equivalents | ⚠ needs_human_review | llm | 1,510.82 | Management Discussion and Analysis | 126 | 0.80 | section_discrepancy |
| KPI_051 | Loan Book | — not_found | — | — | — | — | — | — |
| KPI_052 | Net Interest Income | — not_found | — | — | — | — | — | — |
| KPI_053 | Net Interest Margin | — not_found | — | — | — | — | — | — |
| KPI_054 | Cost to Income Ratio | — not_found | — | — | — | — | — | — |
| KPI_055 | Gross NPA Ratio | — not_found | — | — | — | — | — | — |
| KPI_056 | Net NPA Ratio | — not_found | — | — | — | — | — | — |
| KPI_057 | Capital Adequacy Ratio | — not_found | — | — | — | — | — | — |
| KPI_058 | Provision Coverage Ratio | — not_found | — | — | — | — | — | — |
| KPI_059 | CASA Ratio | — not_found | — | — | — | — | — | — |
| KPI_060 | Assets Under Management | — not_found | — | — | — | — | — | — |
| KPI_061 | Gross Written Premium | — not_found | — | — | — | — | — | — |
| KPI_062 | Net Written Premium | — not_found | — | — | — | — | — | — |
| KPI_063 | Combined Ratio | — not_found | — | — | — | — | — | — |
| KPI_064 | Loss Ratio | — not_found | — | — | — | — | — | — |
| KPI_065 | Expense Ratio | — not_found | — | — | — | — | — | — |
| KPI_066 | Solvency Ratio | — not_found | — | — | — | — | — | — |
| KPI_067 | Occupancy Rate | — not_found | — | — | — | — | — | — |
| KPI_068 | Average Daily Rate | — not_found | — | — | — | — | — | — |
| KPI_069 | Revenue per Available Room | — not_found | — | — | — | — | — | — |
| KPI_070 | Same Store Sales Growth | — not_found | — | — | — | — | — | — |
| KPI_071 | Average Basket Size | — not_found | — | — | — | — | — | — |
| KPI_072 | Inventory Sell Through | — not_found | — | — | — | — | — | — |
| KPI_073 | Monthly Recurring Revenue | — not_found | — | — | — | — | — | — |
| KPI_074 | Annual Recurring Revenue | — not_found | — | — | — | — | — | — |
| KPI_075 | Customer Acquisition Cost | — not_found | — | — | — | — | — | — |
| KPI_076 | Customer Lifetime Value | — not_found | — | — | — | — | — | — |
| KPI_077 | Net Revenue Retention | — not_found | — | — | — | — | — | — |
| KPI_078 | Churn Rate | — not_found | — | — | — | — | — | — |
| KPI_079 | Average Revenue Per User | — not_found | — | — | — | — | — | — |
| KPI_080 | Subscriber Count | — not_found | — | — | — | — | — | — |
| KPI_081 | Data Usage Per Subscriber | — not_found | — | — | — | — | — | — |
| KPI_082 | Plant Capacity Utilization | — not_found | — | — | — | — | — | — |
| KPI_083 | Overall Equipment Effectiveness | — not_found | — | — | — | — | — | — |
| KPI_084 | Production Volume | — not_found | — | — | — | — | — | — |
| KPI_085 | Yield | — not_found | — | — | — | — | — | — |
| KPI_086 | Scrap Rate | — not_found | — | — | — | — | — | — |
| KPI_087 | Order Backlog | — not_found | — | — | — | — | — | — |
| KPI_088 | On Time Delivery | — not_found | — | — | — | — | — | — |
| KPI_089 | Employee Headcount | ✓ found | llm | 2008 | Statement of Cash Flows | 69 | 1.00 | — |
| KPI_090 | Employee Attrition Rate | — not_found | — | — | — | — | — | — |
| KPI_091 | Lost Time Injury Frequency Rate | — not_found | — | — | — | — | — | — |
| KPI_092 | Scope 1 Emissions | — not_found | — | — | — | — | — | — |
| KPI_093 | Scope 2 Emissions | — not_found | — | — | — | — | — | — |
| KPI_094 | Water Withdrawal | — not_found | — | — | — | — | — | — |
| KPI_095 | Energy Consumption | — not_found | — | — | — | — | — | — |
| KPI_096 | Waste Recycled | — not_found | — | — | — | — | — | — |
| KPI_097 | Board Independence | — not_found | — | — | — | — | — | — |
| KPI_098 | Female Board Representation | — not_found | — | — | — | — | — | — |
| KPI_099 | Executive Compensation Ratio | — not_found | — | — | — | — | — | — |
| KPI_100 | Carbon Intensity | — not_found | — | — | — | — | — | — |
| KPI_101 | Renewable Energy Usage | — not_found | — | — | — | — | — | — |

## Flagged / Review Details

### Trade Receivables (`KPI_037`)

**Review reason:** section_discrepancy

**Conflicting values:**

| Section | Value | Method | Source |
|---|---|---|---|
| Management Discussion and Analysis | 11,507.13 | deterministic | text |

**Extraction attempts:**

| Tier | Outcome | Confidence | Note |
|---|---|---|---|
| llm | flagged | 0.90 | cross-section discrepancy resolution: The value of 10,760.33 is taken from a formal financial statement section (table) and represents total trade receivables as of March 31, 2026. The other candidate value (11,507.13) is also from a financial statement but is less precise and appears to be a rounded figure. Therefore, the more precise value from the formal financial statement is chosen. |

### Trade Payables (`KPI_040`)

**Review reason:** section_discrepancy

**Conflicting values:**

| Section | Value | Method | Source |
|---|---|---|---|
| Management Discussion and Analysis | 4,622.30 | deterministic | text |

**Extraction attempts:**

| Tier | Outcome | Confidence | Note |
|---|---|---|---|
| llm | flagged | 0.70 | cross-section discrepancy resolution: Both candidate values are from the Management Discussion and Analysis section, which is less authoritative than formal financial statements. However, since they are the only available values and they represent the same fiscal year, I selected the first candidate value (4,143.72) as it is the first reported figure for Trade Payables in the section. |

### Cash and Cash Equivalents (`KPI_050`)

**Review reason:** section_discrepancy

**Conflicting values:**

| Section | Value | Method | Source |
|---|---|---|---|
| Management Discussion and Analysis | 293.73 | deterministic | text |

**Extraction attempts:**

| Tier | Outcome | Confidence | Note |
|---|---|---|---|
| llm | flagged | 0.80 | cross-section discrepancy resolution: The value of 1,510.82 is more likely to represent Cash and Cash Equivalents as it is a larger figure and typically cash balances are higher than smaller components. The section is also from the Management Discussion and Analysis, which is the correct fiscal year target. |
