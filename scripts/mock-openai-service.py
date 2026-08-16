#!/usr/bin/env python3
"""Bounded OpenAI-compatible mock for local deployment acceptance only.

All outputs from this service are simulated and must never be presented as
model-quality evidence.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import time


MAX_REQUEST_BYTES = 64 * 1024


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/healthz":
            self.send_error(404)
            return
        body = b'{"status":"simulated-ready"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if not 0 < length <= MAX_REQUEST_BYTES:
            self.send_error(413)
            return
        try:
            request = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_error(400)
            return
        model = request.get("model")
        if model == "simulated-slow":
            time.sleep(2)
        if model == "simulated-malformed":
            content = "not-json"
        else:
            content = json.dumps(
                {
                    "profile": "medium",
                    "reasons": ["Simulated mock selected bounded medium resources."],
                    "score": 50,
                    "image_id": "minimal-python",
                    "image_reasons": ["Simulated mock selected the catalog default."],
                }
            )
        body = json.dumps(
            {
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 8,
                    "total_tokens": 20,
                },
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
