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
    r"\b(senior|sr\.?|lead|staff|principal|director|head of|vp|vice president|architect|manager|owner|cro|cxo|c-level|chief|executive|distinguished|partner)\b",
    re.IGNORECASE,
)
JUNIOR_TITLES_REGEX = re.compile(
    r"\b(junior|jr\.?|intern|internship|fresher|entry[- ]level|associate|trainee|graduate|engineer i\b)\b",
    re.IGNORECASE,
)

INDIA_LOCATIONS = {
    "india", "bengaluru", "bangalore", "mumbai", "delhi", "new delhi", "ncr", "gurgaon", "gurugram",
    "noida", "hyderabad", "pune", "chennai", "kolkata", "ahmedabad", "jaipur", "kochi", "in"
}

FOREIGN_COUNTRIES = {
    "canada", "united states", "usa", "u.s.", "u.s.a", "poland", "united kingdom", "uk", "u.k.",
    "germany", "france", "spain", "netherlands", "ireland", "australia", "new zealand", "japan",
    "sweden", "switzerland", "brazil", "mexico", "israel", "austria", "belgium", "italy",
    "portugal", "denmark", "norway", "finland", "czech", "romania", "bulgaria", "hungary",
    "ukraine", "estonia", "latvia", "lithuania", "emea", "latam", "americas", "singapore",
    "philippines", "berlin", "london"
}

INDIA_LOCATION_REGEX = re.compile(
    r"\b(india|bengaluru|bangalore|mumbai|delhi|new delhi|ncr|gurgaon|gurugram|noida|hyderabad|pune|chennai|kolkata|ahmedabad|jaipur|kochi)\b",
    re.IGNORECASE,
)

FOREIGN_LOCATION_REGEX = re.compile(
    r"\b(canada|united states|usa|u\.s\.|u\.s\.a|poland|united kingdom|uk|u\.k\.|germany|france|spain|netherlands|ireland|australia|new zealand|japan|sweden|switzerland|brazil|mexico|israel|austria|belgium|italy|portugal|denmark|norway|finland|czech|romania|bulgaria|hungary|ukraine|estonia|latvia|lithuania|emea|latam|americas|singapore|philippines|berlin|london)\b",
    re.IGNORECASE,
)

CONFLICTING_STACKS = {
    "ruby": ["ruby", "rails"],
    "rust": ["rust"],
    "php": ["php", "laravel", "symfony"],
    "swift": ["swift", "ios"],
    "kotlin": ["kotlin", "android"],
    "c++": ["c++", "cpp"],
    "c#": ["c#", ".net"],
}


def location_matches(job: Job, preferred_locations: List[str]) -> bool:
    """Check if job location aligns with user's preferred locations without false-matching foreign remote jobs."""
    if not preferred_locations:
        return True

    job_loc_lower = (job.location or "").lower()
    job_country_lower = (job.country or "").lower()

    # 1. Detect if job is in India
    job_in_india = (
        job_country_lower in ("in", "india")
        or bool(INDIA_LOCATION_REGEX.search(job_loc_lower))
    )

    # 2. Detect if job is explicitly locked to a foreign country
    has_foreign_restriction = bool(FOREIGN_LOCATION_REGEX.search(job_loc_lower))

    # 3. Detect if job is truly global / worldwide remote
    is_worldwide_remote = (
        "worldwide" in job_loc_lower
        or "anywhere" in job_loc_lower
        or "global" in job_loc_lower
        or "apac" in job_loc_lower
        or (job_loc_lower.strip() in ("remote", "virtual") and not has_foreign_restriction and not job_country_lower)
    )

    user_wants_india = any(
        p.lower() in ("india", "in") or bool(INDIA_LOCATION_REGEX.search(p))
        for p in preferred_locations
    )

    user_wants_remote = any(p.lower() == "remote" for p in preferred_locations)

    # If user target includes India
    if user_wants_india:
        if job_in_india:
            return True
        if is_worldwide_remote and not has_foreign_restriction:
            return True
        return False

    # If user target is Worldwide Remote (without India specifically)
    if user_wants_remote:
        if is_worldwide_remote and not has_foreign_restriction:
            return True
        if job_in_india:
            return True
        return False

    # Fallback for custom specified cities or countries
    for pref in preferred_locations:
        p_clean = pref.strip().lower()
        if p_clean in job_loc_lower or p_clean == job_country_lower:
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
        if is_intern_job:
            return True
        if SENIOR_TITLES_REGEX.search(title_lower):
            return False
        return JUNIOR_TITLES_REGEX.search(title_lower) is not None

    if exp_type == "fresher" or user_exp in ("entry-level", "fresher", "junior"):
        if SENIOR_TITLES_REGEX.search(title_lower):
            return False
        if seniority_lower in ("senior", "lead", "staff", "principal", "c_level"):
            return False
        if JUNIOR_TITLES_REGEX.search(title_lower):
            return True

    return True


ROLE_SYNONYMS = {
    "ml": ["machine learning", "ml", "ai", "artificial intelligence", "deep learning", "nlp", "llm", "data science", "data modeling", "mlops"],
    "ai": ["machine learning", "ml", "ai", "artificial intelligence", "deep learning", "nlp", "llm", "genai", "generative ai"],
    "data scientist": ["data science", "data scientist", "machine learning", "analytics", "modeling"],
    "data analyst": ["data analyst", "data analytics", "business intelligence", "bi", "analytics", "sql"],
    "backend": ["backend", "back-end", "python", "api", "django", "fastapi", "flask", "server", "microservices"],
    "full stack": ["full stack", "fullstack", "frontend", "front-end", "react", "node", "software engineer"],
    "frontend": ["frontend", "front-end", "react", "vue", "angular", "ui", "javascript"],
}


def has_conflicting_tech_stack(job: Job, candidate_skills: List[str]) -> bool:
    """Reject jobs that explicitly require a conflicting stack in parentheses (e.g. Ruby, Rust) that candidate doesn't know."""
    title_lower = job.title.lower()
    cand_skills_lower = {s.lower().strip() for s in candidate_skills}

    for tech_name, syns in CONFLICTING_STACKS.items():
        has_tech_in_title = any(
            f"({s})" in title_lower or f": {s}" in title_lower or f" {s} " in f" {title_lower} "
            for s in syns
        )
        if has_tech_in_title and not any(s in cand_skills_lower for s in syns):
            return True

    return False


def role_or_skill_matches(
    job: Job,
    preferred_roles: List[str],
    preferred_skills: List[str],
    candidate_skills: Optional[List[str]] = None,
) -> bool:
    """Check if job title or description has overlap with candidate's focus."""
    if not preferred_roles and not preferred_skills:
        return True

    all_skills = list(set((preferred_skills or []) + (candidate_skills or [])))
    if has_conflicting_tech_stack(job, all_skills):
        return False

    title_lower = job.title.lower()
    text_to_check = f"{job.title} {' '.join(job.skills)} {job.description[:600]}".lower()

    for role in preferred_roles:
        r_clean = role.lower().strip()
        if r_clean in title_lower:
            return True

        r_terms = [w for w in re.split(r"\s+", r_clean) if len(w) > 2]
        if any(term in title_lower for term in r_terms):
            return True

        for key, syns in ROLE_SYNONYMS.items():
            if key in r_clean:
                if any(syn in text_to_check for syn in syns):
                    return True

    for skill in preferred_skills:
        s_clean = skill.strip().lower()
        if s_clean and s_clean in text_to_check:
            return True

    return False


def prefilter_job(job: Job, preferences: Preferences, profile: Optional[UserProfile] = None) -> bool:
    """
    Stage 1 rule-based check:
    1. Age: drop if older than 35 days (when date is known)
    2. Location alignment (India / Remote / specified cities, excluding foreign country restrictions)
    3. Experience / seniority guardrails (internship vs full-time vs senior/owner)
    4. Target role / skill keywords without conflicting tech stacks
    """
    if job.posted_at:
        age_cutoff = datetime.now(timezone.utc) - timedelta(days=35)
        if job.posted_at < age_cutoff:
            return False

    if not location_matches(job, preferences.locations):
        return False

    exp_type = getattr(preferences, "experience_type", "fresher")
    if not experience_matches(job, preferences.experience_level, experience_type=exp_type):
        return False

    pref_roles = preferences.roles if preferences.roles else (profile.preferred_roles if profile else [])
    pref_skills = preferences.skills if preferences.skills else (profile.skills if profile else [])
    candidate_skills = profile.skills if profile else []

    if not role_or_skill_matches(job, pref_roles, pref_skills, candidate_skills=candidate_skills):
        return False

    return True


async def rank_jobs_by_embedding(
    jobs: List[Job],
    profile: UserProfile,
    top_n: int = 25,
) -> List[Tuple[Job, float]]:
    """Rank prefiltered jobs using vector embedding cosine similarity against candidate profile."""
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

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_n]
