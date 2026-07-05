"""Webhook receiver for Hevy: triggers a sync when a workout is saved.

Hevy POSTs {"workoutId": ...} with a configured Authorization header when a
workout is created. This server validates the header and kicks off hevy.sync
in the background (the watermark makes syncs idempotent, so the payload body
is irrelevant). Exposed publicly via Tailscale Funnel; runs as systemd
hevy-webhook.service with the same EnvironmentFile as the sync.
"""

import logging
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

log = logging.getLogger(__name__)

_sync_lock = threading.Lock()


def check_auth(header_value: str | None, expected_token: str) -> bool:
    return bool(expected_token) and header_value == expected_token


def run_sync() -> None:
    """Run hevy.sync in a subprocess; skip if one is already running."""
    if not _sync_lock.acquire(blocking=False):
        log.info("Sync already running, skipping")
        return
    try:
        subprocess.run(
            [sys.executable, "-m", "hevy.sync"],
            check=False,
            timeout=120,
        )
    finally:
        _sync_lock.release()


class WebhookHandler(BaseHTTPRequestHandler):
    expected_token = ""

    def do_POST(self) -> None:
        if not check_auth(self.headers.get("Authorization"), self.expected_token):
            log.warning("Rejected webhook: bad or missing Authorization header")
            self.send_response(401)
            self.end_headers()
            return

        # Respond within Hevy's 5s budget; sync runs in the background.
        self.send_response(200)
        self.end_headers()
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        log.info("Webhook received: %s", body.decode(errors="replace")[:200])
        threading.Thread(target=run_sync, daemon=True).start()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 -- http.server API
        log.debug(format, *args)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    WebhookHandler.expected_token = os.environ["HEVY_WEBHOOK_TOKEN"]
    port = int(os.environ.get("WEBHOOK_PORT", "8787"))
    server = HTTPServer(("127.0.0.1", port), WebhookHandler)
    log.info("Hevy webhook receiver listening on 127.0.0.1:%s", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
