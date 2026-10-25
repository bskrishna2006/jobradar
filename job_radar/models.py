"""Pydantic data models for JobRadar."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import List, Optional, Literal
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from pydantic import BaseModel, Field


def clean_canonical_url(raw_url: str) -> str:
    """Strip tracking and UTM parameters from URLs to form a canonical link."""
    if not raw_url:
        return ""
    try:
        parsed = urlparse(raw_url)
        # Tracking parameters to drop
        drop_prefixes = ("utm_", "ref", "gh_src", "lever-source", "source", "fbclid", "gclid", "trk")
        query_params = parse_qs(parsed.query)
        cleaned_params = {
            k: v for k, v in query_params.items()
            if not any(k.lower().startswith(p) for p in drop_prefixes)
        }
        clean_query = urlencode(cleaned_params, doseq=True)
        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip("/"),
            parsed.params,
            clean_query,
            ""  # drop fragments
        ))
    except Exception:
        return raw_url.strip()


def compute_content_hash(company: str, title: str, location: str) -> str:
    """Generate SHA256 hash of lowercase(company + title + location)."""
    norm = f"{company.strip().lower()}|{title.strip().lower()}|{location.strip().lower()}"
    norm = re.sub(r"\s+", " ", norm)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


class Job(BaseModel):
    id: str = Field(description="Unique deduplicated canonical ID")
    source: str = Field(description="Source name: freehire, greenhouse, lever, ashby")
    external_id: str = Field(default="", description="Original source ID")
    title: str
    company: str
    location: str = "Remote"
    url: str = Field(description="Cleaned canonical application or posting URL")
    description: str = ""
    skills: List[str] = Field(default_factory=list)
    seniority: str = "unspecified"  # intern, junior, middle, senior, lead, unspecified
    category: str = "other"  # ml_ai, data_science, data_analytics, backend, software_engineering, etc.
    work_mode: str = "unspecified"  # remote, hybrid, onsite, unspecified
    country: Optional[str] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: Optional[str] = None
    posted_at: Optional[datetime] = None
    content_hash: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def model_post_init(self, __context) -> None:
        if not self.content_hash:
            self.content_hash = compute_content_hash(self.company, self.title, self.location)
        if not self.id:
            # Key 1: source + external_id, or canonical URL hash
            if self.source and self.external_id:
                self.id = f"{self.source}:{self.external_id}"
            else:
                self.id = f"{self.source}:{hashlib.md5(self.url.encode('utf-8')).hexdigest()}"
        if self.url:
            self.url = clean_canonical_url(self.url)


class UserProfile(BaseModel):
    user_id: int = Field(description="Telegram chat/user ID")
    skills: List[str] = Field(default_factory=list)
    experience_level: str = "fresher"  # fresher, junior, mid, senior
    years_of_experience: float = 0.0
    education: List[str] = Field(default_factory=list)
    projects: List[str] = Field(default_factory=list)
    preferred_roles: List[str] = Field(default_factory=list)
    # Extracted text for embedding/semantic matching (never logged)
    resume_summary_text: str = ""
    parsed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Preferences(BaseModel):
    user_id: int = Field(description="Telegram chat/user ID")
    experience_type: str = "fresher"  # internship, fresher, experienced, senior
    years_of_experience: float = 0.0
    roles: List[str] = Field(
        default_factory=lambda: ["ML Engineer", "Data Analyst", "Data Scientist"]
    )
    skills: List[str] = Field(
        default_factory=lambda: ["Python", "Machine Learning", "SQL", "Pandas"]
    )
    locations: List[str] = Field(
        default_factory=lambda: ["India", "Remote"]
    )
    experience_level: str = "entry-level"  # entry-level, junior, mid-level, senior
    min_score: int = 60
    jobs_per_digest: int = 10
    digest_time: str = "09:00 IST"  # 08:00 IST, 09:00 IST, 10:00 IST, 18:00 IST
    is_paused: bool = False
    onboarding_completed: bool = False
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MatchResult(BaseModel):
    job: Job
    score: int = Field(ge=0, le=100)
    why_matches: str = Field(description="Short concise 1-2 line explanation")
    missing_skills: List[str] = Field(default_factory=list)
    apply_url: str


JobAction = Literal["applied", "saved", "skipped"]


class UserJobRecord(BaseModel):
    user_id: int
    job_id: str
    status: Literal["sent", "applied", "saved", "skipped"] = "sent"
    match_score: int = 0
    job_title: str = ""
    company_name: str = ""
    apply_url: str = ""
    sent_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
