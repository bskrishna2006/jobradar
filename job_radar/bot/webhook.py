"""Serverless webhook entrypoint for Telegram updates with secret header validation."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Tuple
from telegram import Update
from telegram.ext import Application

from job_radar.config import settings
from job_radar.bot.handlers import register_handlers

logger = logging.getLogger(__name__)

# Cached application instance
_application: Application | None = None


def verify_secret_token(headers: Dict[str, str]) -> bool:
    """Verify Telegram's X-Telegram-Bot-Api-Secret-Token header."""
    expected_secret = settings.telegram_webhook_secret.strip()
    if not expected_secret:
        # If no secret is configured, allow (e.g. in development/testing)
        return True

    # Case-insensitive header lookup
    received_secret = ""
    for k, v in headers.items():
        if k.lower() == "x-telegram-bot-api-secret-token":
            received_secret = v
            break

    return received_secret == expected_secret


async def get_bot_application() -> Application:
    """Initialize or return cached python-telegram-bot Application."""
    global _application
    if _application is None:
        from telegram.request import HTTPXRequest
        req = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
        _application = Application.builder().token(settings.telegram_bot_token).request(req).build()
        register_handlers(_application)
        await _application.initialize()
    return _application


async def handle_webhook_request(headers: Dict[str, str], body_json: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """
    Process incoming webhook payload:
    1. Verify secret token header
    2. Parse Telegram Update
    3. Process through Application handlers
    """
    if not verify_secret_token(headers):
        logger.warning("Rejected webhook request: invalid or missing secret token header.")
        return 403, {"error": "Invalid Telegram secret token header"}

    try:
        app = await get_bot_application()
        update = Update.de_json(body_json, app.bot)
        if update:
            await app.process_update(update)
        return 200, {"status": "ok"}
    except Exception as e:
        logger.error(f"Error processing webhook update: {e}", exc_info=True)
        return 500, {"error": str(e)}
