"""Saved sample API response fixtures for unit testing without live network calls."""

SAMPLE_FREEHIRE_RESPONSE = {
    "data": [
        {
            "public_slug": "junior-ml-engineer-acme-101",
            "source": "freehire",
            "external_id": "fh-101",
            "url": "https://company.com/careers/fh-101?utm_source=freehire&ref=tracker",
            "title": "Junior Machine Learning Engineer",
            "company": "Acme AI",
            "location": "Bengaluru, India",
            "description": "We are looking for a Junior ML Engineer proficient in Python, SQL, and PyTorch to build data pipelines.",
            "countries": ["IN"],
            "work_mode": "hybrid",
            "skills": ["Python", "Machine Learning", "PyTorch", "SQL"],
            "posted_at": "2026-06-18T10:00:00Z",
            "enrichment": {
                "seniority": "junior",
                "category": "ml_ai",
                "salary_min": 800000,
                "salary_max": 1200000,
                "salary_currency": "INR",
            },
        },
        {
            "public_slug": "lead-architect-cloud-202",
            "source": "freehire",
            "external_id": "fh-202",
            "url": "https://company.com/careers/fh-202",
            "title": "Principal Cloud Architect",
            "company": "Legacy Corp",
            "location": "Remote",
            "description": "Requires 12+ years experience in distributed cloud architectures.",
            "countries": ["US"],
            "work_mode": "remote",
            "skills": ["AWS", "Terraform", "Kubernetes"],
            "posted_at": "2026-06-18T10:00:00Z",
            "enrichment": {
                "seniority": "principal",
                "category": "architecture",
            },
        },
    ],
    "meta": {"total": 2, "limit": 20, "offset": 0},
}

SAMPLE_GREENHOUSE_RESPONSE = {
    "jobs": [
        {
            "id": 881234,
            "title": "Data Analyst (Entry Level)",
            "absolute_url": "https://boards.greenhouse.io/stripe/jobs/881234?gh_src=custom",
            "location": {"name": "Remote — India"},
            "content": "<p>Stripe is hiring a Data Analyst to work with Python and SQL dashboards.</p>",
            "updated_at": "2026-06-19T08:00:00Z",
        }
    ]
}

SAMPLE_LEVER_RESPONSE = [
    {
        "id": "lever-99",
        "text": "Machine Learning Engineer - Fresher",
        "hostedUrl": "https://jobs.lever.co/postman/lever-99?lever-source=direct",
        "categories": {
            "location": "Remote",
            "team": "AI Research",
            "commitment": "Full-time",
        },
        "descriptionPlain": "Exciting entry-level role building deep learning models in Python.",
        "createdAt": 1718784000000,
    }
]

SAMPLE_ASHBY_RESPONSE = {
    "jobs": [
        {
            "id": "ashby-77",
            "title": "Data Scientist I",
            "location": "Bengaluru",
            "jobUrl": "https://jobs.ashbyhq.com/linear/ashby-77",
            "descriptionPlain": "Build predictive models using Python, Pandas, and Scikit-Learn.",
            "publishedAt": "2026-06-18T14:30:00Z",
        }
    ]
}

SAMPLE_RESUME_TEXT = """
Mani Krishna
Email: krishna@example.com | Bengaluru, India
GitHub: github.com/krishna | LinkedIn: linkedin.com/in/krishna

EDUCATION
Bachelor of Technology in Computer Science and Engineering
CGPA: 8.5 / 10 | 2020 - 2024

SKILLS
Programming: Python, SQL, C++, Bash
Machine Learning: Scikit-Learn, PyTorch, Pandas, NumPy, NLP, Data Preprocessing
Tools: Git, Docker, MongoDB, FastAPI

PROJECTS
1. Customer Churn Prediction Engine
- Developed an end-to-end ML classification pipeline predicting telecom customer churn with 89% ROC-AUC.
- Handled feature engineering, missing value imputation, and class imbalance using SMOTE in Python.

2. NLP Sentiment Analysis API
- Fine-tuned transformer models for sentiment classification on customer review datasets.
- Built a RESTful inference endpoint using FastAPI and Docker.

EXPERIENCE
Machine Learning Intern | DataTech Solutions (6 months)
- Built automated SQL extraction scripts and tabular data cleaning pipelines.
- Assisted senior engineers in training regression models for price prediction.
"""
