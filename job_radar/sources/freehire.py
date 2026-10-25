"""Freehire API adapter source."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from job_radar.config import settings
from job_radar.models import Job
from job_radar.sources.base import JobSource, strip_html

logger = logging.getLogger(__name__)


class FreehireSource(JobSource):
    """
    Adapter for freehire.me public API.
    Uses /agent/jobs/search (or /jobs/search fallback) with structured filtering.
    """

    name: str = "freehire"

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or settings.freehire_base_url).rstrip("/")

    async def fetch_jobs(
        self,
        query: Optional[str] = None,
        country: Optional[str] = None,
        skills: Optional[List[str]] = None,
        seniority: Optional[str] = None,
        posted_within_days: Optional[int] = None,
        limit: int = 50,
    ) -> List[Job]:
        # Prefer the agent search endpoint which provides full descriptions
        url = f"{self.base_url}/agent/jobs/search"
        params: Dict[str, Any] = {
            "limit": min(limit, 100),
            "sort": "posted_at",
            "order": "desc",
            "description_format": "text",
        }

        if query:
            params["q"] = query
        if country:
            params["countries"] = country
        if skills:
            params["skills"] = ",".join(s.strip().lower() for s in skills[:8])
        if seniority:
            # Map human seniority to freehire values: intern, junior, middle, senior, lead, staff
            s_map = {
                "entry-level": "junior",
                "fresher": "junior",
                "intern": "intern",
                "junior": "junior",
                "mid-level": "middle",
                "senior": "senior",
            }
            params["seniority"] = s_map.get(seniority.lower(), seniority.lower())
        if posted_within_days:
            params["posted_within_days"] = posted_within_days

        logger.info(f"[freehire] Fetching from {url} with params {params}")
        resp = await self._safe_get_json(url, params=params)

        # Fallback to /jobs/search if /agent/jobs/search returned 404 or failed
        if not resp or "data" not in resp:
            fallback_url = f"{self.base_url}/jobs/search"
            params.pop("description_format", None)
            logger.info(f"[freehire] Retrying on fallback endpoint {fallback_url}")
            resp = await self._safe_get_json(fallback_url, params=params)

        if not resp or "data" not in resp:
            logger.warning("[freehire] No job data returned from API.")
            return []

        jobs: List[Job] = []
        raw_items = resp.get("data", [])
        if not isinstance(raw_items, list):
            return []

        for item in raw_items:
            try:
                job = self._map_to_job(item)
                if job:
                    jobs.append(job)
            except Exception as e:
                logger.debug(f"[freehire] Error parsing item {item.get('public_slug')}: {e}")

        logger.info(f"[freehire] Successfully retrieved {len(jobs)} jobs.")
        return jobs

    def _map_to_job(self, item: Dict[str, Any]) -> Optional[Job]:
        title = item.get("title", "").strip()
        company = item.get("company", "").strip()
        url = item.get("url", "").strip()
        if not title or not company or not url:
            return None

        public_slug = item.get("public_slug") or ""
        external_id = str(item.get("external_id") or public_slug)

        # Location parsing
        loc = item.get("location") or ""
        if not loc:
            cities = item.get("cities") or []
            countries = item.get("countries") or []
            loc_parts = []
            if cities:
                loc_parts.append(", ".join(cities))
            if countries:
                loc_parts.append(", ".join(countries))
            loc = " - ".join(loc_parts) or "Remote"

        # Enrichment block
        enrichment = item.get("enrichment") or {}
        seniority = enrichment.get("seniority") or "unspecified"
        category = enrichment.get("category") or "other"
        skills = item.get("skills") or enrichment.get("skills") or []
        salary_min = enrichment.get("salary_min")
        salary_max = enrichment.get("salary_max")
        salary_currency = enrichment.get("salary_currency")

        desc = strip_html(item.get("description") or enrichment.get("summary") or "")

        # Posted timestamp
        posted_at = None
        raw_posted = item.get("posted_at") or item.get("created_at")
        if raw_posted:
            try:
                posted_at = datetime.fromisoformat(raw_posted.replace("Z", "+00:00"))
            except Exception:
                posted_at = None

        return Job(
            id=f"freehire:{external_id}",
            source="freehire",
            external_id=external_id,
            title=title,
            company=company,
            location=loc,
            url=url,
            description=desc,
            skills=[str(s) for s in skills],
            seniority=str(seniority),
            category=str(category),
            work_mode=str(item.get("work_mode", "unspecified")),
            country=(item.get("countries") or [None])[0],
            salary_min=float(salary_min) if salary_min is not None else None,
            salary_max=float(salary_max) if salary_max is not None else None,
            salary_currency=salary_currency,
            posted_at=posted_at,
        )
