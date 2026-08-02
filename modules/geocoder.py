#!/usr/bin/env python3
"""Offline reverse geocoder — nearest US city name for a lat/lon.

Backs the `test` command's "reached RADFORD, VA" naming. Fully offline: reads a
bundled GeoNames-derived dataset (``modules/geodata/us_cities.tsv.gz``, US places
>=1000 pop, ``name<TAB>state<TAB>lat<TAB>lon``). No network, no API key, no scipy.

The dataset is loaded once and cached. Lookup is a haversine nearest-neighbour
with a cheap bounding-box prefilter so a call scans only nearby rows. If the
dataset is missing/unreadable, every lookup returns ``None`` (honest empty) — the
caller degrades to a plain "reached <bot>" with no city, never a crash.

Regenerate the dataset with ``scripts/build_us_cities.py``.
"""
from __future__ import annotations

import gzip
import math
import os
import threading
from typing import Optional

_DATA_PATH = os.path.join(os.path.dirname(__file__), "geodata", "us_cities.tsv.gz")

_lock = threading.Lock()
_rows: Optional[list[tuple[str, str, float, float]]] = None  # (name, state, lat, lon)


def _load() -> list[tuple[str, str, float, float]]:
    """Load and cache the city dataset. Returns [] if unavailable (cached)."""
    global _rows
    if _rows is not None:
        return _rows
    with _lock:
        if _rows is not None:  # re-check under lock
            return _rows
        rows: list[tuple[str, str, float, float]] = []
        try:
            with gzip.open(_DATA_PATH, "rt", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) != 4:
                        continue
                    name, state, lat_s, lon_s = parts
                    try:
                        rows.append((name, state, float(lat_s), float(lon_s)))
                    except ValueError:
                        continue
        except OSError:
            pass  # dataset missing/unreadable -> empty; callers degrade to no city
        _rows = rows
        return _rows


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def nearest_city(lat: float, lon: float, max_km: Optional[float] = None) -> Optional[str]:
    """Return ``"City, ST"`` nearest to (lat, lon), or None.

    None when: the dataset is unavailable, coords are invalid, or the nearest
    city is farther than ``max_km`` (when given). Ties resolve to the larger
    place because the dataset is population-sorted (first match wins).
    """
    if lat is None or lon is None:
        return None
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None

    rows = _load()
    if not rows:
        return None

    best: Optional[tuple[str, str, float, float]] = None
    best_km = float("inf")

    # Cheap bounding-box prefilter: widen until we have candidates. Longitude
    # degrees shrink with latitude, so pad the box generously.
    for box in (1.5, 4.0, 12.0, 90.0):
        lat_pad = box
        lon_pad = box / max(0.2, math.cos(math.radians(lat)))
        found = False
        for name, state, clat, clon in rows:
            if abs(clat - lat) > lat_pad or abs(clon - lon) > lon_pad:
                continue
            found = True
            d = _haversine_km(lat, lon, clat, clon)
            if d < best_km:
                best_km = d
                best = (name, state, clat, clon)
        if found:
            break

    if best is None:
        return None
    if max_km is not None and best_km > max_km:
        return None
    return f"{best[0]}, {best[1]}"


def dataset_available() -> bool:
    """True if the bundled city dataset loaded with at least one row."""
    return bool(_load())
