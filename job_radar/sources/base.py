"""Abstract base class and utilities for all JobRadar job sources."""

from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import httpx

from job_radar.models import Job

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "JobRadarBot/1.0 (Personal Job Discovery Agent; +https://github.com/jobradar)"


def strip_html(html_str: str) -> str:
    """Strip HTML tags and convert common entities to clean plain text."""
    if not html_str:
        return ""
    # Replace breaks/paragraphs with newlines
    text = re.sub(r"<(?:br|p|div|li)[^>]*>", "\n", html_str, flags=re.IGNORECASE)
    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode basic HTML entities
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    # Normalize multiple whitespace/newlines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


class JobSource(ABC):
    """Abstract interface for all job boards and API adapters."""

    name: str = "base"

    @abstractmethod
    async def fetch_jobs(
        self,
        query: Optional[str] = None,
        country: Optional[str] = None,
        skills: Optional[List[str]] = None,
        seniority: Optional[str] = None,
        posted_within_days: Optional[int] = None,
    ) -> List[Job]:
        """Fetch jobs matching search criteria and return normalized Job models."""
        pass

    async def _safe_get_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 15.0,
        retries: int = 2,
    ) -> Optional[Any]:
        """Fetch JSON endpoint with retries and exponential backoff."""
        req_headers = {"User-Agent": DEFAULT_USER_AGENT}
        if headers:
            req_headers.update(headers)

        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    resp = await client.get(url, params=params, headers=req_headers)
                    if resp.status_code == 429:
                        retry_after = float(resp.headers.get("Retry-After", "2"))
                        logger.warning(f"[{self.name}] Rate limited (429). Backing off {retry_after}s.")
                        await asyncio.sleep(retry_after)
                        continue
                    resp.raise_for_status()
                    return resp.json()
            except httpx.HTTPStatusError as e:
                logger.warning(f"[{self.name}] HTTP status {e.response.status_code} for {url} (attempt {attempt+1}/{retries+1})")
                if attempt == retries:
                    return None
            except Exception as e:
                logger.warning(f"[{self.name}] Request failed for {url}: {e} (attempt {attempt+1}/{retries+1})")
                if attempt == retries:
                    return None

            await asyncio.sleep(1.0 * (2 ** attempt))

        return None
