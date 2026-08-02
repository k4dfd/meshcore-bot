#!/usr/bin/env python3
"""Regenerate the bundled offline US city dataset for the `test` command geocoder.

Source of truth: GeoNames `cities1000` (all cities >=1000 pop, CC-BY 4.0).
We filter to US rows and emit a compact TSV `name<TAB>state<TAB>lat<TAB>lon`,
gzipped, sorted by population descending so ties resolve to the larger place.

GeoNames cities1000.txt columns (tab-separated, 0-indexed):
  1 = name (asciiname preferred, col 2), 4 = latitude, 5 = longitude,
  8 = country code, 10 = admin1 code (for US = 2-letter USPS state), 14 = population.

Run:  python3 scripts/build_us_cities.py
Output: modules/geodata/us_cities.tsv.gz  (committed to the repo)

This script needs network ONCE to regenerate; the bot never fetches at runtime.
"""
import gzip
import io
import os
import sys
import urllib.request
import zipfile

URL = "https://download.geonames.org/export/dump/cities1000.zip"
OUT = os.path.join(os.path.dirname(__file__), "..", "modules", "geodata", "us_cities.tsv.gz")


def main() -> int:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    sys.stderr.write(f"Downloading {URL} ...\n")
    req = urllib.request.Request(URL, headers={"User-Agent": "swvamesh-bot-geobuild/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
    sys.stderr.write(f"  got {len(raw)/1e6:.1f} MB zip\n")

    zf = zipfile.ZipFile(io.BytesIO(raw))
    txt = zf.read("cities1000.txt").decode("utf-8")

    rows = []
    for line in txt.splitlines():
        f = line.split("\t")
        if len(f) < 15 or f[8] != "US":
            continue
        name = f[2] or f[1]          # asciiname, fall back to utf-8 name
        state = f[10]                # US admin1 = USPS 2-letter
        lat, lon = f[4], f[5]
        try:
            pop = int(f[14] or "0")
        except ValueError:
            pop = 0
        if not (name and state and lat and lon):
            continue
        rows.append((pop, name, state, lat, lon))

    rows.sort(key=lambda r: r[0], reverse=True)  # larger places win prefix ties
    sys.stderr.write(f"  {len(rows)} US places\n")

    buf = io.StringIO()
    for _pop, name, state, lat, lon in rows:
        # trim coords to 4 dp (~11 m) to shrink the file; plenty for city naming
        buf.write(f"{name}\t{state}\t{float(lat):.4f}\t{float(lon):.4f}\n")
    with gzip.open(OUT, "wt", encoding="utf-8") as gz:
        gz.write(buf.getvalue())

    size = os.path.getsize(OUT)
    sys.stderr.write(f"Wrote {OUT} ({size/1e6:.2f} MB, {len(rows)} rows)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
