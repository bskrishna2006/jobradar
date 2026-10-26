"""Vercel serverless entrypoint for JobRadar Telegram webhook."""

import asyncio
import json
from http.server import BaseHTTPRequestHandler
from job_radar.store import store
from job_radar.config import settings
from job_radar.bot.webhook import handle_webhook_request


class handler(BaseHTTPRequestHandler):
    """Vercel Python serverless HTTP request handler."""

    def _send_json(self, status_code: int, data: dict):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_GET(self):
        """Operational health check probe."""
        db_alive = store.db is not None
        health_data = {
            "status": "healthy" if db_alive or not store._using_fallback else "degraded",
            "service": "JobRadar Telegram Webhook",
            "database": "mongodb_atlas" if db_alive else "local_store_fallback",
            "llm_provider": settings.llm_provider,
            "llm_model": settings.gemini_model if settings.llm_provider == "gemini" else "default",
        }
        self._send_json(200, health_data)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)

        try:
            body = json.loads(post_data.decode("utf-8")) if post_data else {}
        except Exception:
            self._send_json(400, {"error": "Invalid JSON body"})
            return

        headers_dict = {k: v for k, v in self.headers.items()}
        status_code, resp_body = asyncio.run(handle_webhook_request(headers_dict, body))
        self._send_json(status_code, resp_body)
