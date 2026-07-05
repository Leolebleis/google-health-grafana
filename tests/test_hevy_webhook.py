import http.client
import threading
import time
from http.server import HTTPServer
from unittest.mock import patch

import pytest
from hevy.webhook import WebhookHandler, check_auth


class TestCheckAuth:
    def test_matching_token(self):
        assert check_auth("Bearer s3cret", "Bearer s3cret")

    def test_wrong_token(self):
        assert not check_auth("Bearer nope", "Bearer s3cret")

    def test_missing_header(self):
        assert not check_auth(None, "Bearer s3cret")

    def test_empty_expected_token_rejects_everything(self):
        assert not check_auth("", "")
        assert not check_auth(None, "")


@pytest.fixture
def server():
    WebhookHandler.expected_token = "Bearer s3cret"
    srv = HTTPServer(("127.0.0.1", 0), WebhookHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()


def _post(srv: HTTPServer, auth: str | None) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = auth
    conn.request("POST", "/", body='{"workoutId": "abc"}', headers=headers)
    status = conn.getresponse().status
    conn.close()
    return status


def test_post_with_valid_auth_triggers_sync(server):
    with patch("hevy.webhook.run_sync") as mock_sync:
        assert _post(server, "Bearer s3cret") == 200
        # sync runs on a daemon thread; give it a beat
        for _ in range(50):
            if mock_sync.called:
                break
            time.sleep(0.01)
        mock_sync.assert_called_once()


def test_post_with_bad_auth_rejected(server):
    with patch("hevy.webhook.run_sync") as mock_sync:
        assert _post(server, "Bearer wrong") == 401
        assert not mock_sync.called


def test_post_without_auth_rejected(server):
    assert _post(server, None) == 401
