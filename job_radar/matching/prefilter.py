"""Stage 1 matching: Rule-based prefiltering and embedding similarity ranking."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

from job_radar.models import Job, Preferences, UserProfile
from job_radar.matching.embeddings import get_embedding_client, cosine_similarity

logger = logging.getLogger(__name__)

SENIOR_TITLES_REGEX = re.compile(
    r"\b(senior|sr\.?|lead|staff|principal|director|head of|vp|vice president|architect|manager)\b",
    re.IGNORECASE,
)
JUNIOR_TITLES_REGEX = re.compile(
    r"\b(junior|jr\.?|intern|internship|fresher|entry[- ]level|associate|trainee|graduate)\b",
    re.IGNORECASE,
)

INDIA_CITIES = {
    "india", "bengaluru", "bangalore", "mumbai", "delhi", "new delhi", "ncr", "gurgaon", "gurugram",
    "noida", "hyderabad", "pune", "chennai", "kolkata", "ahmedabad", "jaipur", "kochi", "in"
}


def location_matches(job: Job, preferred_locations: List[str]) -> bool:
    """Check if job location aligns with user's preferred locations."""
    if not preferred_locations:
        return True

    job_loc_lower = (job.location or "").lower()
    job_country_lower = (job.country or "").lower()
    is_remote_job = (
        "remote" in job_loc_lower
        or "anywhere" in job_loc_lower
        or "worldwide" in job_loc_lower
        or job.work_mode.lower() == "remote"
    )

    for pref in preferred_locations:
        p_lower = pref.strip().lower()
        if p_lower == "remote" and is_remote_job:
            return True
        if p_lower in ("india", "in"):
            if is_remote_job:
                return True
            if job_country_lower in ("in", "india"):
                return True
            if any(city in job_loc_lower for city in INDIA_CITIES):
                return True
        elif p_lower in job_loc_lower or p_lower in job_country_lower:
            return True

    return False


def experience_matches(job: Job, user_experience: str, experience_type: str = "fresher") -> bool:
    """Check experience level compatibility, prioritizing internships for college students."""
    user_exp = (user_experience or "entry-level").lower()
    exp_type = (experience_type or "fresher").lower()
    title_lower = job.title.lower()
    seniority_lower = (job.seniority or "").lower()
    desc_lower = job.description[:600].lower()

    is_intern_job = bool(
        re.search(r"\b(intern|internship|trainee|apprentice|student|co-?op)\b", title_lower)
        or re.search(r"\b(intern|internship|trainee)\b", desc_lower)
    )

    if exp_type == "internship":
        # Strongly require intern, trainee, or early entry-level positions
        if is_intern_job:
            return True
        # If title has senior/lead, strictly reject
        if SENIOR_TITLES_REGEX.search(title_lower):
            return False
        # Allow junior/associate roles if no strict intern tag
        return JUNIOR_TITLES_REGEX.search(title_lower) is not None

    if exp_type == "fresher" or user_exp in ("entry-level", "fresher", "junior"):
        if JUNIOR_TITLES_REGEX.search(title_lower):
            return True
        if SENIOR_TITLES_REGEX.search(title_lower):
            return False
        if seniority_lower in ("senior", "lead", "staff", "principal", "c_level"):
            return False

    return True


ROLE_SYNONYMS = {
    "ml": ["machine learning", "ml", "ai", "artificial intelligence", "deep learning", "nlp", "llm", "data science"],
    "ai": ["machine learning", "ml", "ai", "artificial intelligence", "deep learning", "nlp", "llm"],
    "data scientist": ["data science", "data scientist", "machine learning", "analytics", "modeling"],
    "data analyst": ["data analyst", "data analytics", "business intelligence", "bi", "analytics", "sql"],
    "backend": ["backend", "back-end", "python", "api", "django", "fastapi", "flask", "server", "software engineer"],
    "full stack": ["full stack", "fullstack", "frontend", "front-end", "react", "node", "software engineer"],
    "frontend": ["frontend", "front-end", "react", "vue", "angular", "ui", "javascript"],
}


def role_or_skill_matches(job: Job, preferred_roles: List[str], preferred_skills: List[str]) -> bool:
    """Check if job title or description has overlap with candidate's focus."""
    if not preferred_roles and not preferred_skills:
        return True

    text_to_check = f"{job.title} {' '.join(job.skills)} {job.description[:600]}".lower()

    # Check roles with synonym expansion
    for role in preferred_roles:
        r_clean = role.lower().strip()
        if r_clean in text_to_check:
            return True
        r_terms = [w for w in re.split(r"\s+", r_clean) if len(w) > 2]
        if any(term in text_to_check for term in r_terms):
            return True
        for key, syns in ROLE_SYNONYMS.items():
            if key in r_clean:
                if any(syn in text_to_check for syn in syns):
                    return True

    # Check skills
    for skill in preferred_skills:
        s_clean = skill.strip().lower()
        if s_clean and s_clean in text_to_check:
            return True

    return False


def prefilter_job(job: Job, preferences: Preferences, profile: Optional[UserProfile] = None) -> bool:
    """
    Stage 1 rule-based check:
    1. Posting age: drop if older than 35 days (when date is known)
    2. Location alignment (India / Remote / specified cities)
    3. Experience / seniority guardrails (internship vs full-time)
    4. Target role / skill keywords
    """
    # 1. Age
    if job.posted_at:
        age_cutoff = datetime.now(timezone.utc) - timedelta(days=35)
        if job.posted_at < age_cutoff:
            return False

    # 2. Location
    if not location_matches(job, preferences.locations):
        return False

    # 3. Experience level & internship focus
    exp_type = getattr(preferences, "experience_type", "fresher")
    if not experience_matches(job, preferences.experience_level, experience_type=exp_type):
        return False

    # 4. Role / skills
    pref_roles = preferences.roles or (profile.preferred_roles if profile else [])
    pref_skills = preferences.skills or (profile.skills if profile else [])
    if not role_or_skill_matches(job, pref_roles, pref_skills):
        return False

    return True


async def rank_jobs_by_embedding(
    jobs: List[Job],
    profile: UserProfile,
    top_n: int = 25,
) -> List[Tuple[Job, float]]:
    """
    Rank prefiltered jobs using vector embedding cosine similarity against candidate profile.
    Returns the top N candidates to be sent to Stage 2 LLM scoring.
    """
    if not jobs:
        return []

    client = get_embedding_client()
    profile_text = profile.resume_summary_text or f"Skills: {', '.join(profile.skills)}. Roles: {', '.join(profile.preferred_roles)}"
    profile_vec = await client.embed_text(profile_text)

    job_texts = [
        f"{j.title} at {j.company}. Location: {j.location}. Category: {j.category}. "
        f"Skills: {', '.join(j.skills[:6])}. Summary: {j.description[:500]}"
        for j in jobs
    ]

    job_vecs = await client.embed_batch(job_texts)

    scored: List[Tuple[Job, float]] = []
    for job, j_vec in zip(jobs, job_vecs):
        sim = cosine_similarity(profile_vec, j_vec)
        scored.append((job, sim))

    # Sort descending by similarity
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_n]
