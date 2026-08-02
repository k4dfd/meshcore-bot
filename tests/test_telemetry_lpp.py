#!/usr/bin/env python3
"""Tests for the CayenneLPP telemetry parser used by the `test` command."""
from modules.telemetry_lpp import parse_lpp


def test_parses_voltage_temp_humidity():
    lpp = [
        {"channel": 1, "type": "voltage", "value": 4.15},
        {"channel": 1, "type": "temperature", "value": 23.4},
        {"channel": 1, "type": "humidity", "value": 55.0},
    ]
    out = parse_lpp(lpp)
    assert out["battery_v"] == 4.15
    assert out["temp_c"] == 23.4
    assert out["humidity_pct"] == 55.0


def test_self_channel_voltage_wins_over_other_channel():
    lpp = [
        {"channel": 3, "type": "voltage", "value": 3.30},
        {"channel": 1, "type": "voltage", "value": 4.02},  # self channel
    ]
    assert parse_lpp(lpp)["battery_v"] == 4.02


def test_missing_and_junk_yields_none_not_exception():
    assert parse_lpp(None) == {"battery_v": None, "temp_c": None, "humidity_pct": None}
    assert parse_lpp("nonsense")["battery_v"] is None
    assert parse_lpp([{"channel": 1, "type": "voltage", "value": "NaNish"}])["battery_v"] is None
    assert parse_lpp([{"nope": 1}])["temp_c"] is None


def test_partial_payload():
    out = parse_lpp([{"channel": 1, "type": "temperature", "value": 19.9}])
    assert out["temp_c"] == 19.9
    assert out["battery_v"] is None
