"""Unit tests for Stage 1 rule prefiltering and embedding ranking."""

import pytest
from datetime import datetime, timezone, timedelta
from job_radar.models import Job, Preferences, UserProfile
from job_radar.matching.prefilter import prefilter_job, rank_jobs_by_embedding


@pytest.fixture
def default_preferences():
    return Preferences(
        user_id=12345,
        roles=["ML Engineer", "Data Analyst", "Data Scientist"],
        skills=["Python", "Machine Learning", "SQL"],
        locations=["India", "Remote"],
        experience_level="entry-level",
        min_score=60,
    )


def test_prefilter_accepts_valid_fresher_job(default_preferences):
    job = Job(
        id="test:1",
        source="freehire",
        title="Junior Machine Learning Engineer",
        company="TechCorp",
        location="Bengaluru, India",
        url="https://example.com/job1",
        description="Looking for fresher or junior engineer with Python and ML skills.",
        skills=["Python", "SQL"],
        seniority="junior",
        posted_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    assert prefilter_job(job, default_preferences) is True


def test_prefilter_rejects_senior_roles_for_fresher(default_preferences):
    job = Job(
        id="test:2",
        source="freehire",
        title="Senior Lead ML Engineer",
        company="BigCorp",
        location="Remote",
        url="https://example.com/job2",
        description="Requires 8+ years leading ML infrastructure.",
        seniority="senior",
        posted_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    assert prefilter_job(job, default_preferences) is False


def test_prefilter_rejects_unmatched_location(default_preferences):
    job = Job(
        id="test:3",
        source="freehire",
        title="Junior Data Scientist",
        company="BerlinTech",
        location="Munich, Germany (Onsite only)",
        work_mode="onsite",
        country="DE",
        url="https://example.com/job3",
        description="Onsite position in Munich. German language required.",
        posted_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    assert prefilter_job(job, default_preferences) is False


def test_prefilter_rejects_old_jobs(default_preferences):
    job = Job(
        id="test:4",
        source="freehire",
        title="Junior Data Analyst",
        company="Stripe",
        location="Remote",
        url="https://example.com/job4",
        description="Data analyst role.",
        posted_at=datetime.now(timezone.utc) - timedelta(days=50),
    )
    assert prefilter_job(job, default_preferences) is False


@pytest.mark.asyncio
async def test_rank_jobs_by_embedding():
    profile = UserProfile(
        user_id=12345,
        skills=["Python", "PyTorch", "NLP", "Machine Learning"],
        experience_level="fresher",
        preferred_roles=["ML Engineer", "Data Scientist"],
        resume_summary_text="Machine learning engineer skilled in Python, PyTorch and transformer NLP models.",
    )

    ml_job = Job(
        id="test:ml",
        source="freehire",
        title="Machine Learning Engineer",
        company="OpenAI Research",
        location="Remote",
        url="https://example.com/ml",
        description="Train NLP transformers and PyTorch neural networks in Python.",
        skills=["Python", "PyTorch", "NLP"],
    )

    sales_job = Job(
        id="test:sales",
        source="freehire",
        title="Enterprise Account Executive",
        company="SalesForce",
        location="Remote",
        url="https://example.com/sales",
        description="Drive B2B sales cycles and closing CRM contracts with enterprise clients.",
        skills=["B2B Sales", "Cold Calling"],
    )

    ranked = await rank_jobs_by_embedding([sales_job, ml_job], profile, top_n=2)
    assert len(ranked) == 2
    # ML job should have higher cosine similarity than sales job
    assert ranked[0][0].id == "test:ml"
    assert ranked[0][1] > ranked[1][1]
