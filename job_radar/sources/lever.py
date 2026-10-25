"""Lever public postings API adapter."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from job_radar.config import settings
from job_radar.models import Job
from job_radar.sources.base import JobSource, strip_html

logger = logging.getLogger(__name__)


class LeverSource(JobSource):
    """
    Adapter for Lever public postings API.
    Endpoint: https://api.lever.co/v0/postings/{company}?mode=json
    """

    name: str = "lever"

    def __init__(self, companies: Optional[List[str]] = None):
        self.companies = companies or settings.lever_companies

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
            url = f"https://api.lever.co/v0/postings/{slug}"
            data = await self._safe_get_json(url, params={"mode": "json"})
            if not isinstance(data, list):
                continue

            for item in data:
                try:
                    title = item.get("text", "").strip()
                    job_id = str(item.get("id"))
                    apply_url = item.get("hostedUrl") or item.get("applyUrl") or ""
                    cats = item.get("categories") or {}
                    loc_name = cats.get("location", "Remote")

                    clean_desc = item.get("descriptionPlain") or strip_html(item.get("description", ""))

                    if query and query.lower() not in title.lower() and query.lower() not in clean_desc.lower():
                        continue

                    posted_at = None
                    raw_created = item.get("createdAt")
                    if raw_created:
                        try:
                            # Lever returns millisecond timestamp
                            posted_at = datetime.fromtimestamp(raw_created / 1000.0, tz=timezone.utc)
                        except Exception:
                            posted_at = None

                    job = Job(
                        id=f"lever:{slug}:{job_id}",
                        source="lever",
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
                    logger.debug(f"[lever] Skipping job from {slug}: {e}")

        logger.info(f"[lever] Fetched {len(all_jobs)} jobs across {len(self.companies)} companies.")
        return all_jobs
