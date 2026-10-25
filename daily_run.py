"""GitHub Actions scheduled daily digest runner for JobRadar."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from typing import Optional
from telegram import Bot

from job_radar.config import settings
from job_radar.store import store
from job_radar.jobs_pipeline import fetch_all_jobs, process_and_deliver_jobs_for_user

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("daily_run")


async def notify_owner_of_error(bot: Optional[Bot], error_msg: str) -> None:
    """Send alert notification to owner if OWNER_CHAT_ID is configured."""
    if not settings.owner_chat_id or not bot:
        return
    try:
        await bot.send_message(
            chat_id=settings.owner_chat_id,
            text=f"🚨 <b>JobRadar Daily Run Error Alert</b>\n\n<code>{error_msg}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Failed to alert owner {settings.owner_chat_id}: {e}")


async def run_daily_pipeline(dry_run: bool = False, specific_user_id: Optional[int] = None) -> int:
    """Execute the daily ingestion, matching, and distribution loop."""
    run_id = str(uuid.uuid4())[:8]
    logger.info(f"=== Starting JobRadar Daily Run (run_id={run_id}, dry_run={dry_run}) ===")

    # Fail loudly at startup if missing required credentials in production
    if not dry_run:
        settings.validate_secrets(require_telegram=True, require_mongo=True, require_llm=True)

    bot: Optional[Bot] = None
    if settings.telegram_bot_token and not dry_run:
        bot = Bot(token=settings.telegram_bot_token)

    try:
        # Determine target active users
        if specific_user_id:
            active_users = [specific_user_id]
        else:
            active_users = store.get_active_users()

        logger.info(f"Targeting {len(active_users)} active user(s) for daily digest.")
        if not active_users:
            logger.info("No active users found. Finishing run.")
            store.log_run(run_id, {"status": "skipped", "reason": "no_active_users", "users_count": 0})
            return 0

        # Cost-saving optimization: Fetch and deduplicate all fresh jobs once
        all_jobs = await fetch_all_jobs()
        logger.info(f"Candidate pool contains {len(all_jobs)} deduplicated jobs.")

        total_delivered = 0
        user_deliveries = {}

        # Loop through users and perform personalized 2-stage matching
        for uid in active_users:
            try:
                matches = await process_and_deliver_jobs_for_user(
                    user_id=uid,
                    candidate_jobs=all_jobs,
                    bot=bot,
                    force=False,
                    dry_run=dry_run,
                )
                delivered_count = len(matches)
                total_delivered += delivered_count
                user_deliveries[str(uid)] = delivered_count
                logger.info(f"User {uid}: successfully delivered {delivered_count} job(s).")
            except Exception as e:
                logger.error(f"Error processing user {uid}: {e}", exc_info=True)
                user_deliveries[str(uid)] = f"error: {str(e)}"

        summary = {
            "status": "success",
            "users_processed": len(active_users),
            "jobs_fetched": len(all_jobs),
            "total_delivered": total_delivered,
            "deliveries": user_deliveries,
        }
        store.log_run(run_id, summary)
        logger.info(f"=== Daily Run Finished Successfully: {total_delivered} jobs delivered ===")
        return 0

    except Exception as e:
        error_msg = f"Fatal daily run failure: {e}"
        logger.error(error_msg, exc_info=True)
        store.log_run(run_id, {"status": "failed", "error": str(e)})
        if bot:
            await notify_owner_of_error(bot, str(e))
        return 1


def main():
    parser = argparse.ArgumentParser(description="JobRadar Daily Scheduled Digest Runner")
    parser.add_argument("--dry-run", action="store_true", help="Run without sending messages or writing to DB")
    parser.add_argument("--user-id", type=int, default=None, help="Process a single specific user ID")
    args = parser.parse_args()

    is_dry = args.dry_run or settings.dry_run
    exit_code = asyncio.run(run_daily_pipeline(dry_run=is_dry, specific_user_id=args.user_id))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
