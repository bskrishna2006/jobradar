"""Unit tests for job deduplication across IDs and cross-board content hashes."""

from datetime import datetime, timezone
from job_radar.dedupe import deduplicate_jobs
from job_radar.models import Job, compute_content_hash, clean_canonical_url


def test_clean_canonical_url():
    raw = "https://boards.greenhouse.io/stripe/jobs/123?utm_source=telegram&utm_campaign=daily&ref=jobradar#apply"
    cleaned = clean_canonical_url(raw)
    assert "utm_source" not in cleaned
    assert "ref=" not in cleaned
    assert cleaned == "https://boards.greenhouse.io/stripe/jobs/123"


def test_compute_content_hash():
    h1 = compute_content_hash("Stripe", "ML Engineer", "Remote")
    h2 = compute_content_hash("stripe ", " ml engineer", "remote  ")
    assert h1 == h2
    assert len(h1) == 64


def test_deduplicate_by_primary_id():
    j1 = Job(
        id="freehire:123",
        source="freehire",
        external_id="123",
        title="ML Engineer",
        company="Stripe",
        location="Remote",
        url="https://stripe.com/jobs/123",
        description="Short description",
    )
    j2 = Job(
        id="freehire:123",
        source="freehire",
        external_id="123",
        title="ML Engineer",
        company="Stripe",
        location="Remote",
        url="https://stripe.com/jobs/123",
        description="Duplicate with same ID",
    )
    res = deduplicate_jobs([j1, j2])
    assert len(res) == 1
    assert res[0].id == "freehire:123"


def test_deduplicate_cross_source_content_hash():
    # Same job from Greenhouse and Freehire
    j_gh = Job(
        id="greenhouse:stripe:456",
        source="greenhouse",
        external_id="456",
        title="Data Scientist",
        company="Stripe",
        location="Bengaluru, India",
        url="https://boards.greenhouse.io/stripe/jobs/456",
        description="Short description from Greenhouse",
    )
    j_fh = Job(
        id="freehire:999",
        source="freehire",
        external_id="999",
        title="Data Scientist",
        company="Stripe",
        location="Bengaluru, India",
        url="https://freehire.me/jobs/stripe-data-scientist-456",
        description="Much richer and detailed description from Freehire containing full requirements and benefits.",
        skills=["Python", "SQL", "Pandas"],
    )

    res = deduplicate_jobs([j_gh, j_fh])
    assert len(res) == 1
    # Richer posting should be preserved
    assert len(res[0].description) > len(j_gh.description)
