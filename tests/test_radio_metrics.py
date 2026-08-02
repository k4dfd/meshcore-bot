#!/usr/bin/env python3
"""Tests for the signal-bar icon + LoRa time-on-air helpers."""
from modules.radio_metrics import (
    airtime_ms,
    format_airtime,
    signal_bars,
    signal_level,
)


def test_signal_level_thresholds():
    assert signal_level(-39) == 4      # very strong
    assert signal_level(-60) == 4
    assert signal_level(-61) == 3
    assert signal_level(-80) == 2
    assert signal_level(-95) == 1
    assert signal_level(-120) == 0
    assert signal_level(None) == 0


def test_signal_bars_glyphs():
    assert signal_bars(-39) == "▂▄▆█"   # full
    assert signal_bars(-80) == "▂▄··"
    assert signal_bars(None) == ""        # unknown -> empty, not fabricated
    # every non-None result is a 4-segment meter
    assert len(signal_bars(-95)) == 4


def test_airtime_sf7_bw62500_reasonable():
    # ~80-byte packet at our preset should be a few hundred ms.
    toa = airtime_ms(80)
    assert toa is not None
    assert 300 < toa < 700, toa
    # airtime grows with payload
    assert airtime_ms(120) > airtime_ms(40)


def test_airtime_bad_input_is_none():
    assert airtime_ms(-1) is None
    assert airtime_ms("x") is None
    assert airtime_ms(50, sf=99) is None


def test_format_airtime_units():
    assert format_airtime(80).endswith("ms")
    assert format_airtime(None) == ""
    # a huge payload crosses into seconds formatting
    assert format_airtime(255).endswith(("ms", "s"))
