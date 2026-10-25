"""Run JobRadar Telegram Bot locally in long-polling mode with full debug logging and error handling."""

import logging
from telegram import Update
from telegram.ext import Application, ContextTypes
from telegram.request import HTTPXRequest

from job_radar.config import settings
from job_radar.bot.handlers import register_handlers

logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("local_bot")


import html


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log any unhandled exception in bot handlers and notify user if possible."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            err_text = html.escape(str(context.error))
            await update.effective_message.reply_text(
                f"⚠️ An error occurred while processing your request:\n<code>{err_text[:3000]}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass


async def on_startup(app: Application) -> None:
    me = await app.bot.get_me()
    print("======================================================")
    print(f"✅ CONNECTED TO BOT: @{me.username} ({me.first_name})")
    print(f"👉 OPEN THIS LINK IN TELEGRAM: https://t.me/{me.username}")
    print("======================================================")


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

    # Process all pending updates
    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
