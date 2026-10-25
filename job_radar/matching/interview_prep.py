"""Interview preparation generator based on job description and user resume."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from job_radar.llm import BaseLLMClient, get_llm_client
from job_radar.models import Job, UserProfile

logger = logging.getLogger(__name__)

INTERVIEW_PREP_PROMPT = """
You are an expert interview coach and technical hiring bar-raiser.
A candidate has an upcoming interview for the following job posting.
Generate targeted, high-yield preparation material tailored specifically to this job description and the candidate's resume profile.

Candidate Profile:
- Skills: {skills}
- Experience Level: {exp_level} ({yoe} years)
- Projects: {projects}
- Resume Summary: {profile_summary}

Job Details:
- Role: {title}
- Company: {company}
- Location: {location}
- Description:
\"\"\"{job_description}\"\"\"

Provide your preparation guide as a JSON object with these 4 sections:
{{
  "technical_questions": [
    "5 deep, realistic technical questions testing key requirements from the JD"
  ],
  "dsa_topics": [
    "3-4 core DSA or algorithmic problem patterns expected for this specific role/level"
  ],
  "resume_based_questions": [
    "3 specific questions grilling the candidate on their projects and experience relative to this job"
  ],
  "company_specific_points": [
    "3 actionable research and behavioral points about the company, architecture scale, or product domain"
  ]
}}
"""


async def generate_interview_prep(
    job: Job,
    profile: UserProfile,
    llm: Optional[BaseLLMClient] = None,
) -> Dict[str, Any]:
    """Generate comprehensive interview preparation guide."""
    client = llm or get_llm_client()

    prompt = INTERVIEW_PREP_PROMPT.format(
        skills=", ".join(profile.skills[:15]),
        exp_level=profile.experience_level,
        yoe=profile.years_of_experience,
        projects="; ".join(profile.projects[:3]),
        profile_summary=profile.resume_summary_text[:500],
        title=job.title,
        company=job.company,
        location=job.location,
        job_description=job.description[:2500],
    )

    try:
        return await client.complete_json(
            prompt=prompt,
            system_prompt="You are an expert technical interview coach. Respond with valid JSON only.",
        )
    except Exception as e:
        logger.error(f"Failed to generate interview prep: {e}")
        return {
            "technical_questions": [
                f"Explain how you would architect a production solution for {job.title}.",
                "How do you ensure data quality and latency guarantees in your pipelines?",
            ],
            "dsa_topics": ["Arrays & Two Pointers", "Hash Maps & Frequency Counters", "BFS / DFS"],
            "resume_based_questions": [
                "Walk me through the most technically challenging project on your resume.",
                "How did you measure the business impact of your machine learning models?",
            ],
            "company_specific_points": [
                f"Research {job.company}'s core business model and engineering tech stack.",
                "Review recent engineering blog posts or open source tools released by the company.",
            ],
        }
