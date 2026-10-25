"""Stage 2 matching: LLM-based fit scoring, smart heuristic matching, and missing skills analysis."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import List, Optional
from job_radar.llm import BaseLLMClient, get_llm_client
from job_radar.models import Job, MatchResult, Preferences, UserProfile
from job_radar.matching.prefilter import (
    INDIA_LOCATIONS,
    FOREIGN_COUNTRIES,
    INDIA_LOCATION_REGEX,
    FOREIGN_LOCATION_REGEX,
    CONFLICTING_STACKS,
)

logger = logging.getLogger(__name__)

SCORER_PROMPT = """
You are a technical hiring manager evaluating a candidate's fit for a job posting.

Candidate Profile:
- Current Status / Target: {exp_type}
- Experience Level: {exp_level} ({yoe} years)
- Technical Skills: {skills}
- Target Roles: {roles}
- Profile Summary: {summary}

Job Details:
- Title: {title}
- Company: {company}
- Location: {location}
- Description:
\"\"\"{description}\"\"\"

Evaluate the match between 0 and 100 based on technical skill alignment, domain fit, and seniority expectations.
Important Rules:
- If candidate is a Fresher or College Student: strictly score Senior, Staff, Lead, Architect, or Owner/CRO positions below 40.
- If candidate specializes in ML/AI/Data and the job is unrelated (e.g. Ruby backend, Hardware, Sales), score below 50.
- If it's a solid match for their skills and experience level, score 75-95.

Return JSON in this exact structure:
{{
  "score": 85,
  "why_matches": "Max 2 lines highlighting key strengths/alignment",
  "missing_skills": ["Skill1", "Skill2"]
}}
"""

ROLE_SYNONYMS = {
    "ml": ["machine learning", "ml", "ai", "artificial intelligence", "deep learning", "nlp", "computer vision", "llm", "generative ai", "data science", "mlops"],
    "ai": ["machine learning", "ml", "ai", "artificial intelligence", "deep learning", "nlp", "llm", "genai"],
    "data scientist": ["data science", "data scientist", "machine learning", "analytics", "modeling"],
    "data analyst": ["data analyst", "data analytics", "business intelligence", "bi", "analytics", "sql"],
    "backend": ["backend", "back-end", "python", "api", "django", "fastapi", "flask", "server", "microservices"],
    "full stack": ["full stack", "fullstack", "frontend", "front-end", "react", "node", "software engineer"],
    "frontend": ["frontend", "front-end", "react", "vue", "angular", "ui", "javascript"],
    "software": ["software engineer", "software developer", "sde", "swe", "developer"],
}

COMMON_TECH_CATALOG = [
    "Docker", "Kubernetes", "AWS", "GCP", "Azure", "PyTorch", "TensorFlow", "FastAPI",
    "SQL", "PostgreSQL", "MongoDB", "Kafka", "Redis", "Git", "React", "Linux", "CI/CD",
    "MLOps", "Pandas", "Scikit-Learn", "Java", "Node.js", "TypeScript"
]


def compute_smart_match(
    job: Job,
    profile: UserProfile,
    preferences: Optional[Preferences] = None,
) -> MatchResult:
    """
    Multi-factor precision matching combining:
    1. Seniority & Internship status fit (0 - 30 points)
    2. Role & Title alignment with synonym mapping (0 - 35 points)
    3. Technical Skill overlap & Missing skills detection (0 - 25 points)
    4. Location & Work mode fit (0 - 10 points)
    """
    job_text = f"{job.title} {job.location} {' '.join(job.skills)} {job.description}".lower()
    title_lower = job.title.lower()
    desc_sample = job.description[:800].lower()

    # Determine candidate attributes
    exp_type = preferences.experience_type if preferences else getattr(profile, "experience_level", "fresher")
    exp_type = (exp_type or "fresher").lower()

    # Strictly prioritize user's active preferences.roles
    if preferences and preferences.roles:
        target_roles = preferences.roles
    else:
        target_roles = profile.preferred_roles or ["Machine Learning Engineer", "Software Engineer"]

    user_skills = list(dict.fromkeys(
        (profile.skills or []) +
        (preferences.skills if preferences and preferences.skills else [])
    ))

    # --- 1. Seniority & Internship Fit (0 - 30 points) ---
    is_intern_job = bool(
        re.search(r"\b(intern|internship|trainee|co-?op|apprentice|student|campus|graduate trainee)\b", title_lower)
        or re.search(r"\b(intern|internship|trainee)\b", desc_sample)
    )
    is_junior_job = bool(
        re.search(r"\b(junior|jr\.?|associate|entry[- ]level|graduate|fresher|engineer i\b)\b", title_lower)
    )
    is_senior_job = bool(
        re.search(r"\b(senior|sr\.?|lead|staff|principal|director|manager|architect|head of|vp|owner|cro|cxo|chief|executive|distinguished|partner)\b", title_lower)
    )

    seniority_score = 15
    status_label = "Entry-level"
    if exp_type == "internship":
        status_label = "Internship"
        if is_intern_job:
            seniority_score = 30
        elif is_junior_job and not is_senior_job:
            seniority_score = 22
        elif is_senior_job:
            seniority_score = 0
        else:
            seniority_score = 15
    elif exp_type == "fresher":
        status_label = "Fresher / Graduate"
        if is_senior_job:
            seniority_score = 0
        elif is_junior_job or is_intern_job:
            seniority_score = 30
        else:
            seniority_score = 20
    elif exp_type in ("mid", "experienced"):
        status_label = "Mid-Level"
        if not is_intern_job and not is_senior_job:
            seniority_score = 30
        elif is_senior_job:
            seniority_score = 20
        else:
            seniority_score = 10
    elif exp_type == "senior":
        status_label = "Senior"
        if is_senior_job:
            seniority_score = 30
        else:
            seniority_score = 15

    # --- 2. Role Title & Category Alignment (0 - 35 points) ---
    title_score = 0
    best_role_match = target_roles[0] if target_roles else "Target Role"

    cand_skills_lower = {s.lower().strip() for s in user_skills}
    has_conflict = False
    for tech_name, syns in CONFLICTING_STACKS.items():
        if any(f"({s})" in title_lower or f": {s}" in title_lower or f" {s} " in f" {title_lower} " for s in syns):
            if not any(s in cand_skills_lower for s in syns):
                has_conflict = True
                break

    if not has_conflict:
        for role in target_roles:
            r_clean = role.lower().strip()
            if r_clean in title_lower:
                title_score = 35
                best_role_match = role
                break

            synonyms = [r_clean]
            for key, syn_list in ROLE_SYNONYMS.items():
                if key in r_clean:
                    synonyms.extend(syn_list)

            matched_syn = False
            for syn in synonyms:
                if re.search(r"\b" + re.escape(syn) + r"\b", title_lower):
                    title_score = max(title_score, 30)
                    best_role_match = role
                    matched_syn = True
                    break
                elif re.search(r"\b" + re.escape(syn) + r"\b", desc_sample):
                    title_score = max(title_score, 20)
                    best_role_match = role

            if matched_syn:
                break

    # --- 3. Skill Overlap & Missing Skills (0 - 25 points) ---
    matched_skills = []
    for s in user_skills:
        s_clean = s.strip()
        if not s_clean:
            continue
        pat = r"\b" + re.escape(s_clean.lower()) + r"\b"
        if re.search(pat, job_text):
            matched_skills.append(s_clean)

    user_skills_set = {s.lower() for s in user_skills}
    missing_skills = []
    for tech in COMMON_TECH_CATALOG:
        if tech.lower() in job_text and tech.lower() not in user_skills_set:
            missing_skills.append(tech)

    n_matches = len(matched_skills)
    if n_matches >= 4:
        skill_score = 25
    elif n_matches == 3:
        skill_score = 20
    elif n_matches == 2:
        skill_score = 15
    elif n_matches == 1:
        skill_score = 10
    else:
        skill_score = 5

    # --- 4. Location & Work Mode Fit (0 - 10 points) ---
    loc_score = 4
    job_loc_lower = (job.location or "").lower()
    job_country_lower = (job.country or "").lower()
    pref_locs = [l.lower() for l in (preferences.locations if preferences else ["india", "remote"])]

    job_in_india = (
        job_country_lower in ("in", "india")
        or bool(INDIA_LOCATION_REGEX.search(job_loc_lower))
    )
    has_foreign_restriction = bool(FOREIGN_LOCATION_REGEX.search(job_loc_lower))

    user_wants_india = any(
        p.lower() in ("india", "in") or bool(INDIA_LOCATION_REGEX.search(p))
        for p in pref_locs
    )

    if user_wants_india:
        if job_in_india:
            loc_score = 10
        elif has_foreign_restriction:
            loc_score = 0
        else:
            loc_score = 6
    elif any("remote" in p for p in pref_locs) and not has_foreign_restriction:
        loc_score = 10

    # Total Score Calculation
    total_score = min(98, max(25, seniority_score + title_score + skill_score + loc_score))

    # Clean explanation
    skills_preview = ", ".join(matched_skills[:3]) if matched_skills else "core foundational skills"
    location_desc = "India" if job_in_india else ("Remote" if not has_foreign_restriction else job.location)
    why_text = (
        f"Strong match for {best_role_match} ({status_label}). "
        f"Aligns with your skills in {skills_preview} | Location: {location_desc}."
    )

    return MatchResult(
        job=job,
        score=total_score,
        why_matches=why_text,
        missing_skills=missing_skills[:4],
        apply_url=job.url,
    )


async def score_single_job(
    job: Job,
    profile: UserProfile,
    preferences: Optional[Preferences] = None,
    llm: Optional[BaseLLMClient] = None,
) -> MatchResult:
    """Score a single job against user profile via LLM or smart precision matching."""
    client = llm or get_llm_client()

    exp_type_str = preferences.experience_type if preferences else profile.experience_level
    if exp_type_str == "internship":
        exp_type_desc = "College Student actively seeking an Internship / Co-op"
    elif exp_type_str == "fresher":
        exp_type_desc = "Recent Graduate / Fresher (0-1 YOE)"
    elif exp_type_str == "mid":
        exp_type_desc = "Mid-level Engineer (1-3 YOE)"
    else:
        exp_type_desc = f"{exp_type_str.capitalize()} level"

    target_roles_str = ", ".join(preferences.roles if preferences and preferences.roles else profile.preferred_roles)

    prompt = SCORER_PROMPT.format(
        exp_type=exp_type_desc,
        exp_level=profile.experience_level,
        yoe=profile.years_of_experience,
        skills=", ".join(profile.skills[:15]),
        roles=target_roles_str,
        summary=profile.resume_summary_text[:400],
        title=job.title,
        company=job.company,
        location=job.location,
        description=job.description[:1800],
    )

    try:
        data = await client.complete_json(
            prompt=prompt,
            system_prompt="You are a strict technical job matching evaluator. Output valid JSON only.",
        )
        score = int(data.get("score", 70))
        score = max(0, min(100, score))
        why = str(data.get("why_matches", "Matches core role requirements.")).strip()
        lines = [l.strip() for l in why.split("\n") if l.strip()]
        why_clean = " ".join(lines[:2])
        missing = [str(s).strip() for s in data.get("missing_skills", []) if str(s).strip()]

        return MatchResult(
            job=job,
            score=score,
            why_matches=why_clean,
            missing_skills=missing[:5],
            apply_url=job.url,
        )
    except Exception as e:
        logger.debug(f"Using smart precision match for job {job.id}: {e}")
        return compute_smart_match(job, profile, preferences=preferences)


async def score_jobs_with_llm(
    jobs: List[Job],
    profile: UserProfile,
    preferences: Optional[Preferences] = None,
    min_score: int = 60,
    max_concurrent: int = 5,
    llm: Optional[BaseLLMClient] = None,
) -> List[MatchResult]:
    """Score prefiltered top jobs in parallel with concurrency limiting, filtering by min_score."""
    if not jobs:
        return []

    client = llm or get_llm_client()
    sem = asyncio.Semaphore(max_concurrent)

    async def _bounded_score(job: Job) -> MatchResult:
        async with sem:
            return await score_single_job(job, profile, preferences=preferences, llm=client)

    tasks = [_bounded_score(j) for j in jobs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    passed: List[MatchResult] = []
    for res in results:
        if isinstance(res, MatchResult):
            if res.score >= min_score:
                passed.append(res)
        else:
            logger.error(f"Error in job scoring task: {res}")

    passed.sort(key=lambda m: m.score, reverse=True)
    return passed
