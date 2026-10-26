"""Unit tests for Telegram webhook secret token validation."""

from job_radar.bot.webhook import verify_secret_token
from job_radar.config import settings


def test_verify_secret_token():
    original_secret = settings.telegram_webhook_secret
    try:
        settings.telegram_webhook_secret = "super_secret_webhook_token_123"

        # Correct header
        valid_headers = {"X-Telegram-Bot-Api-Secret-Token": "super_secret_webhook_token_123"}
        assert verify_secret_token(valid_headers) is True

        # Case-insensitive header lookup
        valid_headers_lower = {"x-telegram-bot-api-secret-token": "super_secret_webhook_token_123"}
        assert verify_secret_token(valid_headers_lower) is True

        # Wrong secret token
        invalid_headers = {"X-Telegram-Bot-Api-Secret-Token": "wrong_token"}
        assert verify_secret_token(invalid_headers) is False

        # Missing header
        empty_headers = {}
        assert verify_secret_token(empty_headers) is False

    finally:
        settings.telegram_webhook_secret = original_secret


def test_verify_secret_token_unconfigured_allows():
    original_secret = settings.telegram_webhook_secret
    try:
        settings.telegram_webhook_secret = ""
        # When unconfigured in dev, returns True
        assert verify_secret_token({}) is True
    finally:
        settings.telegram_webhook_secret = original_secret
