"""Regression tests for /api/stream_data auth when a web_viewer_password is set (#27).

Production runs with web_viewer_password configured, so the `require_auth`
before_request is active. The bot posts to /api/stream_data with an X-Stream-Token
(shared secret) and no session cookie. Before the fix, require_auth 401'd that POST
before the route's own token check ran — silently killing the live packet/command
push. These tests lock in that a valid token gets through while wrong/missing tokens
are still rejected.
"""

from configparser import ConfigParser
from unittest.mock import patch

import pytest

TOKEN = "a" * 64  # stand-in for secrets.token_hex(32)
PASSWORD = "s3cr3t-viewer-pw"


@pytest.fixture
def viewer_pw(tmp_path):
    """A real BotDataViewer with a web_viewer_password set and TESTING off,
    so require_auth (session gate) and the route token check are both live."""
    from modules.web_viewer.app import BotDataViewer

    config = ConfigParser()
    config.add_section("Bot")
    config.set("Bot", "db_path", str(tmp_path / "meshcore_bot.db"))
    config.add_section("Web_Viewer")
    config.set("Web_Viewer", "host", "127.0.0.1")
    config.set("Web_Viewer", "port", "8080")
    config.set("Web_Viewer", "enabled", "false")
    config.set("Web_Viewer", "auto_start", "false")
    config.set("Web_Viewer", "debug", "false")
    config.set("Web_Viewer", "cors_allowed_origins", "*")
    config.set("Web_Viewer", "web_viewer_password", PASSWORD)

    config_path = str(tmp_path / "config.ini")
    with open(config_path, "w") as f:
        config.write(f)
    db_path = str(tmp_path / "meshcore_bot.db")

    with patch.object(BotDataViewer, "_start_database_polling"), \
         patch.object(BotDataViewer, "_start_log_tailing"), \
         patch.object(BotDataViewer, "_start_cleanup_scheduler"), \
         patch.object(BotDataViewer, "_setup_socketio_handlers"), \
         patch("modules.web_viewer.app.RepeaterManager"):
        viewer = BotDataViewer(db_path=db_path, config_path=config_path)

    # TESTING stays False so the route's own token check is NOT bypassed.
    viewer.app.config["TESTING"] = False
    # Seed the shared secret the bot would have stored.
    viewer.db_manager.set_metadata("internal.stream_token", TOKEN)
    return viewer


def _post(client, headers):
    base = {"X-Requested-With": "BotIntegration"}  # satisfy CSRF (bot sets this too)
    base.update(headers)
    return client.post(
        "/api/stream_data",
        json={"type": "packet", "data": {"header": "0x11", "hops": 0}},
        headers=base,
    )


class TestStreamDataAuthWithPassword:
    def test_valid_token_is_accepted(self, viewer_pw):
        """The bot's token-authenticated POST must get through require_auth."""
        client = viewer_pw.app.test_client()
        resp = _post(client, {"X-Stream-Token": TOKEN})
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_missing_token_is_rejected(self, viewer_pw):
        client = viewer_pw.app.test_client()
        resp = _post(client, {})
        assert resp.status_code == 401

    def test_wrong_token_is_rejected(self, viewer_pw):
        client = viewer_pw.app.test_client()
        resp = _post(client, {"X-Stream-Token": "b" * 64})
        assert resp.status_code == 401

    def test_other_api_route_still_requires_session(self, viewer_pw):
        """Sanity: the password gate is genuinely active for ordinary API routes."""
        client = viewer_pw.app.test_client()
        resp = client.get("/api/recent_commands")
        assert resp.status_code == 401
