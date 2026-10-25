"""Unit tests for resume validation, text extraction, and LLM profile parsing."""

import pytest
from job_radar.profile.resume_parser import (
    validate_resume_file,
    extract_text_from_bytes,
    parse_resume_to_profile,
    MAX_RESUME_SIZE_BYTES,
)
from job_radar.llm import MockLLMClient
from tests.fixtures.sample_responses import SAMPLE_RESUME_TEXT


def test_validate_resume_file():
    # Valid files
    validate_resume_file("my_resume.pdf", 1024 * 500)
    validate_resume_file("resume.docx", 1024 * 200)
    validate_resume_file("cv.txt", 1024 * 50)

    # Rejects unsupported extension
    with pytest.raises(ValueError, match="Unsupported format"):
        validate_resume_file("resume.exe", 1024)

    # Rejects file > 5 MB
    with pytest.raises(ValueError, match="exceeds maximum allowed 5 MB"):
        validate_resume_file("heavy_resume.pdf", MAX_RESUME_SIZE_BYTES + 1024)


def test_extract_text_from_txt_bytes():
    raw_bytes = SAMPLE_RESUME_TEXT.encode("utf-8")
    extracted = extract_text_from_bytes(raw_bytes, "resume.txt")
    assert "Mani Krishna" in extracted
    assert "Machine Learning" in extracted


@pytest.mark.asyncio
async def test_parse_resume_to_profile_with_mock_llm():
    mock_llm = MockLLMClient()
    profile = await parse_resume_to_profile(
        user_id=987654,
        resume_text=SAMPLE_RESUME_TEXT,
        llm=mock_llm,
    )
    assert profile.user_id == 987654
    assert "Python" in profile.skills
    assert profile.experience_level == "fresher"
    assert len(profile.preferred_roles) > 0
    assert len(profile.resume_summary_text) > 20
