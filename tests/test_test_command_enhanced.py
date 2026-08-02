#!/usr/bin/env python3
"""Tests for the enhanced `test` command: city naming, RSSI, link quality,
full-report detection, and the compact default template rendering."""
import configparser
from unittest.mock import MagicMock, Mock

from modules.commands.test_command import TestCommand
from tests.conftest import mock_message


def _make_bot():
    bot = MagicMock()
    bot.logger = Mock()
    config = configparser.ConfigParser()
    config.add_section("Bot")
    config.set("Bot", "bot_name", "SWVAMESH-BOT")
    config.set("Bot", "bot_latitude", "37.1465")   # Fairlawn, VA
    config.set("Bot", "bot_longitude", "-80.5722")
    config.add_section("Channels")
    config.set("Channels", "monitor_channels", "general")
    config.add_section("Keywords")
    config.add_section("Test_Command")
    config.set("Test_Command", "enabled", "true")
    config.set("Test_Command", "telemetry_in_test", "false")  # no hardware in unit tests
    bot.config = config
    bot.translator = MagicMock()
    bot.translator.translate = Mock(side_effect=lambda key, **kw: key)
    bot.command_manager = MagicMock()
    bot.command_manager.monitor_channels = ["general"]
    bot.prefix_hex_chars = 2
    # No repeaters/senders in the DB → location lookups return [].
    bot.db_manager = MagicMock()
    bot.db_manager.execute_query = Mock(return_value=[])
    bot.meshcore = MagicMock()
    bot.meshcore.contacts = {}
    return bot


def _cmd():
    return TestCommand(_make_bot())


def test_bot_city_geocodes_to_fairlawn():
    assert _cmd()._bot_city() == "Fairlawn, VA"


def test_bot_city_label_override_wins():
    bot = _make_bot()
    bot.config.set("Test_Command", "bot_city_label", "Radford, VA")
    assert TestCommand(bot)._bot_city() == "Radford, VA"


def test_enhanced_fields_have_rssi_and_quality():
    cmd = _cmd()
    msg = mock_message(content="test", sender_id="YOURNODE", snr=8.5, rssi=-92, hops=2)
    ef = cmd._enhanced_fields(msg)
    assert ef["rssi"] == "-92"
    assert ef["bot_city"] == "Fairlawn, VA"
    assert ef["link_quality"] in ("Excellent", "Good", "Fair", "Weak", "Marginal")
    assert ef["quality_hint"]  # non-empty actionable hint


def test_missing_rssi_is_honest_dash():
    cmd = _cmd()
    msg = mock_message(content="test", snr=None, rssi=None, hops=0)
    ef = cmd._enhanced_fields(msg)
    assert ef["rssi"] == "—"
    assert ef["node_batt"] == "—"
    assert ef["link_quality"] == "Unknown"


def test_default_template_renders_with_new_fields():
    cmd = _cmd()
    msg = mock_message(content="test", sender_id="YOURNODE", snr=8.5, rssi=-92, hops=2)
    out = cmd.format_response(msg, cmd.DEFAULT_FORMAT)
    assert "YOURNODE" in out
    assert "Fairlawn, VA" in out
    assert "SNR 8.5" in out and "RSSI -92" in out
    assert "link" in out


def test_full_request_detection():
    cmd = _cmd()
    assert cmd._is_full_request(mock_message(content="test full")) is True
    assert cmd._is_full_request(mock_message(content="t detail")) is True
    assert cmd._is_full_request(mock_message(content="test")) is False
    assert cmd._is_full_request(mock_message(content="test hello")) is False


def test_split_mode_strips_token_but_keeps_trailing_phrase():
    cmd = _cmd()
    assert cmd._split_mode("full") == (True, "")
    assert cmd._split_mode("full hi there") == (True, "hi there")
    assert cmd._split_mode("hello") == (False, "hello")


def test_full_report_lines_built():
    cmd = _cmd()
    msg = mock_message(content="test full", sender_id="YOURNODE", snr=8.5, rssi=-92, hops=2)
    fields = cmd._assemble_fields(msg)
    lines = cmd._build_full_report(msg, fields)
    assert lines and lines[0].startswith("@[YOURNODE] reached SWVAMESH-BOT")
    assert any(line.startswith("Link:") for line in lines)
    assert any(line.startswith("Tune:") for line in lines)


def test_phrase_full_not_echoed_in_compact():
    cmd = _cmd()
    # "test full extra" → mode stripped, phrase "extra" remains
    fields = cmd._assemble_fields(mock_message(content="test full extra", snr=5, rssi=-100))
    assert fields["phrase"] == "extra"
