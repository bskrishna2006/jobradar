"""Unit tests for job source adapters using saved sample fixtures (no live network calls)."""

import pytest
from unittest.mock import AsyncMock, patch
from job_radar.sources.freehire import FreehireSource
from job_radar.sources.greenhouse import GreenhouseSource
from job_radar.sources.lever import LeverSource
from job_radar.sources.ashby import AshbySource
from tests.fixtures.sample_responses import (
    SAMPLE_FREEHIRE_RESPONSE,
    SAMPLE_GREENHOUSE_RESPONSE,
    SAMPLE_LEVER_RESPONSE,
    SAMPLE_ASHBY_RESPONSE,
)


@pytest.mark.asyncio
async def test_freehire_source_parsing():
    source = FreehireSource()
    with patch.object(source, "_safe_get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = SAMPLE_FREEHIRE_RESPONSE
        jobs = await source.fetch_jobs(country="IN")

        assert len(jobs) == 2
        j1 = jobs[0]
        assert j1.source == "freehire"
        assert j1.external_id == "fh-101"
        assert j1.title == "Junior Machine Learning Engineer"
        assert j1.company == "Acme AI"
        assert j1.location == "Bengaluru, India"
        assert j1.seniority == "junior"
        assert "utm_source" not in j1.url
        assert j1.salary_min == 800000.0


@pytest.mark.asyncio
async def test_greenhouse_source_parsing():
    source = GreenhouseSource(companies=["stripe"])
    with patch.object(source, "_safe_get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = SAMPLE_GREENHOUSE_RESPONSE
        jobs = await source.fetch_jobs()

        assert len(jobs) == 1
        j = jobs[0]
        assert j.source == "greenhouse"
        assert j.external_id == "881234"
        assert "Data Analyst" in j.title
        assert "Stripe" in j.company
        assert "Remote — India" in j.location
        assert "<p>" not in j.description  # HTML stripped


@pytest.mark.asyncio
async def test_lever_source_parsing():
    source = LeverSource(companies=["postman"])
    with patch.object(source, "_safe_get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = SAMPLE_LEVER_RESPONSE
        jobs = await source.fetch_jobs()

        assert len(jobs) == 1
        j = jobs[0]
        assert j.source == "lever"
        assert j.external_id == "lever-99"
        assert "Machine Learning Engineer" in j.title
        assert "lever-source" not in j.url


@pytest.mark.asyncio
async def test_ashby_source_parsing():
    source = AshbySource(companies=["linear"])
    with patch.object(source, "_safe_get_json", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = SAMPLE_ASHBY_RESPONSE
        jobs = await source.fetch_jobs()

        assert len(jobs) == 1
        j = jobs[0]
        assert j.source == "ashby"
        assert j.external_id == "ashby-77"
        assert j.title == "Data Scientist I"
