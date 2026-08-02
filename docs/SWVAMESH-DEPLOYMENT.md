# SWVAMESH-BOT — Deployment Runbook

**Host:** `swvamesh-bot-server` (Tailscale `100.105.142.9` / `swvamesh-bot-server.tail8ed923.ts.net`)
**Hardware:** Raspberry Pi 5 (16 GB, NVMe), Debian 13 (trixie), RAK6421 WisMesh Pi HAT + RAK13300 SX1262 (Slot 1) + RAK1906 BME680 + RV3028 RTC.
**Deployed:** 2026-08-02.

## Services (all enabled on boot)
| Service | What it does |
|---|---|
| `swvamesh-bot-companion` | pymc_core drives the SX1262 radio; **is** the node `SWVAMESH-BOT`; serves the MeshCore companion on `tcp 127.0.0.1:5000` |
| `meshcore-bot` | the bot (meshcore-bot 0.9.2) + web UI on `:8080`; connects to the companion over TCP |
| `swvamesh-bot-sensors` | RAK1906 BME680 sampler → JSON + local MQTT |
| `mosquitto` | local MQTT broker, **loopback only** (`127.0.0.1:1883`) |

```
  LoRa mesh ⇄ SX1262 ── companion(:5000) ── meshcore-bot ─┬─ web UI :8080
                                                          └─ MQTT → mosquitto :1883
  BME680 ── sensors service → /var/lib/swvamesh-bot/bme680.json + MQTT
```

## Deployment source = YOUR fork
The Pi runs from **`github.com/k4dfd/meshcore-bot`** (`main`). To update:
```bash
sudo -u meshcore-bot git -C /opt/meshcore-bot/app pull   # then: sudo systemctl restart meshcore-bot
```
Fork `main` carries: the **Admin-Nodes web UI**, a **comment-preserving config write**, and a **DM-resolver cache-refresh fix**. A `feat/admin-nodes-ui` branch exists for an upstream PR to `agessaman/meshcore-bot`.

## RF parameters (hardcoded in the companion runner; must match the mesh)
910.525 MHz · SF7 · BW 62.5 kHz · **CR 4/8** · **preamble 16** · 14 dBm · 2-byte hash path.

## Key files
| Path | What |
|---|---|
| `/opt/pymc-companion/companion_runner.py` | radio + companion runner (RF hardcoded) |
| `/opt/pymc-companion/channels.json` | radio channel slots (name + base64 PSK), 0600 |
| `/opt/pymc-companion/state/identity.seed` | node identity (32-byte), 0600 |
| `/opt/meshcore-bot/app/config.ini` | bot config (0600) |
| `/opt/meshcore-bot/app/.webui_password` | web-UI password (0600) — `sudo cat` to read |
| `/opt/swvamesh-sensors/bme680_service.py` | BME680 sampler |
| `/var/lib/swvamesh-bot/bme680.json` | latest sensor reading |
| `/etc/systemd/system/{swvamesh-bot-companion,meshcore-bot,swvamesh-bot-sensors}.service` | units |
| `/etc/mosquitto/conf.d/local-only.conf` | broker bound to loopback |

## Manage the bot
- **Web UI:** `http://100.105.142.9:8080` (or `http://swvamesh-bot-server:8080`). Password: `sudo cat /opt/meshcore-bot/app/.webui_password`.
  - **Admin Nodes** panel: `/admin/config` → list/add/remove admin pubkeys (applies live).
  - Config tab: email/SMTP, logging, DB backup (stored in the bot DB).
- **Admin mesh commands** (DM the bot; requires your device in the admin list): `reload` (apply config.ini changes without restart), `repeater`, `channelpause`, `advert`, `schedule`.
- **config.ini** governs: admin ACL, keywords, commands, rate limits, service plugins, API keys, location, channels. After editing, DM `reload` or `sudo systemctl restart meshcore-bot`.

## Current configuration (highlights)
- Bot name **SWVAMESH-BOT**, ID SWVAMESH-BOT (915 MHz ISM / Part 15 — no callsign).
- Channels monitored: **test, NRVMESH**; `prefix_bytes = 2` (2-byte mesh).
- Location: **Fairlawn, VA** (`bot_latitude 37.1465 / bot_longitude -80.5722`) — drives satpass/sun/moon.
- **`dm_max_retries = 0`** — send DM replies once (pymc_core doesn't surface DM ACKs, so retries caused duplicate replies).
- Admin: **K4DFD-(M1)** (`8b52439ca28e…`).
- n2yo satellite key set (`satpass` live).

## Add / change a radio channel
Edit `/opt/pymc-companion/channels.json` (`[{"name","psk_b64","slot"}]`; convert hex PSK with
`python3 -c "import base64;print(base64.b64encode(bytes.fromhex('HEX')).decode())"`), then
`sudo systemctl restart swvamesh-bot-companion && sudo systemctl restart meshcore-bot`.

## MQTT topics (local broker)
- `meshcore/SWV/<pubkey>/status` and `.../packets` — packet-capture (LOCAL only; the public `letsmesh.net` upload is **disabled** for privacy — enable `mqtt2_*`/TLS in `[PacketCapture]` to opt in).
- `swvamesh-bot/env/bme680` — sensor readings (retained JSON).

## Dormant features — activate by adding credentials, then `reload`/restart
- **Email digests** — web UI → Config → Email (`smtp.gmail.com:587`, Gmail app password).
- **Discord / Telegram** — `[DiscordBridge]` webhook / `[TelegramBridge]` token+chat.
- **AirNow AQI** — `[External_Data] airnow_api_key`. **TheSportsDB** — `[Sports_Command]`.
- **Public map upload** — `[PacketCapture]` re-enable `mqtt2` (letsmesh EU) or set `mqtt1` back to TLS/letsmesh.
- **Earthquake/Weather services** — tune region to VA + a monitored channel (currently defaults).

## Security posture
- Bot runs as unprivileged `meshcore-bot`; hardened unit (`ProtectSystem=strict`, `NoNewPrivileges`, `PrivateTmp`, restricted address families).
- Companion + sensors run as root (raw SPI/GPIO/I2C); companion frame server + mosquitto bound to loopback.
- Secrets 0600 (web password, channel PSKs, identity seed, config.ini). Web UI password-protected; Pi behind NAT.
- Optional stricter web-UI lockdown: enable HTTPS Certificates in the Tailscale admin console, bind UI to `127.0.0.1`, and `sudo tailscale serve --bg 8080` for tailnet-only HTTPS.

## Build gotchas (Debian 13 / Pi 5) — already solved
1. `pip install lgpio` → `cannot find -llgpio`: `apt-get install liblgpio-dev libgpiod-dev python3-dev`.
2. pymc_core GPIO backend: install `python-periphery` (its gpiod path uses the v1 `Chip.get_line`, broken on gpiod 2.x).
3. `pkill -f meshcore_bot.py` also matches your own shell — use `systemctl` + the service cgroup, never pkill -f.
