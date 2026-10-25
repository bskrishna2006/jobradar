"""Vercel serverless entrypoint for JobRadar Telegram webhook."""

import asyncio
import json
from http.server import BaseHTTPRequestHandler
from job_radar.bot.webhook import handle_webhook_request


class handler(BaseHTTPRequestHandler):
    """Vercel Python serverless HTTP request handler."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "JobRadar Telegram webhook is active"}')

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)

        try:
            body = json.loads(post_data.decode("utf-8")) if post_data else {}
        except Exception:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Invalid JSON body"}')
            return

        headers_dict = {k: v for k, v in self.headers.items()}
        status_code, resp_body = asyncio.run(handle_webhook_request(headers_dict, body))

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(resp_body).encode("utf-8"))
