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


# ---------------------------------------------------------------------------
# Write-correctness: the config.ini write must never corrupt the file (#28).
# These use a real copy of config.ini.example AS config.ini and re-parse with a
# strict ConfigParser (BasicInterpolation) — the same way the bot reads it.
# ---------------------------------------------------------------------------

import configparser  # noqa: E402


def _make_write_viewer(tmp_path, password="secret-pw"):
    from modules.web_viewer.app import BotDataViewer

    example_src = os.path.join(_REPO_ROOT, "config.ini.example")
    config_path = str(tmp_path / "config.ini")
    shutil.copy2(example_src, config_path)  # config.ini starts as the full example
    shutil.copy2(example_src, os.path.join(str(tmp_path), "config.ini.example"))
    db_path = str(tmp_path / "meshcore_bot.db")

    with patch.object(BotDataViewer, "_start_database_polling"), \
         patch.object(BotDataViewer, "_start_log_tailing"), \
         patch.object(BotDataViewer, "_start_cleanup_scheduler"), \
         patch.object(BotDataViewer, "_setup_socketio_handlers"), \
         patch("modules.web_viewer.app.RepeaterManager"):
        viewer = BotDataViewer(db_path=db_path, config_path=config_path)
    viewer.web_viewer_password = password  # enable the auth gate for the write path
    viewer.app.testing = True
    return viewer, config_path


def _auth_client(viewer):
    client = viewer.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
    return client


def _reparse(config_path):
    cp = configparser.ConfigParser()  # strict=True (default) raises on duplicate keys
    cp.read(config_path)
    return cp


def _schema_setting(section, key):
    from modules.web_viewer.app import parse_config_schema
    with open(os.path.join(_REPO_ROOT, "config.ini.example"), encoding="utf-8") as f:
        sections = parse_config_schema(f.read())
    sec = next((s for s in sections if s["name"] == section), None)
    if not sec:
        return None
    return next((st for st in sec["settings"] if st["key"] == key), None)


class TestSchemaParsing:
    def test_dm_only_typed_bool_from_active_line(self):
        """The active `dm_only = true` must win over the commented `# dm_only = ...`
        help lines, so it is a bool (not free text with prose)."""
        st = _schema_setting("Schedule_Command", "dm_only")
        assert st is not None, "dm_only missing from schema"
        assert st["type"] == "bool"
        assert st["commented"] is False

    def test_commented_section_keys_do_not_bleed_up(self):
        """Keys under a commented `# [CheckIn]` block must not attribute to the
        active section above it."""
        st = _schema_setting("DARC_MoWaS_Service", "check_in_days")
        assert st is None  # check_in_days belongs to [CheckIn], not DARC_MoWaS_Service


class TestWriteDoesNotCorruptConfig:
    def test_toggle_dm_only_produces_single_active_key(self, tmp_path):
        """The blocker: toggling a setting with commented help lines above it must
        not create a duplicate key that makes config.ini unparseable."""
        viewer, cfg = _make_write_viewer(tmp_path)
        client = _auth_client(viewer)
        resp = client.post(
            "/api/config/set",
            json={"section": "Schedule_Command", "key": "dm_only", "value": "false"},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        cp = _reparse(cfg)  # must NOT raise DuplicateOptionError
        assert cp.get("Schedule_Command", "dm_only") == "false"

    def test_percent_value_roundtrips_under_interpolation(self, tmp_path):
        """A literal % in a text value must survive a BasicInterpolation read."""
        viewer, cfg = _make_write_viewer(tmp_path)
        client = _auth_client(viewer)
        resp = client.post(
            "/api/config/set",
            json={"section": "Bot", "key": "bot_name", "value": "100% Mesh"},
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        cp = _reparse(cfg)
        assert cp.get("Bot", "bot_name") == "100% Mesh"

    def test_empty_secret_rejected(self, tmp_path):
        """Blanking a secret (e.g. web_viewer_password) via the API must be refused."""
        viewer, cfg = _make_write_viewer(tmp_path)
        client = _auth_client(viewer)
        resp = client.post(
            "/api/config/set",
            json={"section": "Web_Viewer", "key": "web_viewer_password", "value": ""},
        )
        assert resp.status_code == 400
