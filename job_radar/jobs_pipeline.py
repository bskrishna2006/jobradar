"""Core jobs processing and notification pipeline."""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional
from telegram.error import Forbidden, RetryAfter, TelegramError

from job_radar.config import settings
from job_radar.dedupe import deduplicate_jobs
from job_radar.models import Job, MatchResult
from job_radar.matching.prefilter import prefilter_job, rank_jobs_by_embedding
from job_radar.matching.llm_scorer import score_jobs_with_llm
from job_radar.sources.base import JobSource
from job_radar.sources.freehire import FreehireSource
from job_radar.sources.greenhouse import GreenhouseSource
from job_radar.sources.lever import LeverSource
from job_radar.sources.ashby import AshbySource
from job_radar.store import store
from job_radar.bot.formatting import format_job_message, get_job_action_keyboard

logger = logging.getLogger(__name__)


def get_default_sources() -> List[JobSource]:
    """Instantiate all configured job sources."""
    return [
        FreehireSource(),
        GreenhouseSource(),
        LeverSource(),
        AshbySource(),
    ]


async def fetch_all_jobs(
    sources: Optional[List[JobSource]] = None,
    query: Optional[str] = None,
) -> List[Job]:
    """
    Fetch jobs from all sources concurrently with targeted country and domain queries.
    Per-source try/except ensures one failing source never halts the run.
    """
    if sources is not None:
        tasks = [s.fetch_jobs(query=query) for s in sources]
    else:
        freehire = FreehireSource()
        greenhouse = GreenhouseSource()
        lever = LeverSource()
        ashby = AshbySource()

        tasks = [
            # 1. Dedicated India tech hub jobs
            freehire.fetch_jobs(country="IN", limit=60),
            # 2. Targeted role streams
            freehire.fetch_jobs(query="machine learning", limit=40),
            freehire.fetch_jobs(query="ai", limit=40),
            freehire.fetch_jobs(query="python", limit=40),
            freehire.fetch_jobs(query="data", limit=40),
            freehire.fetch_jobs(query="intern", limit=40),
            # 3. Direct ATS boards
            greenhouse.fetch_jobs(query=query),
            lever.fetch_jobs(query=query),
            ashby.fetch_jobs(query=query),
        ]

    logger.info("Starting targeted job ingestion across sources...")
    results = await asyncio.gather(*tasks, return_exceptions=True)

    combined_jobs: List[Job] = []
    for res in results:
        if isinstance(res, Exception):
            logger.error(f"Job fetch task error: {res}")
        elif isinstance(res, list):
            combined_jobs.extend(res)

    # Global deduplication across all sources
    unique_jobs = deduplicate_jobs(combined_jobs)

    # Cache into global seen_jobs collection
    try:
        store.record_global_seen_jobs(unique_jobs)
    except Exception as e:
        logger.warning(f"Failed to record global seen jobs: {e}")

    logger.info(f"Total deduplicated jobs ready for pipeline: {len(unique_jobs)}")
    return unique_jobs


async def process_and_deliver_jobs_for_user(
    user_id: int,
    candidate_jobs: List[Job],
    bot: Any,
    force: bool = False,
    is_periodic: bool = False,
    dry_run: Optional[bool] = None,
) -> List[MatchResult]:
    """
    Run 2-stage matching for a single user and deliver top matches via Telegram.
    Returns list of delivered MatchResults.
    """
    is_dry = settings.dry_run if dry_run is None else dry_run

    # 1. Check profile and preferences
    profile = store.get_profile(user_id)
    if not profile:
        logger.info(f"User {user_id} has no uploaded resume profile yet.")
        if force and bot:
            await bot.send_message(
                chat_id=user_id,
                text="⚠️ Please upload your resume first so I can match jobs to your skills!\nSend your PDF, DOCX, or TXT file here.",
            )
        return []

    prefs = store.get_preferences(user_id)
    if prefs.is_paused and not force:
        logger.info(f"User {user_id} daily digest is paused. Skipping.")
        return []

    # 2. Filter out jobs this user has already received
    unseen_jobs = store.filter_unseen_jobs_for_user(user_id, candidate_jobs)
    logger.info(f"User {user_id}: {len(unseen_jobs)} unseen jobs out of {len(candidate_jobs)} total.")
    if not unseen_jobs:
        if force and bot:
            await bot.send_message(
                chat_id=user_id,
                text="🔍 No new unseen jobs found right now that match your profile. Check back later or adjust /preferences!",
            )
        return []

    # 3. Stage 1: Rule prefiltering
    prefiltered = [j for j in unseen_jobs if prefilter_job(j, prefs, profile)]
    logger.info(f"User {user_id}: {len(prefiltered)} jobs passed rule prefilter.")
    if not prefiltered:
        if force and bot:
            await bot.send_message(
                chat_id=user_id,
                text="🔍 No new jobs passed your current location/experience filters. You can widen them in /preferences.",
            )
        return []

    # 4. Stage 1b: Embedding similarity ranking -> take top 25
    ranked_top = await rank_jobs_by_embedding(prefiltered, profile, top_n=25)
    top_candidates = [job for job, _ in ranked_top]

    # 5. Stage 2: Fit scoring (LLM with smart multi-factor fallback)
    scored_matches = await score_jobs_with_llm(
        top_candidates,
        profile=profile,
        preferences=prefs,
        min_score=prefs.min_score,
    )
    logger.info(f"User {user_id}: {len(scored_matches)} jobs scored >= {prefs.min_score}%.")

    if not scored_matches:
        if force and bot:
            await bot.send_message(
                chat_id=user_id,
                text=f"🔍 Scored {len(top_candidates)} jobs, but none reached your minimum match threshold of {prefs.min_score}%. Try lowering min score in /preferences.",
            )
        return []

    # Limit to user's preferred jobs_per_digest
    digest_matches = scored_matches[: prefs.jobs_per_digest]

    # 6. Delivery via Telegram
    if is_dry:
        print(f"\n[DRY RUN] Digest for User {user_id} ({len(digest_matches)} jobs):")
        for m in digest_matches:
            print(f"- [{m.score}%] {m.job.company} - {m.job.title} ({m.job.location}) -> {m.job.url}")
        return digest_matches

    if not bot:
        return digest_matches

    try:
        # Summary intro message
        if is_periodic:
            summary_text = (
                f"⏰ <b>JobRadar 2-Hour Alert</b>\n\n"
                f"🎯 Found <b>{len(digest_matches)}</b> new opening(s) matching your profile:"
            )
        else:
            summary_text = (
                f"🎯 <b>Found {len(digest_matches)} new job matches for you!</b>\n"
                f"<i>Ranked by AI match against your profile & preferences:</i>"
            )
        await bot.send_message(chat_id=user_id, text=summary_text, parse_mode="HTML")

        # Deliver each job message with action buttons
        for match in digest_matches:
            msg_text = format_job_message(match)
            keyboard = get_job_action_keyboard(match.job.id)

            await bot.send_message(
                chat_id=user_id,
                text=msg_text,
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )

            # Record in user_jobs
            store.record_job_sent_to_user(user_id, match.job, match.score)
            # Gentle delay to respect Telegram flood limits
            await asyncio.sleep(0.3)

    except Forbidden:
        logger.warning(f"Bot was blocked by user {user_id}. Marking user as inactive.")
        store.set_user_active_status(user_id, False)
    except RetryAfter as e:
        logger.warning(f"Telegram flood control: sleeping for {e.retry_after}s.")
        await asyncio.sleep(e.retry_after)
    except TelegramError as e:
        logger.error(f"Telegram error delivering to {user_id}: {e}")

    return digest_matches
