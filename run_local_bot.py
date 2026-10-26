"""Run JobRadar Telegram Bot locally in long-polling mode with 2-hour scheduled delivery."""

from __future__ import annotations

import asyncio
import html
import logging
from telegram import Update
from telegram.ext import Application, ContextTypes
from telegram.request import HTTPXRequest

from job_radar.config import settings
from job_radar.store import store
from job_radar.jobs_pipeline import fetch_all_jobs, process_and_deliver_jobs_for_user
from job_radar.bot.handlers import register_handlers

logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("local_bot")

TWO_HOURS_IN_SECONDS = 7200  # 2 hours interval


async def periodic_job_radar_loop(app: Application, interval_seconds: int = TWO_HOURS_IN_SECONDS) -> None:
    """
    Background worker that runs every 2 hours:
    1. Scans job boards (freehire.me, greenhouse, lever, ashby).
    2. Runs multi-factor matching against candidate preferences & resume.
    3. Delivers fresh, new unseen direct openings to each active user.
    """
    logger.info(f"⏰ Initialized 2-Hour JobRadar periodic loop (interval: {interval_seconds}s).")
    # Initial grace delay after bot startup before the first automated check
    await asyncio.sleep(20)

    while True:
        try:
            active_uids = store.get_active_users()
            eligible_users = []
            for uid in active_uids:
                profile = store.get_profile(uid)
                prefs = store.get_preferences(uid)
                if profile and prefs.onboarding_completed and not prefs.is_paused:
                    eligible_users.append(uid)

            if eligible_users:
                logger.info(f"⏰ [2-Hour Periodic JobRadar] Scanning openings for {len(eligible_users)} active user(s)...")
                candidate_jobs = await fetch_all_jobs()

                for uid in eligible_users:
                    try:
                        matches = await process_and_deliver_jobs_for_user(
                            user_id=uid,
                            candidate_jobs=candidate_jobs,
                            bot=app.bot,
                            force=False,
                            is_periodic=True,
                        )
                        if matches:
                            logger.info(f"⏰ Delivered {len(matches)} new matching jobs to user {uid}.")
                    except Exception as err:
                        logger.error(f"Error delivering periodic jobs to user {uid}: {err}", exc_info=True)
            else:
                logger.info("⏰ [2-Hour Periodic JobRadar] No active users with completed profile. Waiting for next cycle.")

        except Exception as e:
            logger.error(f"Error in periodic_job_radar_loop cycle: {e}", exc_info=True)

        logger.info(f"⏰ [2-Hour Periodic JobRadar] Cycle completed. Sleeping for {interval_seconds}s...")
        await asyncio.sleep(interval_seconds)


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log any unhandled exception in bot handlers, send friendly user message, and alert owner."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Sorry, an unexpected error occurred while processing your request. "
                "Our team has been alerted. Please try again shortly."
            )
        except Exception:
            pass

    # If OWNER_CHAT_ID is configured, send the diagnostic trace privately to the bot administrator
    if settings.owner_chat_id and context.bot and context.error:
        try:
            err_text = html.escape(str(context.error))
            await context.bot.send_message(
                chat_id=settings.owner_chat_id,
                text=f"🚨 <b>JobRadar Exception Alert</b>\n\n<code>{err_text[:2000]}</code>",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Could not deliver error alert to OWNER_CHAT_ID: {e}")


async def on_startup(app: Application) -> None:
    """Invoked when the Telegram application has initialized."""
    me = await app.bot.get_me()
    print("======================================================")
    print(f"✅ CONNECTED TO BOT: @{me.username} ({me.first_name})")
    print(f"👉 OPEN THIS LINK IN TELEGRAM: https://t.me/{me.username}")
    print("⏰ Periodic 2-hour job matcher: ACTIVE")
    print("======================================================")

    # Launch periodic 2-hour matching worker in background
    asyncio.create_task(periodic_job_radar_loop(app, interval_seconds=TWO_HOURS_IN_SECONDS))


def main():
    if not settings.telegram_bot_token:
        print("❌ Error: TELEGRAM_BOT_TOKEN is empty in your .env file!")
        return

    print("======================================================")
    print("🤖 Starting JobRadar Bot in local polling mode...")
    print(f"👉 Token: {settings.telegram_bot_token[:10]}...")
    print("======================================================")

    request_config = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .request(request_config)
        .post_init(on_startup)
        .build()
    )

    register_handlers(application)
    application.add_error_handler(global_error_handler)

    # Process pending updates and start polling
    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
