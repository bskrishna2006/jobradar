"""Resume text extraction and structured parsing via LLM."""

from __future__ import annotations

import io
import logging
import re
from typing import Optional
from pypdf import PdfReader
from docx import Document

from job_radar.llm import BaseLLMClient, get_llm_client
from job_radar.models import UserProfile

logger = logging.getLogger(__name__)

MAX_RESUME_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB


def validate_resume_file(filename: str, file_size: int) -> None:
    """Validate resume format and file size limit."""
    if file_size > MAX_RESUME_SIZE_BYTES:
        raise ValueError(f"File size exceeds maximum allowed 5 MB (size: {file_size / (1024*1024):.1f} MB).")

    allowed_exts = (".pdf", ".docx", ".txt")
    if not any(filename.lower().endswith(ext) for ext in allowed_exts):
        raise ValueError("Unsupported format. Please send your resume as a PDF, DOCX, or TXT file.")


def extract_text_from_bytes(file_bytes: bytes, filename: str) -> str:
    """Extract clean text content from PDF, DOCX, or TXT file bytes."""
    fname = filename.lower()
    text = ""

    if fname.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(file_bytes))
        pages_text = []
        for idx, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            pages_text.append(page_text)
        text = "\n".join(pages_text)

    elif fname.endswith(".docx"):
        doc = Document(io.BytesIO(file_bytes))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n".join(paragraphs)

    elif fname.endswith(".txt"):
        text = file_bytes.decode("utf-8", errors="ignore")

    else:
        raise ValueError("Unsupported file format for text extraction.")

    # Clean whitespace and strip control characters
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 50:
        raise ValueError("Could not extract sufficient text from the file. Please ensure it is not a scanned image.")

    return text


PARSE_RESUME_PROMPT = """
You are an expert technical recruiter and resume analyzer.
Analyze the following resume text and extract a structured profile JSON.

Output JSON matching this exact structure:
{{
  "skills": ["Python", "SQL", "Machine Learning", "PyTorch"],
  "experience_level": "fresher",
  "years_of_experience": 1.0,
  "education": ["B.Tech in Computer Science, ABC University, 2024"],
  "projects": ["Brief title/summary of key project 1", "Brief title/summary of key project 2"],
  "preferred_roles": ["ML Engineer", "Data Scientist", "Data Analyst"],
  "summary": "2-line summary of candidate's key strengths and specialization"
}}

Resume text:
\"\"\"{resume_text}\"\"\"
"""


COMMON_TECH_SKILLS = [
    "Python", "SQL", "Machine Learning", "Deep Learning", "PyTorch", "TensorFlow",
    "Pandas", "NumPy", "Scikit-Learn", "Docker", "Kubernetes", "AWS", "GCP", "Azure",
    "FastAPI", "Flask", "Django", "Git", "C++", "Java", "JavaScript", "TypeScript",
    "React", "Node.js", "NLP", "Computer Vision", "Linux", "R", "Tableau", "Power BI",
    "Spark", "Hadoop", "PostgreSQL", "MySQL", "MongoDB", "Redis", "Kafka", "Data Engineering",
    "Data Analysis", "Statistics", "Data Mining", "MLOps", "LLMs", "Generative AI", "LangChain",
]


def parse_resume_heuristics(text: str) -> Dict[str, Any]:
    """Robust local regex and keyword extractor for resume parsing fallback."""
    text_lower = text.lower()

    # 1. Skills extraction
    found_skills = []
    for s in COMMON_TECH_SKILLS:
        pattern = r"\b" + re.escape(s.lower()) + r"\b"
        if re.search(pattern, text_lower):
            found_skills.append(s)

    # 2. Experience level and YoE
    yoe = 0.0
    yoe_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:\+)?\s*(?:years?|yrs?)\s*(?:of)?\s*experience", text_lower)
    if yoe_match:
        try:
            yoe = float(yoe_match.group(1))
        except ValueError:
            yoe = 0.0

    if yoe >= 5.0 or "senior" in text_lower or "lead" in text_lower:
        exp_level = "senior"
    elif yoe >= 2.0 or "mid" in text_lower:
        exp_level = "mid"
    elif "junior" in text_lower:
        exp_level = "junior"
    else:
        exp_level = "fresher"

    # 3. Education extraction (proper word boundaries to avoid matching 'be', 'tube', etc.)
    education = []
    edu_patterns = [
        r"\b(b\.?tech\b[^\n,.]*|bachelor\s+of[^\n,.]*|b\.?e\.?\s+(?:in|of)[^\n,.]*|m\.?tech\b[^\n,.]*|master\s+of[^\n,.]*|b\.?sc\b[^\n,.]*|b\.?s\.?\s+in[^\n,.]*)",
    ]
    for pat in edu_patterns:
        m = re.findall(pat, text, flags=re.IGNORECASE)
        for match_str in m:
            clean_m = match_str.strip()
            # Guard against common words like 'be '
            if len(clean_m) > 6 and not clean_m.lower().startswith("be ") and clean_m not in education:
                education.append(clean_m)

    if not education:
        # Check for degree keywords in general
        if "computer science" in text_lower:
            education.append("B.Tech / B.E. in Computer Science")
        elif "engineering" in text_lower:
            education.append("Bachelor of Engineering")
        else:
            education.append("Bachelor's Degree")

    # 4. Project extraction
    projects = []
    # Search for project headlines or section
    proj_section = re.search(
        r"(?:projects?|academic projects?|key projects?)\s*[:\n](.*?)(?=\n\s*(?:education|experience|work history|skills|certifications|awards|\Z))",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if proj_section:
        raw_p = proj_section.group(1).strip()
        lines = [line.strip("-•* \t") for line in raw_p.split("\n") if len(line.strip("-•* \t")) > 6]
        for l in lines:
            # Filter out description lines and keep project titles
            if not any(k in l.lower() for k in ["technologies:", "tech stack:", "built a", "developed a", "http", "github"]):
                clean_l = l.split("—")[0].split("-")[0].split(":")[0].strip()
                if len(clean_l) > 4 and clean_l not in projects:
                    projects.append(clean_l[:80])
            if len(projects) >= 3:
                break

    if not projects:
        # Fallback project titles inferred from skills
        if "machine learning" in text_lower or "python" in text_lower:
            projects = ["Machine Learning & Data Modeling Pipeline", "Full-Stack Python Application"]
        else:
            projects = ["Technical Engineering Project"]

    # 5. Preferred roles inferred from skill overlap
    preferred_roles = []
    if any(k in text_lower for k in ["machine learning", "pytorch", "tensorflow", "deep learning", "ai"]):
        preferred_roles.extend(["ML Engineer", "Data Scientist"])
    if any(k in text_lower for k in ["data analyst", "sql", "tableau", "power bi", "pandas"]):
        preferred_roles.append("Data Analyst")
    if any(k in text_lower for k in ["python", "fastapi", "django", "backend"]):
        preferred_roles.append("Python Backend Engineer")
    if not preferred_roles:
        preferred_roles = ["ML Engineer", "Data Analyst", "Data Scientist"]

    return {
        "skills": found_skills[:20] or ["Python", "SQL", "Machine Learning"],
        "experience_level": exp_level,
        "years_of_experience": yoe,
        "education": education[:2],
        "projects": projects[:3],
        "preferred_roles": list(dict.fromkeys(preferred_roles))[:4],
        "summary": f"{exp_level.capitalize()} developer skilled in {', '.join(found_skills[:4]) or 'Python & data analytics'}.",
    }


async def parse_resume_to_profile(
    user_id: int,
    resume_text: str,
    llm: Optional[BaseLLMClient] = None,
) -> UserProfile:
    """
    Parse resume text using LLM into a structured UserProfile.
    Crucially: NEVER logs the resume text itself to maintain privacy.
    Falls back gracefully to local heuristic parser if LLM endpoint fails.
    """
    client = llm or get_llm_client()
    prompt = PARSE_RESUME_PROMPT.format(resume_text=resume_text[:6000])

    try:
        data = await client.complete_json(
            prompt=prompt,
            system_prompt="You are a strict, structured resume parser. Always output valid JSON only.",
        )
    except Exception as e:
        logger.warning(f"LLM API parse failed: {e}. Using intelligent fallback parser.")
        data = parse_resume_heuristics(resume_text)

    # Normalize fields
    skills = [str(s).strip() for s in data.get("skills", []) if str(s).strip()]
    exp_level = str(data.get("experience_level", "fresher")).lower()
    if exp_level not in ("fresher", "junior", "mid", "senior"):
        exp_level = "fresher"

    yoe = float(data.get("years_of_experience", 0.0) or 0.0)
    education = [str(e).strip() for e in data.get("education", []) if str(e).strip()]
    projects = [str(p).strip() for p in data.get("projects", []) if str(p).strip()]
    preferred_roles = [str(r).strip() for r in data.get("preferred_roles", []) if str(r).strip()]
    summary = str(data.get("summary", "")).strip()

    # Form a clean consolidated summary for matching embeddings
    summary_for_matching = (
        f"Roles: {', '.join(preferred_roles)}. "
        f"Skills: {', '.join(skills)}. "
        f"Experience Level: {exp_level} ({yoe} years). "
        f"Summary: {summary}. "
        f"Projects: {'; '.join(projects[:3])}."
    )

    return UserProfile(
        user_id=user_id,
        skills=skills,
        experience_level=exp_level,
        years_of_experience=yoe,
        education=education,
        projects=projects,
        preferred_roles=preferred_roles,
        resume_summary_text=summary_for_matching,
    )
