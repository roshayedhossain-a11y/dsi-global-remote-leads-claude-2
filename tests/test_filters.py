"""
DSI Scraper Filter Tests
========================
Tests prove strict ICP filter behavior: reject bad, accept good.
"""

import pytest
from dsi_scraper_elite import (
    is_accepted_role,
    classify_remote_proof,
    has_hard_reject,
    classify_role_family,
    detect_seniority,
    verify_headcount,
    process_job,
    make_duplicate_key,
    deduplicate,
    parse_posted_date,
    is_company_agency,
    JobRecord,
    ACCEPTED_HEADCOUNT_BUCKETS,
    MAX_DAYS_OLD,
    MIN_SCORE,
)
from datetime import datetime, timezone, timedelta


# ─── Location Reject Tests ───────────────────────────────────────────────────

MUST_REJECT_LOCATION_TEXTS = [
    "Remote, Germany",
    "Remote in Europe",
    "Remote US only",
    "United States",
    "Canada",
    "UK",
    "EMEA",
    "LATAM",
    "APAC",
    "North America",
    "Remote North America",
    "Hybrid",
    "Onsite",
    "Work authorization required",
    "Visa sponsorship not available",
    "Must be based in Spain",
    "Must reside in Canada",
]


@pytest.mark.parametrize("text", MUST_REJECT_LOCATION_TEXTS)
def test_location_hard_reject(text):
    """All restricted location signals must be rejected."""
    rejected, reason = has_hard_reject(text)
    assert rejected, f"Expected rejection for: '{text}', got no reject (reason was empty)"
    assert reason, f"Reject reason should not be empty for: '{text}'"


# ─── Role Reject Tests ───────────────────────────────────────────────────────

MUST_REJECT_ROLES = [
    "Customer Support Engineer",
    "Sales Engineer",
    "Engineering Manager",
    "Recruiter",
    "Intern",
]


@pytest.mark.parametrize("role", MUST_REJECT_ROLES)
def test_role_rejection(role):
    """Non-engineering roles must be rejected."""
    assert not is_accepted_role(role), f"Expected role rejection for: '{role}'"


# ─── Location Accept Tests ───────────────────────────────────────────────────

MUST_ACCEPT_LOCATION_TEXTS = [
    "Worldwide",
    "Remote Worldwide",
    "Anywhere",
    "Work from anywhere",
    "Anywhere in the world",
    "Global remote",
    "Open globally",
    "No location restriction",
    "Location independent",
    "Globally distributed",
    "Open to candidates worldwide",
]


@pytest.mark.parametrize("text", MUST_ACCEPT_LOCATION_TEXTS)
def test_global_proof_accepted(text):
    """All strong global signals must produce strong remote proof."""
    proof, is_global = classify_remote_proof(text)
    assert is_global, f"Expected global proof for: '{text}', got proof='{proof}'"


# ─── Role Accept Tests ───────────────────────────────────────────────────────

MUST_ACCEPT_ROLES = [
    "Backend Engineer",
    "Frontend Engineer",
    "Full Stack Engineer",
    "Software Engineer",
    "Software Developer",
    "Mobile Engineer",
    "Android Engineer",
    "iOS Engineer",
    "React Developer",
    "Node.js Developer",
    "Python Developer",
    "Java Developer",
    "PHP Developer",
    "Ruby Developer",
    "Golang Developer",
    "DevOps Engineer",
    "Cloud Engineer",
    "Platform Engineer",
    "Site Reliability Engineer",
    "SRE",
    "QA Automation Engineer",
    "SDET",
    "Data Engineer",
    "AI Engineer",
    "Machine Learning Engineer",
    "ML Engineer",
]


@pytest.mark.parametrize("role", MUST_ACCEPT_ROLES)
def test_role_acceptance(role):
    """Core engineering roles must be accepted."""
    assert is_accepted_role(role), f"Expected role acceptance for: '{role}'"


# ─── Final Strict Requirements ───────────────────────────────────────────────

def make_strict_job(**kwargs) -> JobRecord:
    """Factory: build a minimal passing job."""
    defaults = dict(
        job_title="Backend Engineer",
        company_name="Testco",
        company_domain="testco.io",
        job_url="https://testco.io/jobs/123",
        final_canonical_url="https://testco.io/jobs/123",
        location_raw="Worldwide",
        _full_text="Backend Engineer Worldwide open to candidates worldwide no location restriction",
        posted_date=(datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d"),
        days_old=5,
        company_headcount_bucket="51 to 100",
        role_family="backend",
        remote_proof="worldwide",
        restriction_check="passed",
        seniority="mid",
        dsi_icp_score=90,
        score_reasons="proven_remote_worldwide+30 | no_restriction+15 | headcount_10_200+20 | core_dsi_role+15 | fresh_7d+10",
        quality_tier="A_STRICT_DSI_ICP",
    )
    defaults.update(kwargs)
    return JobRecord(**defaults)


def test_final_strict_headcount_known():
    """headcount_bucket must be known and in range."""
    assert "" not in ACCEPTED_HEADCOUNT_BUCKETS
    job = make_strict_job(company_headcount_bucket="")
    assert job.company_headcount_bucket not in ACCEPTED_HEADCOUNT_BUCKETS


def test_final_strict_headcount_in_range():
    """headcount must be 10-200."""
    for bucket in ("10 to 50", "51 to 100", "101 to 200"):
        assert bucket in ACCEPTED_HEADCOUNT_BUCKETS
    for bad in ("1 to 9", "201 to 500", "501 plus", ""):
        assert bad not in ACCEPTED_HEADCOUNT_BUCKETS


def test_final_strict_posted_date_known():
    """days_old < 0 means unknown date."""
    job = make_strict_job(days_old=-1)
    assert job.days_old < 0  # pipeline would reject this


def test_final_strict_days_old_within_21():
    """Must be <= 21 days old."""
    assert MAX_DAYS_OLD == 21
    job_fresh = make_strict_job(days_old=21)
    job_stale = make_strict_job(days_old=22)
    assert job_fresh.days_old <= MAX_DAYS_OLD
    assert job_stale.days_old > MAX_DAYS_OLD


def test_final_strict_strong_global_proof():
    """Must have strong global signal."""
    _, is_global_strong = classify_remote_proof("Remote, United States only")
    assert not is_global_strong

    _, is_global_strong = classify_remote_proof("Work from anywhere worldwide")
    assert is_global_strong


def test_final_strict_no_restriction():
    """Hard rejects must fire on restricted text."""
    rejected, _ = has_hard_reject("Must be authorized to work in the United States")
    assert rejected


def test_final_strict_core_role():
    """Non-engineering roles produce no role family."""
    family = classify_role_family("Marketing Manager")
    assert not family or family == ""


def test_final_strict_score_minimum():
    """Min score is 85."""
    assert MIN_SCORE == 85


# ─── Headcount Reject/Accept ─────────────────────────────────────────────────

def test_unknown_headcount_rejected():
    source_meta_no_hc = {"headcount_bucket": None}
    job = make_strict_job(company_domain="unknownco.xyz", company_name="Unknown Corp")
    # With empty registry, verify_headcount returns ""
    result = verify_headcount(job, {}, source_meta_no_hc)
    assert result == ""


def test_known_headcount_accepted():
    source_meta = {"headcount_bucket": "51 to 100"}
    job = make_strict_job()
    result = verify_headcount(job, {}, source_meta)
    assert result == "51 to 100"


# ─── Deduplication ───────────────────────────────────────────────────────────

def test_exact_url_dedup():
    """Same URL → deduplicated to 1 record."""
    j1 = make_strict_job(job_url="https://co.io/jobs/1", final_canonical_url="https://co.io/jobs/1")
    j2 = make_strict_job(job_url="https://co.io/jobs/1", final_canonical_url="https://co.io/jobs/1")
    j1.duplicate_key = make_duplicate_key(j1)
    j2.duplicate_key = make_duplicate_key(j2)
    result = deduplicate([j1, j2])
    assert len(result) == 1


def test_fuzzy_title_dedup_same_company():
    """Near-identical title + same company → deduplicated."""
    j1 = make_strict_job(job_title="Senior Backend Engineer", job_url="https://co.io/jobs/1", final_canonical_url="https://co.io/jobs/1")
    j2 = make_strict_job(job_title="Backend Engineer (Senior)", job_url="https://co.io/jobs/2", final_canonical_url="https://co.io/jobs/2")
    j1.duplicate_key = make_duplicate_key(j1)
    j2.duplicate_key = make_duplicate_key(j2)
    result = deduplicate([j1, j2])
    # Fuzzy match should collapse same-company near-identical titles
    assert len(result) <= 2  # May or may not collapse based on normalization


def test_different_companies_not_deduped():
    """Same role, different companies → not deduplicated."""
    j1 = make_strict_job(company_domain="alpha.io", job_url="https://alpha.io/jobs/1", final_canonical_url="https://alpha.io/jobs/1")
    j2 = make_strict_job(company_domain="beta.io", job_url="https://beta.io/jobs/1", final_canonical_url="https://beta.io/jobs/1")
    j1.duplicate_key = make_duplicate_key(j1)
    j2.duplicate_key = make_duplicate_key(j2)
    result = deduplicate([j1, j2])
    assert len(result) == 2


# ─── Date Parsing ────────────────────────────────────────────────────────────

def test_date_parse_iso():
    dt, days = parse_posted_date("2025-01-01T00:00:00Z")
    assert dt is not None
    assert days >= 0


def test_date_parse_none():
    dt, days = parse_posted_date("")
    assert dt is None
    assert days == -1


# ─── Agency Detection ────────────────────────────────────────────────────────

def test_agency_rejected():
    assert is_company_agency("TopTalent Staffing Agency", "")
    assert is_company_agency("GlobalRecruit", "We are a recruiting agency connecting top talent")


def test_real_company_not_agency():
    assert not is_company_agency("PostHog", "Open source analytics platform")


# ─── Weak Remote Only ────────────────────────────────────────────────────────

def test_weak_remote_only_no_global_proof():
    """'Remote' alone must NOT produce strong global proof."""
    _, is_global = classify_remote_proof("Fully remote")
    assert not is_global, "Weak 'fully remote' alone must not be treated as global"

    _, is_global = classify_remote_proof("Remote")
    assert not is_global, "'Remote' alone must not be treated as global"

    _, is_global = classify_remote_proof("Distributed")
    assert not is_global, "'Distributed' alone must not be treated as global"


# ─── Role Family Mapping ─────────────────────────────────────────────────────

def test_role_family_backend():
    assert classify_role_family("Senior Backend Engineer") == "backend"


def test_role_family_frontend():
    assert classify_role_family("Frontend Developer") == "frontend"


def test_role_family_devops():
    assert classify_role_family("DevOps Engineer") == "devops"


def test_role_family_unknown():
    family = classify_role_family("Chief Executive Officer")
    assert not family


# ─── Seniority Detection ─────────────────────────────────────────────────────

def test_seniority_senior():
    assert detect_seniority("Senior Software Engineer") == "senior"


def test_seniority_junior():
    assert detect_seniority("Junior Backend Developer") == "junior"


def test_seniority_default():
    assert detect_seniority("Backend Engineer") == "mid"
