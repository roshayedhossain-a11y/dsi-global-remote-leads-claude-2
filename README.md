# DSI Elite ICP Job Collector

Production-grade GitHub Actions data collector for **DS Innovators (DSI)**.

Collects **strictly verified** remote-worldwide engineering job posts from companies with **10–200 employees**. Quality beats volume. Every row in the final CSV is a real, fresh, globally-open engineering role from a legitimate software company — prospecting intelligence DSI can actually trust.

---

## What This Is

This is **not** a generic remote job scraper. It is a strict ICP (Ideal Customer Profile) buying-intent data collector. The output is used to find companies that are:

- Actively hiring engineers or developers **remote worldwide** (no country restriction)
- **10–200 employees** (verified)
- B2B SaaS, AI SaaS, dev tools, fintech, healthtech, cloud infra, data infra, or similar
- Potentially a fit for DSI staff resource augmentation from Bangladesh

---

## Output

**One CSV only.** No debug files. No rejected files. No secondary files.

```
FINAL_USE_THIS_ONLY_YYYY_MM_DD.csv
```

Every row in this file is `quality_tier = A_STRICT_DSI_ICP`.

---

## How to Upload to GitHub

```bash
git clone https://github.com/YOUR_ORG/dsi-icp-collector.git
cd dsi-icp-collector

# Copy these files in:
cp dsi_scraper_elite.py sources.yml requirements.txt README.md .

mkdir -p .github/workflows tests
cp .github/workflows/daily_scrape.yml .github/workflows/
cp tests/test_filters.py tests/

git add .
git commit -m "Initial DSI Elite ICP Collector"
git push origin main
```

The GitHub Actions workflow (`daily_scrape.yml`) runs automatically every day at 06:00 UTC.

---

## How to Run Manually

### Prerequisites

```bash
pip install -r requirements.txt
```

### Run the scraper

```bash
python dsi_scraper_elite.py
```

Output will be written to `FINAL_USE_THIS_ONLY_YYYY_MM_DD.csv` in the current directory.

### Run tests

```bash
pip install pytest
pytest tests/test_filters.py -v
```

---

## Where to Download the Final CSV

### From GitHub Actions

1. Go to your repository on GitHub
2. Click **Actions** → **DSI Elite ICP Job Collector**
3. Click on the latest successful run
4. Scroll to **Artifacts** at the bottom
5. Download `dsi-icp-jobs-XXXXXXXX`
6. Unzip → open `FINAL_USE_THIS_ONLY_YYYY_MM_DD.csv`

### From local run

The CSV is written to your current working directory.

---

## How to Add More Sources in `sources.yml`

Each source entry looks like this:

```yaml
- source_id: ashby_mycompany
  source_name: MyCompany Ashby
  source_type: ats_ashby          # ats_greenhouse | ats_lever | ats_ashby | api | rss
  source_family: ashby
  api_url: https://api.ashbyhq.com/posting-api/job-board/mycompany-slug
  company_name: MyCompany
  company_domain: mycompany.com
  headcount_bucket: "10 to 50"   # MUST be one of: "10 to 50" | "51 to 100" | "101 to 200"
  hq_country: United States
  target_market_fit: high        # high | medium | low
  trust_score: 10                # 1-10
  enabled: true
  notes: "What this company does. Remote worldwide."
```

### Source type quick reference

| `source_type`     | `api_url` pattern                                                     |
|-------------------|-----------------------------------------------------------------------|
| `ats_greenhouse`  | `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` |
| `ats_lever`       | `https://api.lever.co/v0/postings/{company}?mode=json`                |
| `ats_ashby`       | `https://api.ashbyhq.com/posting-api/job-board/{slug}`                |
| `api` + remotive  | `https://remotive.com/api/remote-jobs?category=software-dev`          |
| `rss`             | RSS feed URL                                                          |

### Finding ATS slugs

- **Greenhouse**: visit `https://boards.greenhouse.io/{slug}/` → use `{slug}`
- **Lever**: visit `https://jobs.lever.co/{company}/` → use `{company}`
- **Ashby**: visit `https://{company}.ashbyhq.com/` → use the Ashby API slug

---

## How to Add More Verified Company Sources

Only add companies where you can verify the headcount from public sources (LinkedIn, Crunchbase, Pitchbook, their own about page).

1. Look up the company's ATS (Greenhouse / Lever / Ashby / Workable / Smartrecruiters)
2. Find their public job board API endpoint
3. Add an entry to `sources.yml` with the correct `headcount_bucket`
4. Set `enabled: true`
5. Commit and push — the next daily run will include them

**Do not add companies with unknown headcount.** `headcount_bucket: null` is treated as unknown and all jobs from that company-specific source will be rejected from the final CSV.

---

## How to Adjust Target Markets

The target market list is defined in the prompt spec but filtering is applied per-job via:
- `company_hq_country` in `sources.yml` (for company-specific ATS sources)
- `target_market_fit` field (high/medium/low)

To add a new target market:
1. Add company sources from that market to `sources.yml` with `hq_country: "Country Name"`
2. Set `target_market_fit: high`
3. The scoring engine will weight them accordingly

---

## How to Keep Quality Strict

The scraper enforces multiple independent layers of rejection:

| Layer | Rule |
|-------|------|
| Role | Must match accepted engineering role patterns |
| Agency | Company must not be a staffing/recruiting agency |
| Headcount | Must be 10–200, verified from `sources.yml` or source metadata |
| Remote proof | Must have a **strong global signal** (not just "remote" or "fully remote") |
| Hard reject | Any country restriction, work authorization, visa, hybrid, onsite → rejected |
| Freshness | Posted within 21 days. Unknown date → rejected |
| URL | Must have a real job URL |
| Score | Minimum 85/100 |

**Do not lower the `MIN_SCORE` threshold.** It exists to prevent weak data from entering the CSV.

**Do not add company sources with unknown headcount.** If you cannot verify headcount from a public free source, do not add the source.

---

## CSV Columns Reference

| Column | Description |
|--------|-------------|
| `quality_tier` | Always `A_STRICT_DSI_ICP` |
| `company_name` | Company name (cleaned) |
| `company_domain` | Primary domain |
| `company_website` | Full URL |
| `company_headcount_bucket` | `10 to 50` / `51 to 100` / `101 to 200` |
| `company_hq_country` | HQ country |
| `target_market_fit` | high / medium |
| `job_title` | Original job title |
| `role_family` | Normalized role family |
| `seniority` | junior / mid / senior / principal |
| `location_raw` | Raw location string from source |
| `remote_proof` | The exact global signal found |
| `restriction_check` | `passed` |
| `job_url` | Original job URL |
| `final_canonical_url` | Resolved URL |
| `posted_date` | YYYY-MM-DD |
| `days_old` | Days since posting |
| `source_name` | Source name |
| `source_family` | Source family (greenhouse, lever, etc.) |
| `source_type` | ats_greenhouse / ats_lever / ats_ashby / api / rss |
| `source_trust_score` | 1–10 |
| `tech_stack_detected` | Detected technologies |
| `dsi_icp_score` | Score out of 100 (min 85) |
| `score_reasons` | Pipe-separated score breakdown |
| `duplicate_key` | MD5 hash used for deduplication |
| `collected_date` | Date this run was executed |

---

## Architecture

```
dsi_collector/
├── dsi_scraper_elite.py       # Main scraper
├── sources.yml                # Source registry (100+ sources)
├── requirements.txt
├── README.md
├── .github/
│   └── workflows/
│       └── daily_scrape.yml   # GitHub Actions workflow
└── tests/
    └── test_filters.py        # Pytest suite
```

---

## Troubleshooting

**0 rows in output:**
Run is not broken. It means no jobs passed all strict filters today. Add more verified company-specific ATS sources to `sources.yml`.

**Source returns 403:**
The source is bot-protected. It is automatically skipped. Mark it `enabled: false` in `sources.yml` if persistent.

**Source returns 429:**
Rate-limited. The scraper respects `Retry-After` headers and uses exponential backoff. It will skip the source if backoff is too long.

**Tests fail:**
Do not push if tests fail. Fix the filter logic first. The tests define the ground truth of what the collector must accept and reject.

---

*Built for DS Innovators — https://www.dsinnovators.com/*
