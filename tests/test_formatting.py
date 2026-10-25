"""Unit tests for Telegram message formatting and length constraints."""

from job_radar.bot.formatting import (
    format_job_message,
    format_profile_summary,
    split_message,
    format_stats_message,
)
from job_radar.models import Job, MatchResult, UserProfile


def test_format_job_message_exact_structure():
    job = Job(
        id="freehire:101",
        source="freehire",
        title="Junior ML Engineer",
        company="Acme AI",
        location="Bengaluru, India",
        url="https://company.com/careers/101",
        description="ML role",
    )
    match = MatchResult(
        job=job,
        score=87,
        why_matches="Strong Python and SQL alignment. Fits entry level.",
        missing_skills=["Docker", "Kubernetes"],
        apply_url=job.url,
    )
    msg = format_job_message(match)

    # Check required lines from acceptance criteria:
    # Company - Role (Location)
    # Match: 87%
    # Why: <2 lines>
    # Missing: <skills>
    # Apply: <link>
    assert "Acme AI - Junior ML Engineer" in msg
    assert "Bengaluru, India" in msg
    assert "<b>Match:</b> 87%" in msg
    assert "<b>Why:</b> Strong Python and SQL alignment. Fits entry level." in msg
    assert "<b>Missing:</b> Docker, Kubernetes" in msg
    assert "https://company.com/careers/101" in msg


def test_split_message_under_4096():
    # Long text with 10,000 characters
    long_text = "\n".join([f"Line {i}: This is some long message content for testing chunking." for i in range(250)])
    chunks = split_message(long_text, max_length=4096)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 4096


def test_format_stats_message():
    stats = {"total": 45, "applied": 12, "saved": 8, "skipped": 20, "sent": 5}
    msg = format_stats_message(stats)
    assert "Total Jobs Delivered:</b> 45" in msg
    assert "Applied:</b> 12" in msg
    assert "Saved:</b> 8" in msg
