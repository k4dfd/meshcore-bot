#!/usr/bin/env python3
"""Link-quality assessment for the `test` command.

Turns raw SNR / RSSI / hop-count into a plain-language verdict plus one short
tuning hint, so a user testing their node learns not just the numbers but what to
DO with them. Pure functions, no I/O — unit-tested at the bucket boundaries.

Model (our RF preset: SF10 / BW 250 kHz — the MeshCore default preset):
  - LoRa SF10 demodulator SNR floor = -15 dB (Semtech per-SF LoRa limit table,
    -2.5 dB per SF step from SF7 -7.5). MeshCore firmware hardcodes this exact
    value in its own SNR-threshold table (RadioLibWrappers.cpp:194-202).
  - SX1262 SF10 / BW 250 kHz sensitivity ~= -129 dBm (datasheet -132 dBm at
    BW125, +3 dB for the 125->250 kHz bandwidth doubling). Approximate; both
    floors are overridable in config so a different preset can be tuned in.
  - The limiting margin = min(snr_margin, rssi_margin). Verdict buckets on that,
    then a small penalty for long paths (each hop is a dropout opportunity).
"""
from __future__ import annotations

from typing import NamedTuple, Optional

DEFAULT_SNR_FLOOR_DB = -15.0     # SF10 demod floor (matches MeshCore firmware table)
DEFAULT_RSSI_FLOOR_DBM = -129.0  # SF10/BW250 sensitivity (SX1262 -132dBm@BW125 +3dB)

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
    """One short, actionable sentence keyed to the ACTUAL limiting factor.

    The two ways a link runs out of budget need opposite fixes, so we name the
    likely real issue rather than just the tier:
      - RSSI-limited -> range/obstruction problem (signal too weak): more antenna,
        line-of-sight, or a closer repeater.
      - SNR-limited  -> noise/interference problem (signal present but noisy): a
        quieter channel/time, or the antenna away from local noise.
    """
    if verdict == "Unknown":
        return ""

    if verdict in ("Marginal", "Weak", "Fair"):
        if limiting == "snr":
            core = ("SNR is near the demod floor — this is noise/interference-limited: "
                    "try a quieter channel or time of day, and move the antenna away from "
                    "local noise (computers, chargers, USB) or up higher")
        else:  # "rssi" (or a single-metric assessment) -> range/path limited
            core = ("signal strength is the limit — this is range/obstruction-limited: "
                    "raise the antenna, improve line-of-sight, or route through a closer "
                    "repeater")
        lead = "usable but little headroom — " if verdict == "Fair" else "weak link — "
        tail = f" (the {hops}-hop path adds loss too)" if hops and hops > 3 else ""
        return lead + core + tail

    # Good / Excellent — plenty of headroom.
    if (hops or 0) <= 1 and base_margin >= _EXCELLENT:
        return ("strong, near-direct link with headroom to spare — you could lower TX "
                "power to save airtime and battery")
    if (hops or 0) > 3:
        return ("healthy signal but a long path — a closer repeater would cut latency "
                "and add resilience")
    return "healthy link with comfortable margin — no changes needed"
