"""Ashby public job-board API adapter."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from job_radar.config import settings
from job_radar.models import Job
from job_radar.sources.base import JobSource, strip_html

logger = logging.getLogger(__name__)


class AshbySource(JobSource):
    """
    Adapter for Ashby public job board API.
    Endpoint: https://api.ashbyhq.com/posting-api/job-board/{company}
    """

    name: str = "ashby"

    def __init__(self, companies: Optional[List[str]] = None):
        self.companies = companies or settings.ashby_companies

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
            url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
            data = await self._safe_get_json(url)
            if not data or "jobs" not in data:
                continue

            for item in data.get("jobs", []):
                try:
                    title = item.get("title", "").strip()
                    job_id = str(item.get("id"))
                    apply_url = item.get("jobUrl", "").strip()
                    loc_name = item.get("location", "Remote")
                    clean_desc = item.get("descriptionPlain") or strip_html(item.get("descriptionHtml", ""))

                    if query and query.lower() not in title.lower() and query.lower() not in clean_desc.lower():
                        continue

                    posted_at = None
                    raw_published = item.get("publishedAt")
                    if raw_published:
                        try:
                            posted_at = datetime.fromisoformat(raw_published.replace("Z", "+00:00"))
                        except Exception:
                            posted_at = None

                    job = Job(
                        id=f"ashby:{slug}:{job_id}",
                        source="ashby",
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
                    logger.debug(f"[ashby] Skipping job from {slug}: {e}")

        logger.info(f"[ashby] Fetched {len(all_jobs)} jobs across {len(self.companies)} companies.")
        return all_jobs
