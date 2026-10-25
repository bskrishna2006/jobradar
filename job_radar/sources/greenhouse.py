"""Greenhouse public job-board API adapter."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from job_radar.config import settings
from job_radar.models import Job
from job_radar.sources.base import JobSource, strip_html

logger = logging.getLogger(__name__)


class GreenhouseSource(JobSource):
    """
    Adapter for Greenhouse public board API.
    Docs: https://developers.greenhouse.io/job-board.html
    Endpoint: https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true
    """

    name: str = "greenhouse"

    def __init__(self, companies: Optional[List[str]] = None):
        self.companies = companies or settings.greenhouse_companies

    async def fetch_jobs(
        self,
        query: Optional[str] = None,
        country: Optional[str] = None,
        skills: Optional[List[str]] = None,
        seniority: Optional[str] = None,
        posted_within_days: Optional[int] = None,
    ) -> List[Job]:
        all_jobs: List[Job] = []

        for slug in self.companies:
            slug = slug.strip().lower()
            if not slug:
                continue
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
            data = await self._safe_get_json(url, params={"content": "true"})
            if not data or "jobs" not in data:
                continue

            raw_jobs = data.get("jobs", [])
            for item in raw_jobs:
                try:
                    title = item.get("title", "").strip()
                    job_id = str(item.get("id"))
                    apply_url = item.get("absolute_url", "").strip()
                    loc_obj = item.get("location") or {}
                    loc_name = loc_obj.get("name", "Remote") if isinstance(loc_obj, dict) else str(loc_obj)

                    raw_content = item.get("content", "")
                    clean_desc = strip_html(raw_content)

                    # Basic query filtering if specified
                    if query and query.lower() not in title.lower() and query.lower() not in clean_desc.lower():
                        continue

                    posted_at = None
                    raw_updated = item.get("updated_at")
                    if raw_updated:
                        try:
                            posted_at = datetime.fromisoformat(raw_updated.replace("Z", "+00:00"))
                        except Exception:
                            posted_at = None

                    job = Job(
                        id=f"greenhouse:{slug}:{job_id}",
                        source="greenhouse",
                        external_id=job_id,
                        title=title,
                        company=slug.capitalize(),
                        location=loc_name or "Remote",
                        url=apply_url,
                        description=clean_desc,
                        posted_at=posted_at,
                    )
                    all_jobs.append(job)
                except Exception as e:
                    logger.debug(f"[greenhouse] Skipping job from {slug}: {e}")

        logger.info(f"[greenhouse] Fetched {len(all_jobs)} jobs across {len(self.companies)} companies.")
        return all_jobs
