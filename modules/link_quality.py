#!/usr/bin/env python3
"""Link-quality assessment for the `test` command.

Turns raw SNR / RSSI / hop-count into a plain-language verdict plus one short
tuning hint, so a user testing their node learns not just the numbers but what to
DO with them. Pure functions, no I/O — unit-tested at the bucket boundaries.

Model (our RF preset: SF7 / BW 62.5 kHz):
  - LoRa SF7 demodulator SNR floor ~= -7.5 dB (well-established per-SF LoRa limit).
  - SX1262 SF7 / BW 62.5 kHz sensitivity ~= -127 dBm (datasheet, approximate;
    both floors are overridable so a different preset can be tuned in config).
  - The limiting margin = min(snr_margin, rssi_margin). Verdict buckets on that,
    then a small penalty for long paths (each hop is a dropout opportunity).
"""
from __future__ import annotations

from typing import NamedTuple, Optional

DEFAULT_SNR_FLOOR_DB = -7.5      # SF7 demod floor
DEFAULT_RSSI_FLOOR_DBM = -127.0  # SF7/BW62.5 sensitivity (approx)

# Verdict thresholds on the limiting margin (dB), after hop penalty.
_EXCELLENT = 15.0
_GOOD = 8.0
_FAIR = 3.0
_MARGINAL = 0.0


class LinkAssessment(NamedTuple):
    verdict: str            # Excellent | Good | Fair | Weak | Marginal | Unknown
    hint: str               # one short, actionable sentence ("" when Unknown)
    margin_db: Optional[float]   # limiting margin after hop penalty, None if unknown
    snr_margin_db: Optional[float]
    rssi_margin_db: Optional[float]
    limiting: Optional[str]      # "snr" | "rssi" | None


def assess_link(
    snr: Optional[float],
    rssi: Optional[float],
    hops: Optional[int],
    *,
    snr_floor_db: float = DEFAULT_SNR_FLOOR_DB,
    rssi_floor_dbm: float = DEFAULT_RSSI_FLOOR_DBM,
) -> LinkAssessment:
    """Assess a received link. Missing metrics degrade gracefully:

    - Both SNR and RSSI None -> Unknown (no verdict, empty hint).
    - Only one present -> assess on that one alone.
    """
    snr_margin = None if snr is None else float(snr) - snr_floor_db
    rssi_margin = None if rssi is None else float(rssi) - rssi_floor_dbm

    margins = [m for m in (snr_margin, rssi_margin) if m is not None]
    if not margins:
        return LinkAssessment("Unknown", "", None, snr_margin, rssi_margin, None)

    base_margin = min(margins)
    limiting = None
    if snr_margin is not None and rssi_margin is not None:
        limiting = "snr" if snr_margin <= rssi_margin else "rssi"
    elif snr_margin is not None:
        limiting = "snr"
    else:
        limiting = "rssi"

    # Long paths are less robust: dock ~1.5 dB of effective margin per hop beyond 2.
    hop_penalty = 0.0
    h = hops or 0
    if h > 2:
        hop_penalty = 1.5 * (h - 2)
    margin = base_margin - hop_penalty

    if margin >= _EXCELLENT:
        verdict = "Excellent"
    elif margin >= _GOOD:
        verdict = "Good"
    elif margin >= _FAIR:
        verdict = "Fair"
    elif margin >= _MARGINAL:
        verdict = "Weak"
    else:
        verdict = "Marginal"

    hint = _tuning_hint(verdict, limiting, base_margin, h)
    return LinkAssessment(verdict, hint, round(margin, 1),
                          None if snr_margin is None else round(snr_margin, 1),
                          None if rssi_margin is None else round(rssi_margin, 1),
                          limiting)


def _tuning_hint(verdict: str, limiting: Optional[str], base_margin: float, hops: int) -> str:
    """One short, actionable sentence. Keyed to the limiting factor + headroom."""
    if verdict in ("Marginal", "Weak"):
        if hops <= 0:
            return "weak & direct: raise antenna height or improve line-of-sight"
        return "weak link: reposition antenna, add height, or route via a closer repeater"
    if verdict == "Fair":
        return "usable but little margin: a small antenna/height gain would harden it"
    # Good / Excellent — plenty of headroom
    if hops <= 1 and base_margin >= _EXCELLENT:
        return "strong & near-direct: you could lower TX power to save airtime"
    if hops > 3:
        return "solid signal but many hops: a closer repeater would cut latency"
    return "healthy link with good margin"
