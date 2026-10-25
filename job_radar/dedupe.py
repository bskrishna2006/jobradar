"""Deduplication logic across multiple sources and runs."""

from __future__ import annotations

import logging
from typing import Dict, List, Set, Tuple
from job_radar.models import Job

logger = logging.getLogger(__name__)


def deduplicate_jobs(jobs: List[Job]) -> List[Job]:
    """
    Deduplicate a list of jobs across sources using:
    1. Key 1: Primary ID (source:id or canonical URL)
    2. Key 2: Content Hash (hash of lowercase company + title + location)

    If a duplicate is encountered across boards, preserves the richer posting
    (preferring longer description and richer skill tags).
    """
    seen_ids: Set[str] = set()
    seen_hashes: Dict[str, Job] = {}
    deduped: List[Job] = []

    for job in jobs:
        # Check primary ID
        if job.id in seen_ids:
            continue

        # Check content hash across different sources/boards
        if job.content_hash in seen_hashes:
            existing = seen_hashes[job.content_hash]
            # Replace existing if current job has significantly richer description
            if len(job.description) > len(existing.description) + 50 or (len(job.skills) > len(existing.skills)):
                # Replace in deduped list
                idx = deduped.index(existing)
                deduped[idx] = job
                seen_hashes[job.content_hash] = job
                seen_ids.add(job.id)
            continue

        seen_ids.add(job.id)
        seen_hashes[job.content_hash] = job
        deduped.append(job)

    logger.info(f"Deduplication: {len(jobs)} jobs reduced to {len(deduped)} unique postings.")
    return deduped
