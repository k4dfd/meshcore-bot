# Enhanced `test` Command — Node Performance / Link Report

**Goal:** Make `test`/`t` a genuinely useful node-tuning tool. From the packet's
source + destination + RF metadata, report how far the sender's node actually
reached (named city), link quality, and actionable tuning hints — plus the
sender node's telemetry when it answers.

**Status:** design approved 2026-08-02 (Chris). Additive to the existing
`TestCommand`; no behavior removed. Honest empty (`—`) whenever a field is absent
— never fabricated.

## Decisions (approved)
- **Geocoding:** bundled offline US city dataset (GeoNames `cities1000`, US subset,
  ≥1000 pop) shipped in-repo; pure-Python nearest-neighbor (haversine). No API key,
  no network at query time, no scipy. Keeps coordinates on-device (privacy).
- **Verbosity:** compact single-line default (fits one ~130-char packet). Opt-in
  `test full` sends a 3–4 line multi-part report (path, quality, tuning).
- **Telemetry:** attempted in the default `test` (Chris's call), but **bounded**:
  a short-timeout pull; on no-answer the reply still returns with `batt: —`.

## Data points (all grounded in verified availability)
| Field | Placeholder | Source | Notes |
|---|---|---|---|
| Sender name/prefix | `{sender}` | packet | always |
| Hop count | `{hops}` `{hops_label}` | `hops`/`routing_info.path_length` | always |
| SNR (dB) | `{snr}` | RF-correlated | channel + DM |
| RSSI (dBm) | `{rssi}` | RF-correlated (0x88 push) | honest `—` when uncorrelated |
| Bot's nearest city | `{bot_city}` | config lat/lon → geocoder | always (bot loc fixed) |
| Sender's nearest city | `{sender_city}` | advert lat/lon → geocoder | only if sender advertises loc |
| Reach distance | `{reach_distance}` | haversine sender→bot | needs sender loc; else `—` |
| Path distance (sum/first-last) | `{path_distance}` `{firstlast_distance}` | repeater loc DB | existing |
| Path bytes | `{path_bytes}` | `routing_info.bytes_per_hop`×hops | channel msgs |
| Named path | `{path_named}` | path_nodes → repeater name+city | channel msgs |
| Link quality verdict | `{link_quality}` | SNR+RSSI margin model | Excellent/Good/Fair/Weak |
| Tuning hint | `{quality_hint}` | limiting-factor heuristic | one short sentence |
| Node battery | `{node_batt}` | bounded telemetry pull | `—` on no-answer |
| Node temp | `{node_temp}` | bounded telemetry pull | `—` on no-answer |

## Link-quality model (SF7 / BW 62.5 kHz — our RF preset)
- SNR demod floor ≈ **−7.5 dB** (SF7). `snr_margin = snr − (−7.5)`.
- RSSI sensitivity ≈ **−127 dBm** (SF7/BW62.5, from SX1262 datasheet; pin exact at build).
  `rssi_margin = rssi − (−127)`.
- Verdict from `min(snr_margin, rssi_margin)` with a small hop penalty:
  Excellent ≥ 15 dB, Good ≥ 8, Fair ≥ 3, Weak < 3 (Marginal < 0).
- Hint keyed to the limiting factor (weak signal → antenna/height; strong + spare
  hops → could lower TX power; many hops → closer repeater / better placement).

## File structure
- `modules/geocoder.py` (NEW) — `nearest_city(lat, lon) -> "City, ST" | None`; lazy dataset load + cache.
- `modules/geodata/us_cities.tsv.gz` (NEW, tracked) — GeoNames US ≥1000-pop subset: `name\tstate\tlat\tlon`.
- `scripts/build_us_cities.py` (NEW) — regenerates the dataset from GeoNames (documented, reproducible).
- `modules/link_quality.py` (NEW) — pure `assess_link(snr, rssi, hops) -> (verdict, hint, margins)`.
- `modules/commands/test_command.py` (MODIFY) — new placeholders, `test full`, bounded telemetry pull.
- `config.ini.example` (MODIFY) — `[Test_Command]` new keys + templates + docs.
- `tests/` — geocoder (known-city assertions), link_quality (bucket boundaries), test-field assembly.

## Constraints
- Reply cap ~130 chars (chunked multi-part exists for `test full`).
- DMs are encrypted → hop *count* only (no node hashes); channel msgs expose path_nodes.
- Data dir `data/` is gitignored → dataset lives under `modules/geodata/` (tracked).
