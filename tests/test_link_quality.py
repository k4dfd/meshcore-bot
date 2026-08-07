#!/usr/bin/env python3
"""Tests for the link-quality assessment used by the `test` command."""
from modules.link_quality import (
    DEFAULT_RSSI_FLOOR_DBM,
    DEFAULT_SNR_FLOOR_DB,
    assess_link,
)


def test_strong_direct_link_is_excellent_with_lower_power_hint():
    a = assess_link(snr=10.0, rssi=-90, hops=1)
    assert a.verdict == "Excellent"
    assert "TX power" in a.hint
    assert a.limiting == "snr"  # snr margin 17.5 < rssi margin 37


def test_weak_direct_link_is_marginal_with_antenna_hint():
    a = assess_link(snr=-9.0, rssi=-126, hops=0)
    assert a.verdict == "Marginal"  # snr margin -1.5 -> below 0
    assert "antenna" in a.hint or "line-of-sight" in a.hint


def test_hop_penalty_downgrades_verdict():
    # Same signal, more hops -> lower verdict via the per-hop penalty.
    near = assess_link(snr=6.0, rssi=-100, hops=1)   # margin ~13.5 -> Good
    far = assess_link(snr=6.0, rssi=-100, hops=6)    # -6 dB penalty -> ~7.5 -> Fair
    order = ["Marginal", "Weak", "Fair", "Good", "Excellent"]
    assert order.index(far.verdict) < order.index(near.verdict)


def test_missing_both_metrics_is_unknown():
    a = assess_link(snr=None, rssi=None, hops=2)
    assert a.verdict == "Unknown"
    assert a.hint == ""
    assert a.margin_db is None


def test_single_metric_still_assessed():
    a = assess_link(snr=None, rssi=-95, hops=1)
    assert a.verdict != "Unknown"
    assert a.limiting == "rssi"
    assert a.snr_margin_db is None
    assert a.rssi_margin_db == 32.0  # -95 - (-127)


def test_limiting_factor_is_the_smaller_margin():
    # RSSI near the floor, SNR comfortable -> rssi limits.
    a = assess_link(snr=8.0, rssi=-125, hops=0)
    assert a.limiting == "rssi"


def test_floor_constants_are_sf7_values():
    assert DEFAULT_SNR_FLOOR_DB == -7.5
    assert DEFAULT_RSSI_FLOOR_DBM == -127.0


def test_snr_limited_weak_gives_noise_interference_advice():
    # SNR near the floor, RSSI strong -> SNR-limited -> noise/interference advice.
    a = assess_link(snr=-6.0, rssi=-70, hops=0)
    assert a.limiting == "snr"
    assert a.verdict in ("Weak", "Marginal", "Fair")
    assert "noise" in a.hint or "interference" in a.hint


def test_rssi_limited_weak_gives_range_advice():
    # RSSI near the floor, SNR strong -> RSSI-limited -> range/obstruction advice.
    a = assess_link(snr=10.0, rssi=-125, hops=0)
    assert a.limiting == "rssi"
    assert a.verdict in ("Weak", "Marginal", "Fair")
    assert "range" in a.hint or "line-of-sight" in a.hint


def test_fair_link_hint_notes_thin_headroom():
    a = assess_link(snr=-3.0, rssi=-70, hops=0)  # snr margin 4.5 -> Fair, snr-limited
    assert a.verdict == "Fair"
    assert "little headroom" in a.hint


def test_long_path_weak_mentions_hops():
    a = assess_link(snr=-6.0, rssi=-70, hops=6)  # snr-limited + long path
    assert "hop" in a.hint  # notes the multi-hop loss
