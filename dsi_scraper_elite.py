"""
DSI Elite ICP Job Collector
============================
Production-grade GitHub Actions data collector for DS Innovators (DSI).
Collects strictly verified remote-worldwide engineering jobs from companies
with 10-200 employees. Quality beats volume.
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlparse, urljoin

import feedparser
import pandas as pd
import requests
import tldextract
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from rapidfuzz import fuzz
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

# ─── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("dsi_scraper")

# ─── Constants ──────────────────────────────────────────────────────────────

MAX_DAYS_OLD = 21
MIN_SCORE = 85
SOURCES_FILE = os.path.join(os.path.dirname(__file__), "sources.yml")
TODAY = datetime.now(timezone.utc)
RUN_DATE_STR = TODAY.strftime("%Y_%m_%d")
OUTPUT_FILENAME = f"FINAL_USE_THIS_ONLY_{RUN_DATE_STR}.csv"

FINAL_COLUMNS = [
    "quality_tier",
    "company_name",
    "company_domain",
    "company_website",
    "company_headcount_bucket",
    "company_hq_country",
    "target_market_fit",
    "job_title",
    "role_family",
    "seniority",
    "location_raw",
    "remote_proof",
    "restriction_check",
    "job_url",
    "final_canonical_url",
    "posted_date",
    "days_old",
    "source_name",
    "source_family",
    "source_type",
    "source_trust_score",
    "tech_stack_detected",
    "dsi_icp_score",
    "score_reasons",
    "duplicate_key",
    "collected_date",
]

# ─── Accept/Reject Lists ────────────────────────────────────────────────────

ACCEPTED_HEADCOUNT_BUCKETS = {"10 to 50", "51 to 100", "101 to 200"}

ACCEPTED_ROLE_PATTERNS = [
    r"\bbackend\b", r"\bfront.?end\b", r"\bfull.?stack\b",
    r"\bsoftware\s+engineer\b", r"\bsoftware\s+developer\b",
    r"\bmobile\s+engineer\b", r"\bandroid\s+engineer\b", r"\bios\s+engineer\b",
    r"\breact\s+developer\b", r"\bnode\.?js\s+developer\b",
    r"\bpython\s+developer\b", r"\bjava\s+developer\b",
    r"\bphp\s+developer\b", r"\bruby\s+developer\b", r"\bgolang\s+developer\b",
    r"\bdevops\s+engineer\b", r"\bcloud\s+engineer\b",
    r"\bplatform\s+engineer\b", r"\bsite\s+reliability\b", r"\b\bsre\b",
    r"\bqa\s+automation\b", r"\bsdet\b",
    r"\bdata\s+engineer\b", r"\bai\s+engineer\b",
    r"\bmachine\s+learning\s+engineer\b", r"\bml\s+engineer\b",
    r"\bsecurity\s+engineer\b",
    r"\btechnical\s+lead\b",
]

REJECTED_ROLE_PATTERNS = [
    r"\bcustomer\s+support\b", r"\btechnical\s+support\b",
    r"\bsolutions\s+engineer\b", r"\bsales\s+engineer\b",
    r"\bpre.?sales\b", r"\bsales\b",
    r"\bengineering\s+manager\b", r"\bproduct\s+manager\b",
    r"\bproject\s+manager\b", r"\bscrum\s+master\b",
    r"\bbusiness\s+analyst\b", r"\bdata\s+analyst\b",
    r"\bux\s+designer\b", r"\bui\s+designer\b", r"\bgraphic\s+designer\b",
    r"\brecruiter\b", r"\btalent\s+acquisition\b",
    r"\b(intern|internship|student|trainee|apprentice)\b",
    r"\bbusiness\s+developer\b", r"\bmarketing\b",
    r"\boperations\b", r"\bfinance\b", r"\blegal\b",
    r"\bhelpdesk\b", r"\bit\s+support\b", r"\btechnical\s+writer\b",
]

STRONG_GLOBAL_SIGNALS = [
    "worldwide", "remote worldwide", "work from anywhere",
    "anywhere in the world", "remote anywhere", "global remote",
    "open globally", "open to candidates worldwide",
    "no location restriction", "location independent",
    "globally distributed", "fully distributed team",
    "hire from anywhere", "all countries", "any country",
    "wherever you are", "work remotely from anywhere",
    "anywhere",  # standalone "anywhere" is global
]

# Regex-based hard rejects: country/region names as location context
HARD_REJECT_LOCATION_REGEXES = [
    # "Remote, Germany" pattern — remote + any country/region name
    r"remote,\s*(germany|france|spain|uk|united kingdom|united states|us|usa|canada|"
    r"australia|new zealand|india|poland|portugal|romania|serbia|ukraine|ireland|"
    r"netherlands|sweden|norway|denmark|finland|switzerland|singapore|uae)",
    # Standalone region codes as location (EMEA, APAC etc)
    r"^(emea|apac|latam|north america|south america|europe|eu)$",
    # "Remote in <country/region>"
    r"remote\s+in\s+(us|usa|united states|canada|uk|united kingdom|europe|eu|emea|apac|latam|"
    r"germany|france|spain|poland|portugal|romania|serbia|ukraine|north america)",
    # "Remote <country/region>"
    r"remote\s+(north america|us|usa|united states|canada|uk|europe|emea|apac|latam)",
    # "<country> only" patterns
    r"(united states|us|usa|canada|uk|europe|eu|emea|apac|latam|"
    r"north america|australia|new zealand|india)\s+only",
    # Location field = country name alone (bare country as location = restricted)
    r"^(united states|canada|uk|united kingdom|germany|france|spain|"
    r"australia|new zealand|india|singapore|netherlands|sweden|norway|"
    r"denmark|finland|switzerland|uae|ireland)$",
]

HARD_REJECT_LOCATION_SIGNALS = [
    "must be based in", "must live in", "must reside in",
    "must be located in", "applicants must be based in",
    "applicants must reside in",
    "work authorization required", "must be authorized to work",
    "legally authorized to work", "right to work in",
    "eligible to work in",
    "no visa sponsorship", "visa sponsorship not available",
    "cannot sponsor", "unable to sponsor",
    "citizen only", "permanent resident only",
    "hybrid", "onsite", "office required", "must commute",
]

AGENCY_SIGNALS = [
    "staffing", "recruiting agency", "talent agency",
    "outsourcing", "staff augmentation company",
    "we connect", "we match", "placement agency",
    "staffing agency", "recruitment agency",
]

REJECTED_COMPANY_TYPES = [
    "recruitment agency", "staffing agency", "talent agency",
    "outsourcing", "consulting", "confidential", "anonymous",
]

SENIORITY_NOISE = re.compile(
    r"\b(senior|sr\.?|lead|principal|staff|junior|jr\.?|mid|mid-level|"
    r"remote|worldwide|global|contract|full[- ]time|part[- ]time)\b",
    re.IGNORECASE,
)

COMPANY_NOISE = re.compile(
    r"\b(inc\.?|llc\.?|ltd\.?|limited|gmbh|b\.?v\.?|pty\.?|co\.?|company|corp\.?|corporation)\b",
    re.IGNORECASE,
)

ROLE_FAMILIES = {
    "backend": [r"\bbackend\b", r"\bnode\.?js\b", r"\bpython\s+dev", r"\bjava\s+dev", r"\bruby\s+dev", r"\bphp\s+dev", r"\bgolang\b"],
    "frontend": [r"\bfront.?end\b", r"\breact\s+dev", r"\bvue\b", r"\bangular\b", r"\bjavascript\s+dev"],
    "fullstack": [r"\bfull.?stack\b"],
    "mobile": [r"\bmobile\s+eng", r"\bandroid\b", r"\bios\s+eng", r"\bswift\b", r"\bkotlin\s+eng"],
    "devops": [r"\bdevops\b", r"\bdevsecops\b"],
    "cloud": [r"\bcloud\s+eng"],
    "sre": [r"\bsite\s+reliability\b", r"\bsre\b"],
    "qa_automation": [r"\bqa\s+automation\b", r"\bsdet\b", r"\btest\s+eng"],
    "data_engineering": [r"\bdata\s+eng"],
    "ai_ml": [r"\bai\s+eng", r"\bmachine\s+learning\b", r"\bml\s+eng", r"\bllm\b"],
    "platform": [r"\bplatform\s+eng"],
    "security_engineering": [r"\bsecurity\s+eng"],
    "software_engineering": [r"\bsoftware\s+eng", r"\bsoftware\s+dev"],
}

TECH_PATTERNS = re.compile(
    r"\b(python|golang|go|rust|java|kotlin|swift|typescript|javascript|"
    r"node\.?js|react|vue|angular|next\.?js|django|fastapi|rails|laravel|"
    r"aws|gcp|azure|kubernetes|k8s|docker|terraform|postgres|mysql|redis|"
    r"kafka|spark|airflow|pytorch|tensorflow|llm|rag|vector|embedding)\b",
    re.IGNORECASE,
)

# ─── Data Class ─────────────────────────────────────────────────────────────

@dataclass
class JobRecord:
    quality_tier: str = ""
    company_name: str = ""
    company_domain: str = ""
    company_website: str = ""
    company_headcount_bucket: str = ""
    company_hq_country: str = ""
    target_market_fit: str = ""
    job_title: str = ""
    role_family: str = ""
    seniority: str = ""
    location_raw: str = ""
    remote_proof: str = ""
    restriction_check: str = ""
    job_url: str = ""
    final_canonical_url: str = ""
    posted_date: str = ""
    days_old: int = -1
    source_name: str = ""
    source_family: str = ""
    source_type: str = ""
    source_trust_score: int = 0
    tech_stack_detected: str = ""
    dsi_icp_score: int = 0
    score_reasons: str = ""
    duplicate_key: str = ""
    collected_date: str = TODAY.strftime("%Y-%m-%d")
    # internal only
    _full_text: str = field(default="", repr=False)
    _reject_reason: str = field(default="", repr=False)

# ─── HTTP Client ─────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; DSI-Job-Collector/1.0; "
        "+https://www.dsinnovators.com/)"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

_domain_last_hit: dict[str, float] = {}
_MIN_GAP = 1.5  # seconds between requests to same domain


def _rate_limit(url: str) -> None:
    domain = urlparse(url).netloc
    last = _domain_last_hit.get(domain, 0.0)
    wait = _MIN_GAP + random.uniform(0.3, 0.8)
    elapsed = time.time() - last
    if elapsed < wait:
        time.sleep(wait - elapsed)
    _domain_last_hit[domain] = time.time()


@retry(
    retry=retry_if_exception_type(requests.RequestException),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=False,
)
def safe_get(url: str, timeout: int = 15, **kwargs) -> Optional[requests.Response]:
    _rate_limit(url)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, **kwargs)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 10))
            log.warning("429 from %s, sleeping %ds", url, retry_after)
            time.sleep(min(retry_after, 30))
            raise requests.RequestException("429 rate limit")
        if resp.status_code in (403, 404):
            log.info("HTTP %d from %s, skipping source", resp.status_code, url)
            return None
        resp.raise_for_status()
        return resp
    except requests.RequestException as e:
        log.warning("Request failed for %s: %s", url, e)
        raise


# ─── Source Loader ───────────────────────────────────────────────────────────

def load_sources() -> list[dict]:
    with open(SOURCES_FILE, "r") as f:
        data = yaml.safe_load(f)
    sources = [s for s in data.get("sources", []) if s.get("enabled", True)]
    log.info("Loaded %d enabled sources", len(sources))
    return sources


def build_company_registry(sources: list[dict]) -> dict[str, dict]:
    """Build domain -> source lookup for headcount verification."""
    registry = {}
    for s in sources:
        domain = s.get("company_domain")
        if domain and s.get("headcount_bucket") in ACCEPTED_HEADCOUNT_BUCKETS:
            registry[domain.lower()] = s
        name = s.get("company_name")
        if name:
            registry[COMPANY_NOISE.sub("", name).strip().lower()] = s
    return registry


# ─── Classifiers ────────────────────────────────────────────────────────────

def normalize_title(title: str) -> str:
    return SENIORITY_NOISE.sub("", title).strip().lower()


def classify_role_family(title: str) -> str:
    t = title.lower()
    for family, patterns in ROLE_FAMILIES.items():
        for p in patterns:
            if re.search(p, t):
                return family
    return ""


def is_accepted_role(title: str) -> bool:
    t = title.lower()
    for p in REJECTED_ROLE_PATTERNS:
        if re.search(p, t):
            return False
    for p in ACCEPTED_ROLE_PATTERNS:
        if re.search(p, t):
            return True
    return False


def detect_seniority(title: str) -> str:
    t = title.lower()
    if re.search(r"\b(principal|staff|distinguished)\b", t):
        return "principal"
    if re.search(r"\b(senior|sr\.?|lead)\b", t):
        return "senior"
    if re.search(r"\b(junior|jr\.?)\b", t):
        return "junior"
    if re.search(r"\b(mid|mid-level)\b", t):
        return "mid"
    return "mid"


def classify_remote_proof(text: str) -> tuple[str, bool]:
    """
    Returns (proof_label, is_strong_global).
    Strong global = explicitly global, not just 'remote'.
    """
    t = text.lower()
    for signal in STRONG_GLOBAL_SIGNALS:
        if signal in t:
            return signal, True
    return "none", False


def has_hard_reject(text: str) -> tuple[bool, str]:
    """Returns (should_reject, reason)."""
    t = text.lower().strip()
    for signal in HARD_REJECT_LOCATION_SIGNALS:
        if signal in t:
            return True, f"hard_reject:{signal}"
    for pattern in HARD_REJECT_LOCATION_REGEXES:
        if re.search(pattern, t, re.IGNORECASE):
            return True, f"hard_reject_regex:{pattern[:40]}"
    for signal in AGENCY_SIGNALS:
        if signal in t:
            return True, f"agency:{signal}"
    return False, ""


def extract_domain(url: str) -> str:
    if not url:
        return ""
    ext = tldextract.extract(url)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}"
    return ""


def extract_tech_stack(text: str) -> str:
    found = set(TECH_PATTERNS.findall(text.lower()))
    return ", ".join(sorted(found)) if found else ""


def parse_posted_date(raw: str) -> tuple[Optional[datetime], int]:
    if not raw:
        return None, -1
    try:
        dt = dateparser.parse(raw)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt:
            days = (TODAY - dt).days
            return dt, days
    except Exception:
        pass
    return None, -1


def is_company_agency(company_name: str, description: str = "") -> bool:
    combined = (company_name + " " + description).lower()
    for signal in AGENCY_SIGNALS:
        if signal in combined:
            return True
    for t in REJECTED_COMPANY_TYPES:
        if t in combined:
            return True
    return False


# ─── Headcount Verifier ──────────────────────────────────────────────────────

def verify_headcount(
    job: JobRecord,
    company_registry: dict[str, dict],
    source_meta: dict,
) -> str:
    # If source already has known headcount
    if source_meta.get("headcount_bucket") in ACCEPTED_HEADCOUNT_BUCKETS:
        return source_meta["headcount_bucket"]

    # Try company registry by domain
    if job.company_domain:
        match = company_registry.get(job.company_domain.lower())
        if match and match.get("headcount_bucket") in ACCEPTED_HEADCOUNT_BUCKETS:
            return match["headcount_bucket"]

    # Try by normalized company name
    norm_name = COMPANY_NOISE.sub("", job.company_name).strip().lower()
    match = company_registry.get(norm_name)
    if match and match.get("headcount_bucket") in ACCEPTED_HEADCOUNT_BUCKETS:
        return match["headcount_bucket"]

    return ""  # Unknown = reject


# ─── Scoring ────────────────────────────────────────────────────────────────

def score_job(job: JobRecord, source_meta: dict) -> tuple[int, list[str]]:
    score = 0
    reasons = []

    _, is_global = classify_remote_proof(job._full_text + " " + job.location_raw)
    if is_global:
        score += 30
        reasons.append("proven_remote_worldwide+30")

    reject, _ = has_hard_reject(job._full_text)
    if not reject:
        score += 15
        reasons.append("no_restriction+15")

    text_lower = (job._full_text + " " + job.location_raw).lower()
    if any(w in text_lower for w in ["async", "asynchronous", "timezone flexible", "overlap"]):
        score += 5
        reasons.append("async_friendly+5")

    if job.company_headcount_bucket in ACCEPTED_HEADCOUNT_BUCKETS:
        score += 20
        reasons.append("headcount_10_200+20")

    fit = source_meta.get("target_market_fit", "")
    if fit == "high":
        score += 10
        reasons.append("target_market_high+10")
    elif fit == "medium":
        score += 5
        reasons.append("target_market_medium+5")

    if job.role_family:
        score += 15
        reasons.append("core_dsi_role+15")

    if 0 <= job.days_old <= 7:
        score += 10
        reasons.append("fresh_7d+10")
    elif 8 <= job.days_old <= 14:
        score += 7
        reasons.append("fresh_14d+7")
    elif 15 <= job.days_old <= 21:
        score += 5
        reasons.append("fresh_21d+5")

    stype = source_meta.get("source_type", "")
    if stype in ("ats_greenhouse", "ats_lever", "ats_ashby"):
        score += 10
        reasons.append("official_ats+10")
    elif source_meta.get("trust_score", 0) >= 8:
        score += 6
        reasons.append("trusted_board+6")

    company_type = source_meta.get("source_family", "")
    if any(x in source_meta.get("notes", "").lower() for x in ["saas", "open source", "platform", "product"]):
        score += 5
        reasons.append("product_saas+5")

    return score, reasons


# ─── Deduplication ──────────────────────────────────────────────────────────

def make_duplicate_key(job: JobRecord) -> str:
    norm_title = normalize_title(job.job_title)
    family = job.role_family or "unknown"
    domain = job.company_domain or job.company_name.lower()
    url_key = job.final_canonical_url or job.job_url
    if url_key:
        return hashlib.md5(url_key.encode()).hexdigest()
    combined = f"{domain}|{family}|{norm_title}"
    return hashlib.md5(combined.encode()).hexdigest()


def deduplicate(records: list[JobRecord]) -> list[JobRecord]:
    seen_keys: set[str] = set()
    seen_urls: set[str] = set()
    out = []
    for r in records:
        key = r.duplicate_key
        url = r.final_canonical_url or r.job_url
        if key in seen_keys or (url and url in seen_urls):
            continue

        # Fuzzy title dedup within same company
        is_dupe = False
        for existing in out:
            if existing.company_domain == r.company_domain:
                sim = fuzz.ratio(normalize_title(r.job_title), normalize_title(existing.job_title))
                if sim > 92:
                    is_dupe = True
                    break
        if is_dupe:
            continue

        seen_keys.add(key)
        if url:
            seen_urls.add(url)
        out.append(r)
    return out


# ─── Hard Reject Filter ──────────────────────────────────────────────────────

def hard_reject_overrides(job: JobRecord) -> tuple[bool, str]:
    reject, reason = has_hard_reject(job._full_text + " " + job.location_raw)
    if reject:
        return True, reason
    if not job.company_headcount_bucket:
        return True, "unknown_headcount"
    if job.company_headcount_bucket not in ACCEPTED_HEADCOUNT_BUCKETS:
        return True, f"headcount_out_of_range:{job.company_headcount_bucket}"
    if not job.role_family:
        return True, "non_engineering_role"
    if job.days_old < 0:
        return True, "unknown_posted_date"
    if job.days_old > MAX_DAYS_OLD:
        return True, f"stale:{job.days_old}d"
    if not job.job_url:
        return True, "missing_url"
    _, is_global = classify_remote_proof(job._full_text + " " + job.location_raw)
    if not is_global:
        return True, "weak_remote_only_no_global_proof"
    return False, ""


# ─── Canonical URL ───────────────────────────────────────────────────────────

def resolve_canonical_url(url: str) -> str:
    """Try to follow redirects to get final URL. Skip on failure."""
    if not url:
        return url
    try:
        _rate_limit(url)
        r = requests.head(url, headers=HEADERS, timeout=8, allow_redirects=True)
        return r.url
    except Exception:
        return url


# ─── Source Fetchers ─────────────────────────────────────────────────────────

def _build_base_job(source_meta: dict) -> JobRecord:
    return JobRecord(
        source_name=source_meta.get("source_name", ""),
        source_family=source_meta.get("source_family", ""),
        source_type=source_meta.get("source_type", ""),
        source_trust_score=source_meta.get("trust_score", 0),
        company_name=source_meta.get("company_name", ""),
        company_domain=source_meta.get("company_domain", ""),
        company_website=f"https://{source_meta['company_domain']}" if source_meta.get("company_domain") else "",
        company_hq_country=source_meta.get("hq_country", ""),
        target_market_fit=source_meta.get("target_market_fit", ""),
    )


def fetch_greenhouse(source_meta: dict, company_registry: dict) -> list[JobRecord]:
    url = source_meta.get("api_url", "")
    resp = safe_get(url)
    if not resp:
        return []
    try:
        data = resp.json()
    except Exception:
        return []

    records = []
    jobs = data.get("jobs", [])
    for j in jobs:
        if not j.get("title"):
            continue
        content = j.get("content", "") or ""
        location = " ".join([loc.get("name", "") for loc in j.get("offices", [])])
        if not location:
            location = j.get("location", {}).get("name", "") if isinstance(j.get("location"), dict) else str(j.get("location", ""))

        full_text = f"{j.get('title', '')} {location} {content}"
        job = _build_base_job(source_meta)
        job.job_title = j.get("title", "")
        job.location_raw = location
        job.job_url = j.get("absolute_url", "")
        job.final_canonical_url = job.job_url
        job._full_text = full_text

        # Posted date
        updated = j.get("updated_at") or j.get("published_at") or ""
        dt, days = parse_posted_date(updated)
        job.posted_date = dt.strftime("%Y-%m-%d") if dt else ""
        job.days_old = days

        records.append(job)
    return records


def fetch_lever(source_meta: dict, company_registry: dict) -> list[JobRecord]:
    url = source_meta.get("api_url", "")
    resp = safe_get(url)
    if not resp:
        return []
    try:
        jobs = resp.json()
    except Exception:
        return []

    if not isinstance(jobs, list):
        jobs = jobs.get("postings", []) if isinstance(jobs, dict) else []

    records = []
    for j in jobs:
        if not j.get("text"):
            continue
        location = j.get("categories", {}).get("location", "") or j.get("workplaceType", "")
        description = j.get("descriptionPlain", "") or j.get("description", "") or ""
        full_text = f"{j.get('text', '')} {location} {description}"

        job = _build_base_job(source_meta)
        job.job_title = j.get("text", "")
        job.location_raw = location
        job.job_url = j.get("hostedUrl", "") or f"https://jobs.lever.co/{source_meta.get('company_domain', '')}/{j.get('id', '')}"
        job.final_canonical_url = job.job_url
        job._full_text = full_text

        created = j.get("createdAt")
        if created:
            dt, days = parse_posted_date(datetime.fromtimestamp(created / 1000, tz=timezone.utc).isoformat())
        else:
            dt, days = None, -1
        job.posted_date = dt.strftime("%Y-%m-%d") if dt else ""
        job.days_old = days

        records.append(job)
    return records


def fetch_ashby(source_meta: dict, company_registry: dict) -> list[JobRecord]:
    url = source_meta.get("api_url", "")
    resp = safe_get(url)
    if not resp:
        return []
    try:
        data = resp.json()
    except Exception:
        return []

    jobs = data.get("jobPostings", []) if isinstance(data, dict) else []
    records = []
    for j in jobs:
        title = j.get("title", "")
        if not title:
            continue
        location = j.get("locationName", "") or j.get("workplaceType", "")
        desc = j.get("descriptionHtml", "") or ""
        soup = BeautifulSoup(desc, "html.parser")
        desc_text = soup.get_text(" ", strip=True)
        full_text = f"{title} {location} {desc_text}"

        job = _build_base_job(source_meta)
        job.job_title = title
        job.location_raw = location
        job.job_url = j.get("jobUrl", "") or j.get("applyUrl", "")
        job.final_canonical_url = job.job_url
        job._full_text = full_text

        created = j.get("publishedAt") or j.get("createdAt") or ""
        dt, days = parse_posted_date(created)
        job.posted_date = dt.strftime("%Y-%m-%d") if dt else ""
        job.days_old = days

        records.append(job)
    return records


def fetch_remotive(source_meta: dict, company_registry: dict) -> list[JobRecord]:
    resp = safe_get(source_meta.get("api_url", ""))
    if not resp:
        return []
    try:
        data = resp.json()
    except Exception:
        return []

    jobs = data.get("jobs", [])
    records = []
    for j in jobs:
        title = j.get("title", "")
        company = j.get("company_name", "")
        if not title or not company:
            continue

        desc = j.get("description", "") or ""
        location = j.get("candidate_required_location", "")
        full_text = f"{title} {company} {location} {BeautifulSoup(desc, 'html.parser').get_text()}"

        job = _build_base_job(source_meta)
        job.job_title = title
        job.company_name = company
        job.company_domain = extract_domain(j.get("url", ""))
        job.location_raw = location
        job.job_url = j.get("url", "")
        job.final_canonical_url = job.job_url
        job._full_text = full_text

        dt, days = parse_posted_date(j.get("publication_date", ""))
        job.posted_date = dt.strftime("%Y-%m-%d") if dt else ""
        job.days_old = days

        records.append(job)
    return records


def fetch_remoteok(source_meta: dict, company_registry: dict) -> list[JobRecord]:
    resp = safe_get(source_meta.get("api_url", ""))
    if not resp:
        return []
    try:
        data = resp.json()
    except Exception:
        return []

    if isinstance(data, list) and data and "legal" in str(data[0]).lower():
        data = data[1:]  # skip legal notice dict

    records = []
    for j in data:
        if not isinstance(j, dict):
            continue
        title = j.get("position", "")
        company = j.get("company", "")
        if not title or not company:
            continue

        location = j.get("location", "")
        tags = " ".join(j.get("tags", []))
        desc = j.get("description", "") or ""
        full_text = f"{title} {company} {location} {tags} {desc}"

        job = _build_base_job(source_meta)
        job.job_title = title
        job.company_name = company
        job.company_domain = extract_domain(j.get("url", ""))
        job.location_raw = location
        job.job_url = j.get("url", "")
        job.final_canonical_url = job.job_url
        job._full_text = full_text

        dt, days = parse_posted_date(j.get("date", ""))
        job.posted_date = dt.strftime("%Y-%m-%d") if dt else ""
        job.days_old = days

        records.append(job)
    return records


def fetch_himalayas(source_meta: dict, company_registry: dict) -> list[JobRecord]:
    resp = safe_get(source_meta.get("api_url", ""))
    if not resp:
        return []
    try:
        data = resp.json()
    except Exception:
        return []

    jobs = data.get("jobs", []) if isinstance(data, dict) else data
    records = []
    for j in jobs:
        title = j.get("title", "")
        company = j.get("companyName", "") or j.get("company", {}).get("name", "")
        if not title:
            continue

        location = j.get("locationRestrictions", "") or j.get("location", "")
        desc = j.get("description", "") or ""
        full_text = f"{title} {company} {location} {desc}"

        job = _build_base_job(source_meta)
        job.job_title = title
        job.company_name = company
        job.company_domain = extract_domain(j.get("applicationLink", "") or j.get("url", ""))
        job.location_raw = str(location)
        job.job_url = j.get("url", "") or j.get("applicationLink", "")
        job.final_canonical_url = job.job_url
        job._full_text = full_text

        dt, days = parse_posted_date(j.get("publishedAt", "") or j.get("postedAt", ""))
        job.posted_date = dt.strftime("%Y-%m-%d") if dt else ""
        job.days_old = days

        records.append(job)
    return records


def fetch_jobicy(source_meta: dict, company_registry: dict) -> list[JobRecord]:
    resp = safe_get(source_meta.get("api_url", ""))
    if not resp:
        return []
    try:
        data = resp.json()
    except Exception:
        return []

    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    records = []
    for j in jobs:
        title = j.get("jobTitle", "")
        company = j.get("companyName", "")
        if not title:
            continue

        location = j.get("jobGeo", "")
        desc = j.get("jobDescription", "") or ""
        full_text = f"{title} {company} {location} {desc}"

        job = _build_base_job(source_meta)
        job.job_title = title
        job.company_name = company
        job.company_domain = extract_domain(j.get("url", ""))
        job.location_raw = location
        job.job_url = j.get("url", "")
        job.final_canonical_url = job.job_url
        job._full_text = full_text

        dt, days = parse_posted_date(j.get("pubDate", ""))
        job.posted_date = dt.strftime("%Y-%m-%d") if dt else ""
        job.days_old = days

        records.append(job)
    return records


def fetch_arbeitnow(source_meta: dict, company_registry: dict) -> list[JobRecord]:
    resp = safe_get(source_meta.get("api_url", ""))
    if not resp:
        return []
    try:
        data = resp.json()
    except Exception:
        return []

    jobs = data.get("data", []) if isinstance(data, dict) else []
    records = []
    for j in jobs:
        title = j.get("title", "")
        company = j.get("company_name", "")
        if not title:
            continue

        location = j.get("location", "")
        remote = str(j.get("remote", ""))
        desc = j.get("description", "") or ""
        full_text = f"{title} {company} {location} {remote} {desc}"

        job = _build_base_job(source_meta)
        job.job_title = title
        job.company_name = company
        job.company_domain = extract_domain(j.get("url", ""))
        job.location_raw = location
        job.job_url = j.get("url", "")
        job.final_canonical_url = job.job_url
        job._full_text = full_text

        dt, days = parse_posted_date(j.get("created_at", ""))
        job.posted_date = dt.strftime("%Y-%m-%d") if dt else ""
        job.days_old = days

        records.append(job)
    return records


def fetch_rss(source_meta: dict, company_registry: dict) -> list[JobRecord]:
    feed_url = source_meta.get("feed_url", "")
    try:
        feed = feedparser.parse(feed_url)
    except Exception as e:
        log.warning("RSS parse failed for %s: %s", feed_url, e)
        return []

    records = []
    for entry in feed.entries:
        title = entry.get("title", "")
        if not title:
            continue

        company = entry.get("author", "") or entry.get("company", "") or ""
        location = entry.get("location", "") or entry.get("category", "") or ""
        summary = entry.get("summary", "") or ""
        content_parts = [c.value for c in entry.get("content", [])] if entry.get("content") else []
        full_text = f"{title} {company} {location} {summary} {' '.join(content_parts)}"

        url = entry.get("link", "")

        job = _build_base_job(source_meta)
        job.job_title = title
        job.company_name = company
        job.company_domain = extract_domain(url)
        job.location_raw = location
        job.job_url = url
        job.final_canonical_url = url
        job._full_text = BeautifulSoup(full_text, "html.parser").get_text(" ")

        published = entry.get("published", "") or entry.get("updated", "")
        dt, days = parse_posted_date(published)
        job.posted_date = dt.strftime("%Y-%m-%d") if dt else ""
        job.days_old = days

        records.append(job)
    return records


FETCHER_MAP = {
    "ats_greenhouse": fetch_greenhouse,
    "ats_lever": fetch_lever,
    "ats_ashby": fetch_ashby,
    "api": None,  # dispatched below per source_family
    "rss": fetch_rss,
}

API_FAMILY_MAP = {
    "remotive": fetch_remotive,
    "remoteok": fetch_remoteok,
    "himalayas": fetch_himalayas,
    "jobicy": fetch_jobicy,
    "arbeitnow": fetch_arbeitnow,
}


def fetch_source(source_meta: dict, company_registry: dict) -> list[JobRecord]:
    stype = source_meta.get("source_type", "")
    sfam = source_meta.get("source_family", "")

    fetcher = FETCHER_MAP.get(stype)
    if fetcher is None and stype == "api":
        fetcher = API_FAMILY_MAP.get(sfam)

    if fetcher is None:
        log.warning("No fetcher for source_type=%s source_family=%s", stype, sfam)
        return []

    try:
        return fetcher(source_meta, company_registry)
    except Exception as e:
        log.warning("Fetcher failed for %s: %s", source_meta.get("source_name"), e)
        return []


# ─── Pipeline ────────────────────────────────────────────────────────────────

def process_job(
    job: JobRecord,
    source_meta: dict,
    company_registry: dict,
) -> Optional[JobRecord]:
    """Apply all filters and enrichment. Returns None if rejected."""

    # 1. Role check
    if not is_accepted_role(job.job_title):
        return None

    # 2. Agency check
    if is_company_agency(job.company_name, job._full_text):
        return None

    # 3. Headcount
    hc = verify_headcount(job, company_registry, source_meta)
    if not hc:
        return None
    job.company_headcount_bucket = hc

    # 4. Role family
    job.role_family = classify_role_family(job.job_title)
    if not job.role_family:
        return None

    # 5. Remote proof
    proof, is_global = classify_remote_proof(job._full_text + " " + job.location_raw)
    job.remote_proof = proof
    if not is_global:
        return None

    # 6. Hard rejects
    reject, reason = has_hard_reject(job._full_text + " " + job.location_raw)
    if reject:
        return None
    job.restriction_check = "passed"

    # 7. Freshness
    if job.days_old < 0 or job.days_old > MAX_DAYS_OLD:
        return None

    # 8. URL required
    if not job.job_url:
        return None

    # 9. Enrich
    job.seniority = detect_seniority(job.job_title)
    job.tech_stack_detected = extract_tech_stack(job._full_text)
    if not job.company_domain and job.job_url:
        job.company_domain = extract_domain(job.job_url)
    if not job.company_website and job.company_domain:
        job.company_website = f"https://{job.company_domain}"
    if not job.target_market_fit:
        job.target_market_fit = source_meta.get("target_market_fit", "")

    # 10. Score
    score, reasons = score_job(job, source_meta)
    job.dsi_icp_score = score
    job.score_reasons = " | ".join(reasons)

    if score < MIN_SCORE:
        return None

    # 11. Dedup key
    job.duplicate_key = make_duplicate_key(job)
    job.quality_tier = "A_STRICT_DSI_ICP"
    return job


def run_pipeline() -> list[JobRecord]:
    sources = load_sources()
    company_registry = build_company_registry(sources)
    all_candidates: list[JobRecord] = []

    for source_meta in sources:
        name = source_meta.get("source_name", "?")
        log.info("Fetching: %s", name)
        raw_jobs = fetch_source(source_meta, company_registry)
        log.info("  Raw: %d jobs from %s", len(raw_jobs), name)

        for job in raw_jobs:
            result = process_job(job, source_meta, company_registry)
            if result:
                all_candidates.append(result)

        time.sleep(random.uniform(0.5, 1.2))

    log.info("Pre-dedup candidates: %d", len(all_candidates))
    final = deduplicate(all_candidates)
    log.info("Post-dedup final: %d", len(final))
    return final


# ─── Output ──────────────────────────────────────────────────────────────────

def write_output(records: list[JobRecord]) -> str:
    rows = []
    for r in records:
        rows.append({col: getattr(r, col, "") for col in FINAL_COLUMNS})

    df = pd.DataFrame(rows, columns=FINAL_COLUMNS)
    output_path = OUTPUT_FILENAME

    if len(df) == 0:
        df = pd.DataFrame(columns=FINAL_COLUMNS)
        df.to_csv(output_path, index=False)
        print("\n⚠️  No strict ICP rows found. Do not use weak data. Add more verified company sources.\n")
    else:
        df.to_csv(output_path, index=False)
        msg = f"\n✅ Strict quality found {len(df)} rows."
        if len(df) < 500:
            msg += " To scale, add more verified 10 to 200 headcount remote-first companies to sources.yml."
        print(msg + "\n")

    log.info("Output written: %s (%d rows)", output_path, len(df))
    return output_path


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    log.info("DSI Elite ICP Collector starting. Run date: %s", RUN_DATE_STR)
    records = run_pipeline()
    output_path = write_output(records)
    log.info("Done. Artifact: %s", output_path)


if __name__ == "__main__":
    main()
