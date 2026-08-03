"""Tests for the live packet-streaming path (task #27).

Covers the two halves of the feature added in feat/packet-stream:

1. ``BotIntegration._flush_write_queue`` now pushes committed rows live to the
   web viewer (previously the ``_handle_packet_data`` socketio path was never
   reached). Verified via ``_emit_live_rows`` / ``_post_stream_data`` and the
   flush→emit wiring.
2. ``MessageHandler`` captures *every* received mesh packet into the web-viewer
   packet_stream (rich path or raw fallback), deduplicated so a packet heard via
   multiple RF paths is not re-emitted. Verified via ``_should_capture_for_web``
   and ``_capture_raw_packet_for_web``.
"""

import configparser
from unittest.mock import Mock, patch

from modules.message_handler import MessageHandler

# Reuse the BotIntegration factory (all I/O patched out) from the integration suite.
from tests.test_web_viewer_integration import _make_bot_integration

# ---------------------------------------------------------------------------
# Factory helper for MessageHandler
# ---------------------------------------------------------------------------


def _make_handler():
    """MessageHandler with a minimal mock bot (mirrors the test_message_handler fixture)."""
    bot = Mock()
    bot.logger = Mock()
    cfg = configparser.ConfigParser()
    cfg.add_section("Bot")
    cfg.set("Bot", "enabled", "true")
    cfg.set("Bot", "rf_data_timeout", "15.0")
    cfg.set("Bot", "message_correlation_timeout", "10.0")
    cfg.set("Bot", "enable_enhanced_correlation", "true")
    cfg.add_section("Channels")
    cfg.set("Channels", "respond_to_dms", "true")
    bot.config = cfg
    bot.connection_time = None
    bot.prefix_hex_chars = 2
    bot.command_manager = Mock()
    bot.command_manager.monitor_channels = ["general"]
    bot.command_manager.is_user_banned = Mock(return_value=False)
    bot.command_manager.commands = {}
    return MessageHandler(bot)


def _handler_with_integration():
    """Handler whose bot exposes a working web_viewer_integration.bot_integration mock."""
    h = _make_handler()
    integ = Mock()
    integ.bot_integration = Mock()
    h.bot.web_viewer_integration = integ
    return h, integ


# ===========================================================================
# MessageHandler._should_capture_for_web — dedup window
# ===========================================================================


class TestShouldCaptureForWeb:
    def test_first_capture_true_then_duplicate_false(self):
        h = _make_handler()
        assert h._should_capture_for_web("abc", 1000.0) is True
        # Same identity one second later is inside the default 30s window.
        assert h._should_capture_for_web("abc", 1001.0) is False

    def test_distinct_keys_both_capture(self):
        h = _make_handler()
        assert h._should_capture_for_web("k1", 1000.0) is True
        assert h._should_capture_for_web("k2", 1000.0) is True

    def test_empty_key_always_captures(self):
        """A missing packet identity must never be deduped away."""
        h = _make_handler()
        assert h._should_capture_for_web("", 1000.0) is True
        assert h._should_capture_for_web("", 1000.0) is True

    def test_recaptures_after_window_expires(self):
        h = _make_handler()
        h._web_capture_dedup_window = 30.0
        assert h._should_capture_for_web("k", 1000.0) is True
        # 31s later the prior entry is evicted, so it is a fresh capture again.
        assert h._should_capture_for_web("k", 1031.0) is True

    def test_size_cap_bounds_dedup_dict(self):
        h = _make_handler()
        h._max_web_capture_dedup = 5
        h._web_capture_dedup_window = 100000.0  # keep everything inside the window
        for i in range(20):
            h._should_capture_for_web(f"k{i}", 1000.0 + i)
        assert len(h._web_capture_dedup) <= 5

    def test_internal_error_falls_open(self):
        """Any dedup fault must return True so captures are never silently dropped."""
        h = _make_handler()
        h._web_capture_dedup = None  # .get() will raise → except → True
        assert h._should_capture_for_web("k", 1000.0) is True


# ===========================================================================
# MessageHandler._capture_raw_packet_for_web — raw fallback capture
# ===========================================================================


class TestCaptureRawPacketForWeb:
    def test_no_integration_is_noop(self):
        h = _make_handler()
        h.bot.web_viewer_integration = None
        # Must not raise.
        h._capture_raw_packet_for_web({"raw_hex": "aabb"})

    def test_missing_raw_hex_bails(self):
        h, integ = _handler_with_integration()
        h._capture_raw_packet_for_web({"raw_hex": ""})
        integ.bot_integration.capture_full_packet_data.assert_not_called()

    def test_decode_failure_still_captures_raw(self):
        h, integ = _handler_with_integration()
        with patch.object(h, "decode_meshcore_packet", side_effect=Exception("bad")):
            h._capture_raw_packet_for_web({"raw_hex": "aabbccdd", "payload": ""})
        integ.bot_integration.capture_full_packet_data.assert_called_once()
        arg = integ.bot_integration.capture_full_packet_data.call_args[0][0]
        assert arg.get("decode_failed") is True
        assert arg.get("raw_packet_hex") == "aabbccdd"

    def test_decode_success_prefers_extracted_payload_and_keeps_signal(self):
        h, integ = _handler_with_integration()
        decoded = {"payload_type": Mock(value=1), "foo": "bar"}
        with patch.object(h, "decode_meshcore_packet", return_value=decoded):
            h._capture_raw_packet_for_web(
                {"raw_hex": "aabb", "payload": "1122", "snr": -5.0, "rssi": -80}
            )
        integ.bot_integration.capture_full_packet_data.assert_called_once()
        arg = integ.bot_integration.capture_full_packet_data.call_args[0][0]
        assert arg.get("raw_packet_hex") == "1122"  # extracted payload preferred over raw_hex
        assert arg.get("snr") == -5.0
        assert arg.get("rssi") == -80

    def test_duplicate_within_window_suppressed(self):
        h, integ = _handler_with_integration()
        with patch.object(h, "decode_meshcore_packet", side_effect=Exception("bad")):
            h._capture_raw_packet_for_web({"raw_hex": "deadbeef", "payload": ""})
            h._capture_raw_packet_for_web({"raw_hex": "deadbeef", "payload": ""})
        assert integ.bot_integration.capture_full_packet_data.call_count == 1


# ===========================================================================
# BotIntegration._emit_live_rows — row-type → stream-type mapping
# ===========================================================================


class TestEmitLiveRows:
    def _bi_recording(self):
        bi = _make_bot_integration()
        posted = []
        bi._post_stream_data = lambda st, data: posted.append((st, data))
        return bi, posted

    def test_command_row_maps_to_command_stream(self):
        bi, posted = self._bi_recording()
        bi._emit_live_rows([(1.0, '{"a": 1}', "command")])
        assert posted == [("command", {"a": 1})]

    def test_packet_and_routing_map_to_packet_stream(self):
        bi, posted = self._bi_recording()
        bi._emit_live_rows([(1.0, '{"p": 1}', "packet"), (2.0, '{"r": 1}', "routing")])
        assert [st for st, _ in posted] == ["packet", "packet"]

    def test_message_row_not_pushed_live(self):
        bi, posted = self._bi_recording()
        bi._emit_live_rows([(1.0, '{"m": 1}', "message")])
        assert posted == []

    def test_malformed_json_skipped_without_raising(self):
        bi, posted = self._bi_recording()
        bi._emit_live_rows([(1.0, "not-json", "packet"), (2.0, '{"ok": 1}', "packet")])
        # Bad row skipped, good row still emitted.
        assert posted == [("packet", {"ok": 1})]


# ===========================================================================
# BotIntegration._post_stream_data — cross-process bridge to the web viewer
# ===========================================================================


class TestPostStreamData:
    def test_circuit_open_skips_post(self):
        bi = _make_bot_integration()
        bi._should_skip_web_viewer_send = Mock(return_value=True)
        bi.http_session = Mock()
        bi._post_stream_data("packet", {"x": 1})
        bi.http_session.post.assert_not_called()

    def test_session_post_success_records_true(self):
        bi = _make_bot_integration()
        bi._should_skip_web_viewer_send = Mock(return_value=False)
        bi._record_web_viewer_result = Mock()
        sess = Mock()
        bi.http_session = sess
        bi._post_stream_data("packet", {"x": 1})
        assert sess.post.called
        url = sess.post.call_args[0][0]
        assert url.endswith("/api/stream_data")
        assert sess.post.call_args[1]["json"] == {"type": "packet", "data": {"x": 1}}
        bi._record_web_viewer_result.assert_called_with(True)

    def test_session_post_failure_records_false(self):
        bi = _make_bot_integration()
        bi._should_skip_web_viewer_send = Mock(return_value=False)
        bi._record_web_viewer_result = Mock()
        sess = Mock()
        sess.post.side_effect = Exception("connection refused")
        bi.http_session = sess
        bi._post_stream_data("packet", {"x": 1})  # must not raise
        bi._record_web_viewer_result.assert_called_with(False)

    def test_no_session_uses_requests_with_token_header(self):
        bi = _make_bot_integration()
        bi._should_skip_web_viewer_send = Mock(return_value=False)
        bi._record_web_viewer_result = Mock()
        bi.http_session = None
        with patch("requests.post") as rp:
            bi._post_stream_data("command", {"y": 2})
        assert rp.called
        assert "X-Stream-Token" in rp.call_args[1]["headers"]
        bi._record_web_viewer_result.assert_called_with(True)


# ===========================================================================
# BotIntegration._flush_write_queue — the previously-dead emit path is wired
# ===========================================================================


class TestFlushEmitsCommittedRows:
    def test_flush_emits_committed_rows_after_commit(self):
        bi = _make_bot_integration()
        bi._emit_live_rows = Mock()
        bi._write_queue.put((1.0, '{"a": 1}', "packet"))
        # Patch the sqlite layer so the commit "succeeds" without a real DB.
        with patch("sqlite3.connect"):
            bi._flush_write_queue()
        assert bi._emit_live_rows.called
        emitted = bi._emit_live_rows.call_args[0][0]
        assert emitted[0][2] == "packet"

    def test_flush_empty_queue_emits_nothing(self):
        bi = _make_bot_integration()
        bi._emit_live_rows = Mock()
        with patch("sqlite3.connect"):
            bi._flush_write_queue()
        bi._emit_live_rows.assert_not_called()

    def test_failed_commit_does_not_emit(self):
        """Best-effort guarantee: only committed rows are emitted. A DB failure
        requeues the rows and must NOT push anything to live subscribers."""
        bi = _make_bot_integration()
        bi._emit_live_rows = Mock()
        bi._requeue_rows = Mock()  # avoid the real re-queue side effect
        bi._write_queue.put((1.0, '{"a": 1}', "packet"))
        with patch("sqlite3.connect", side_effect=Exception("disk I/O error")):
            bi._flush_write_queue()
        bi._emit_live_rows.assert_not_called()
        bi._requeue_rows.assert_called_once()
