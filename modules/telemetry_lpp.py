#!/usr/bin/env python3
"""Parse a MeshCore telemetry LPP payload into human fields.

The companion lib delivers ``TELEMETRY_RESPONSE`` as ``res["lpp"]`` — a list of
CayenneLPP entries ``{"channel": int, "type": <name str>, "value": <val>}`` where
``type`` is the decoded name ("voltage", "temperature", "humidity", ...). A node's
own battery is the ``voltage`` on the self channel (TELEM_CHANNEL_SELF = 1 in the
firmware). This module is pure (no I/O) so it is unit-tested without hardware.
"""
from __future__ import annotations

from typing import Any, Optional

SELF_CHANNEL = 1  # firmware TELEM_CHANNEL_SELF — the node's own battery/MCU


def parse_lpp(lpp: Any) -> dict[str, Optional[float]]:
    """Return ``{"battery_v","temp_c","humidity_pct"}`` from an LPP entry list.

    Robust to junk: non-list input, missing fields, or unparsable values yield
    ``None`` for that field (honest empty), never an exception. When a type
    appears on multiple channels, the self channel (1) wins for voltage; else the
    first occurrence is used.
    """
    out: dict[str, Optional[float]] = {"battery_v": None, "temp_c": None, "humidity_pct": None}
    if not isinstance(lpp, list):
        return out

    def _num(v: Any) -> Optional[float]:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    for entry in lpp:
        if not isinstance(entry, dict):
            continue
        etype = str(entry.get("type", "")).lower()
        channel = entry.get("channel")
        value = _num(entry.get("value"))
        if value is None:
            continue
        if etype == "voltage":
            # Prefer the self channel; otherwise take the first voltage seen.
            if out["battery_v"] is None or channel == SELF_CHANNEL:
                out["battery_v"] = round(value, 2)
        elif etype == "temperature" and out["temp_c"] is None:
            out["temp_c"] = round(value, 1)
        elif etype == "humidity" and out["humidity_pct"] is None:
            out["humidity_pct"] = round(value, 1)
    return out
