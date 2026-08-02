#!/usr/bin/env python3
"""Radio-metric helpers for the `test` command: a signal-strength bar icon and
LoRa time-on-air. Pure functions (no I/O), unit-tested.
"""
from __future__ import annotations

import math
from typing import Optional

# Green-square signal meter (bright, renders in color; unlike the monochrome block
# glyphs, which some clients stretch/darken). Level derived from packet RSSI (dBm).
_GREEN = "🟩"
_EMPTY = "⬜"


def signal_level(rssi: Optional[float]) -> int:
    """Map packet RSSI (dBm) to a 0–4 signal level (cell-phone bars)."""
    if rssi is None:
        return 0
    try:
        r = float(rssi)
    except (TypeError, ValueError):
        return 0
    if r >= -60:
        return 4
    if r >= -75:
        return 3
    if r >= -90:
        return 2
    if r >= -105:
        return 1
    return 0


def signal_bars(rssi: Optional[float]) -> str:
    """Compact green signal icon (📶) for the given RSSI, '' if unknown.

    The single 📶 emoji renders as bright green cell-signal bars on phone clients
    and stays compact on the one-line reply. Use ``signal_meter`` for a level-
    filled green meter in the multi-line report.
    """
    if rssi is None:
        return ""
    return "📶"


def signal_meter(rssi: Optional[float]) -> str:
    """4-segment green level meter (🟩 filled / ⬜ empty), '' if unknown."""
    if rssi is None:
        return ""
    level = signal_level(rssi)
    return _GREEN * level + _EMPTY * (4 - level)


def airtime_ms(
    payload_len: int,
    *,
    sf: int = 7,
    bw_hz: int = 62500,
    coding_rate: int = 4,   # 1..4 → 4/5..4/8 (our mesh uses 4/8 = 4)
    preamble: int = 16,
    explicit_header: bool = True,
    crc: bool = True,
    low_data_rate: Optional[bool] = None,
) -> Optional[float]:
    """LoRa time-on-air in milliseconds (Semtech formula). None on bad input.

    Defaults match our RF preset (SF7 / BW 62.5 kHz / CR 4/8 / preamble 16).
    """
    try:
        pl = int(payload_len)
        if pl < 0:
            return None
    except (TypeError, ValueError):
        return None
    if sf < 6 or sf > 12 or bw_hz <= 0 or coding_rate < 1 or coding_rate > 4:
        return None

    t_sym = (2 ** sf) / bw_hz  # seconds
    if low_data_rate is None:
        low_data_rate = t_sym > 0.016  # LDRO on when symbol time > 16 ms
    de = 1 if low_data_rate else 0
    ih = 0 if explicit_header else 1
    crc_on = 1 if crc else 0

    numerator = 8 * pl - 4 * sf + 28 + 16 * crc_on - 20 * ih
    denominator = 4 * (sf - 2 * de)
    payload_symb = 8 + max(math.ceil(numerator / denominator) * (coding_rate + 4), 0)

    t_preamble = (preamble + 4.25) * t_sym
    t_payload = payload_symb * t_sym
    return round((t_preamble + t_payload) * 1000.0, 1)


def format_airtime(payload_len: Optional[int], **kw) -> str:
    """Human airtime string ('452ms' / '1.2s'), '' when unknown."""
    if payload_len is None:
        return ""
    ms = airtime_ms(payload_len, **kw)
    if ms is None:
        return ""
    return f"{ms/1000:.2f}s" if ms >= 1000 else f"{int(round(ms))}ms"
