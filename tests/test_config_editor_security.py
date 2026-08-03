"""Security tests for the data-driven config editor (#28).

The editor can set ANY config value (including secrets / channel PSKs), so it must
fail closed when no web_viewer_password is configured — otherwise, on host=0.0.0.0,
anyone who can reach the port could rewrite the bot's config unauthenticated. With a
password set, require_auth enforces an authenticated session.
"""

import os
import shutil
from configparser import ConfigParser
from unittest.mock import patch

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _make_viewer(tmp_path, password=""):
    from modules.web_viewer.app import BotDataViewer

    # The schema loader reads config.ini.example next to config.ini — provide the real one.
    shutil.copy2(
        os.path.join(_REPO_ROOT, "config.ini.example"),
        os.path.join(str(tmp_path), "config.ini.example"),
    )

    config = ConfigParser()
    config.add_section("Bot")
    config.set("Bot", "db_path", str(tmp_path / "meshcore_bot.db"))
    config.add_section("Web_Viewer")
    config.set("Web_Viewer", "host", "0.0.0.0")  # worst case for the finding
    config.set("Web_Viewer", "port", "8080")
    config.set("Web_Viewer", "enabled", "false")
    config.set("Web_Viewer", "auto_start", "false")
    config.set("Web_Viewer", "debug", "false")
    config.set("Web_Viewer", "cors_allowed_origins", "*")
    config.set("Web_Viewer", "web_viewer_password", password)

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
    viewer.app.testing = True
    return viewer


class TestConfigEditorFailsClosedWithoutPassword:
    def test_config_set_blocked_without_password(self, tmp_path):
        viewer = _make_viewer(tmp_path, password="")
        client = viewer.app.test_client()
        resp = client.post(
            "/api/config/set",
            json={"section": "Bot", "key": "log_level", "value": "INFO"},
            headers={"X-Requested-With": "test"},
        )
        assert resp.status_code == 403
        body = resp.get_json()
        assert body and body.get("success") is False

    def test_config_schema_blocked_without_password(self, tmp_path):
        viewer = _make_viewer(tmp_path, password="")
        client = viewer.app.test_client()
        resp = client.get("/api/config/schema")
        assert resp.status_code == 403

    def test_config_editor_page_blocked_without_password(self, tmp_path):
        viewer = _make_viewer(tmp_path, password="")
        client = viewer.app.test_client()
        resp = client.get("/config/editor")
        assert resp.status_code == 403


class TestConfigEditorAllowedWhenAuthenticated:
    def test_schema_served_to_authenticated_session(self, tmp_path):
        viewer = _make_viewer(tmp_path, password="secret-pw")
        client = viewer.app.test_client()
        with client.session_transaction() as sess:
            sess["authenticated"] = True
        resp = client.get("/api/config/schema")
        # Guard passes (password set) and require_auth passes (session); schema returns.
        assert resp.status_code == 200
        assert "sections" in resp.get_json()

    def test_set_reaches_validation_when_authenticated(self, tmp_path):
        """With a password + session the guard is cleared; an unknown key then hits
        the normal 400 validation (proving the guard is not what blocked it)."""
        viewer = _make_viewer(tmp_path, password="secret-pw")
        client = viewer.app.test_client()
        with client.session_transaction() as sess:
            sess["authenticated"] = True
        resp = client.post(
            "/api/config/set",
            json={"section": "Nonexistent", "key": "nope", "value": "x"},
            headers={"X-Requested-With": "test"},
        )
        assert resp.status_code == 400  # not 403 — guard cleared, validation rejected it
