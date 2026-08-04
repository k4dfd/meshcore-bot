# Nodey — SWVAMESH-BOT + MeshConnect Knowledge Base

Nodey's grounding knowledge for BOTH applications: SWVAMESH-BOT (this MeshCore
mesh-radio bot and its web viewer) and MeshConnect / mcdash (the operator
dashboard). Used to answer "how do I…" / "why isn't X working" accurately, and to
help draft feature specifications. Keep it factual and current. Never reveal
secrets (keys, passwords, PSKs) — they are never in here.

---

# PART 1 — SWVAMESH-BOT: User Manual

## What SWVAMESH-BOT is
SWVAMESH-BOT is a Python bot (meshcore-bot) that connects to a MeshCore LoRa mesh
network over serial, BLE, or TCP/IP. It listens on configured channels and DMs,
answers keyword commands, tracks contacts/repeaters/paths in a local database, and
runs scheduled messages and service plugins (weather, feeds, Discord/Telegram
bridges, etc.). A Flask + SocketIO web viewer gives the operator a browser
dashboard for monitoring the bot and editing its configuration, separate from what
mesh users see over the radio.

## The web viewer pages (feature map)
- Dashboard (home, `/`) — bot health/uptime, contact and cache summary tiles,
  hop-count stats, geographic reach (countries/states/cities), message/command
  volume, and four analytics cards: Most Popular Commands, Most Active Users,
  Longest Paths, and Top Channels (each with a 24h/7d/30d/all time-window
  selector). Also hosts the Live Activity feed (color-coded packets/commands/
  messages).
- Realtime (`/realtime`) — three live streams: Command Stream, Live Channel
  Messages, and Packet Stream, each with pause/clear controls. Clicking a packet
  opens a detail modal showing packet hash, route, path bytes, and (for
  GroupText/Advert) a byte-level payload breakdown, including decrypted channel
  text where a key is known.
- Contacts (`/contacts`) — full contact list with signal, path, and location
  data. Star a contact, purge inactive contacts by age, export to CSV/JSON.
  Distances are shown in miles (feet for <0.1 mi).
- Mesh (`/mesh`) — interactive graph/map of the mesh network showing node counts,
  repeaters, and connections. Distances in miles.
- Greeter (`/greeter`) — onboarding-period status, current greeter settings, a
  sample greeting preview, and a searchable list of already-greeted users (with
  the ability to un-greet one).
- Feeds (`/feeds`) — RSS/API feed subscriptions per channel: totals, active feeds,
  items in the last 24h, active errors, and a table to add/edit/delete feed
  subscriptions.
- Radio (`/radio`) — channel slot usage stats, radio parameters (read from
  device), and channel management; includes Reboot Radio and Connect/Disconnect
  controls.
- Config (`/config`) — in-browser settings across panels: Email & Notifications
  (SMTP + nightly digest), Log Rotation, Radio Reliability, Maintenance Status,
  Database Information, and Database Backup. Links out to the API Explorer and Raw
  Config views.
- Config Editor (`/config/editor`) — a data-driven editor covering the full
  `config.ini.example` schema (every section/key, not just the Config-tab panels).
  Requires `web_viewer_password` to be set — it is fail-closed and refuses to load
  if no password is configured, even before login.
- Logs (`/logs`) — real-time log viewer with level-based coloring (debug/info/
  warning/error/critical), per-level filter toggles, pause, and clear.

## Bot commands (what mesh users send)
- `test` / `t` [phrase] — personalized link report: hops, SNR/RSSI, a signal-bar
  icon, "Reached <bot> in <City>" with distance in miles from the sender's node, a
  link-quality verdict (with margin), and a Path Hash size when routed. `test full`
  (or `detail`/`-v`) gives a multi-line expanded report (per-hop named path,
  airtime, tuning hint, node battery/temp if available).
- `wx`/`weather` <zip|city> — NOAA weather for a US location or global forecast.
- `aqi`/`air` <location> — Air Quality Index lookup.
- `sun` — sunrise/sunset; `moon` — moon phase and rise/set; `solar` — solar/HF band
  conditions; `hfcond` — HF band conditions for ham radio.
- `satpass <NORAD#|shortcut>` — satellite pass predictions.
- `dice`/`roll` — dice rolls; `magic8` — Magic 8-ball.
- `joke`/`dadjoke`/`hacker`/`catfact` — entertainment one-liners.
- `sports` [team|league] — sports scores/schedules.
- `alert`/`alerts` <location> — active emergency incidents (PulsePoint).
- `path`/`decode`/`route` — decode hex path data to show which repeaters routed a
  message.
- `prefix`/`repeater`/`lookup` <hex> — look up repeaters by prefix and show known
  locations.
- `channels` [list|category|#channel] — lists hashtag channels and categories.
- `stats` [messages|channels|paths|adverts] — bot usage stats for the past 24 hours.
- `multitest`/`mt` — listens 6 seconds and reports all unique paths your messages
  took to reach the bot.
- `ping` — replies "Pong!"; `version`/`ver` — running bot version; `help`
  [command] — command list/details.
- `schedule` (DM only) — upcoming scheduled messages and the advert interval.
- DM-only management commands: `repeater` (repeater/purge management), `advert`,
  `feed`, `announce`, `webviewer`.

## Common how-to (operator)
- Check longest paths: Dashboard home page, Longest Paths card — pick a time window
  (24h/7d/30d/all).
- Watch live packets/commands: Realtime page — three panes (Command Stream, Live
  Channel Messages, Packet Stream); click any packet for a byte-level breakdown.
- Edit a config setting: Config Editor (`/config/editor`) for any
  `config.ini.example` key, or the Config page (`/config`) for the curated panels.
  Both require `web_viewer_password` to be set in `[Web_Viewer]`; the Config Editor
  is disabled entirely without it.
- Find a contact's distance: Contacts page — distance column (miles, or feet under
  ~0.1 mi), computed from the contact's advertised location.
- Read the logs: Logs page (`/logs`) — live-tailing viewer with level coloring,
  filters, pause, and clear.
- Restart the bot: on a systemd install, `sudo systemctl restart meshcore-bot` (or
  `status`/`start`/`stop`). If the radio has entered a "zombie" state (unresponsive
  firmware), power-cycle the radio hardware first, then use the "Restart Bot
  Processing" button in the zombie banner shown on every web-viewer page.

## Troubleshooting
- RSSI/SNR shows "—" on `test`: signal metrics come from the RX_LOG_DATA/RAW_DATA
  event subscriptions set up during connect(). If the companion hasn't (re)connected
  cleanly, or the connection doesn't emit those raw events, no fresh SNR/RSSI is
  available and the field falls back to "—".
- Bot offline / not responding to any mesh traffic: check the Radio Offline banner
  (shown on every page after repeated send timeouts) — outbound sends are paused but
  inbound packets may still arrive. Clear it once the radio connection is healthy. A
  separate "Radio Zombie" banner means the radio firmware itself is unresponsive and
  needs a physical power cycle (reconnect alone won't fix it).
- A message isn't getting a reply: the bot only responds on channels listed in
  `[Channels] monitor_channels` (plus DMs, if `respond_to_dms = true`) — messages on
  channels not in that list, or blocked by `channel_keywords`, are ignored by design.
- Config Editor returns 403 / won't load: it fails closed — it refuses to render or
  accept writes unless `web_viewer_password` is set in `[Web_Viewer]`, since it can
  set any setting including secrets/PSKs. Set a password to unlock it, then log in.

---

# PART 2 — SWVAMESH-BOT: Developer Guide

This is what lets Nodey help draft realistic feature specs. Ground every "affected
files" claim in this section.

## Architecture overview
- `meshcore_bot.py` is the process entrypoint. It parses CLI args, builds a
  `MeshCoreBot` (in `modules/core.py`), wires signal handlers (SIGTERM/SIGINT for
  shutdown, SIGHUP for live config reload), and runs inside `asyncio.run()`.
- The bot does NOT talk to the radio directly. On the deployed host a separate
  process (`pymc` core, the `swvamesh-bot-companion` service) drives the SX1262 LoRa
  radio and IS the mesh node. It exposes a MeshCore "companion" protocol over
  `tcp 127.0.0.1:5000`.
- `modules/core.py` connects to that companion using the `meshcore` Python library
  (`meshcore.MeshCore.create_tcp(...)`, default `127.0.0.1:5000`; serial/BLE are the
  other `[Connection] connection_type` values). This gives an async event bus
  (`EventType.*`) for contact messages, channel messages, adverts, raw RF log data.
- `modules/message_handler.py` (`MessageHandler`) subscribes to those events,
  decodes MeshCore packets (advert parsing, path/route extraction, SNR/RSSI caching
  keyed by packet prefix), builds a normalized `MeshMessage`, and calls
  `process_message()`.
- `process_message()` records stats, runs the greeter, then hands the message to
  `modules/command_manager.py` (`CommandManager`), the dispatcher for keyword replies
  and command plugins in `modules/commands/`.
- `CommandManager` also owns rate limiting, cooldown queuing, channel/flood-scope
  resolution, and the `send_dm` / `send_channel_message` calls back down to the
  companion.
- A Flask + Flask-SocketIO web viewer (`modules/web_viewer/app.py`) runs as a
  SEPARATE subprocess, spawned and supervised by `modules/web_viewer/integration.py`
  (`WebViewerIntegration`). It reads the same `config.ini` and a shared SQLite DB.
- The bot pushes live data (commands, raw packets, mesh graph edges/nodes) into the
  viewer subprocess over local HTTP (`POST /api/stream_data`), authenticated with a
  shared `X-Stream-Token` secret.
- `modules/scheduler.py` (`MessageScheduler`) runs a background APScheduler thread for
  scheduled messages, DB backups, nightly maintenance email, and radio health/zombie
  alerts.
- `modules/db_manager.py` (`DBManager` / `AsyncDBManager`) wraps the SQLite DB
  (contacts, stats, path_stats, feeds, packet_stream, metadata) used by both processes.

## The command/plugin system
- Every command is a Python class in `modules/commands/` (or `local/commands/` for
  out-of-tree plugins) subclassing `BaseCommand` (`modules/commands/base_command.py`).
  `PluginLoader` (`modules/plugin_loader.py`) discovers every `.py` file there, finds
  the `BaseCommand` subclass, instantiates it, and registers it by `name`/`keywords`.
- `BaseCommand` declares class attributes a subclass overrides: `name`, `keywords`
  (all trigger words/aliases), `description`, `category`, `requires_dm`,
  `requires_internet`, `cooldown_seconds`, `handles_own_response`. It provides
  `get_config_value()` (config lookup with old/new section-name migration),
  channel-allowlist loading, cooldown tracking, admin-ACL checks, and
  `send_response()` / `send_response_chunked()`.
- THERE ARE TWO DISPATCH PATHS, both from `CommandManager.check_keywords()`:
  - Keyword-matcher path: for each command, `should_execute()` (keyword/mention
    match) then `can_execute()` (channel, cooldown, admin ACL) are checked. If both
    pass, `CommandManager` calls `get_response_format()`. If that returns a non-empty
    string AND `handles_own_response` is falsy, `CommandManager` pre-renders the
    response itself (`format_response()`) and sends it — the command's `execute()` is
    NEVER called for this trigger.
  - execute() path: if `get_response_format()` returns `None`, or the command sets
    `handles_own_response = True`, `check_keywords()` records a match with
    `response=None`; `process_message()` then defers to
    `CommandManager.execute_commands()`, which calls the command's own
    `async execute(message)`.
- `handles_own_response = True` exists for any command that must build a
  personalized, multi-part, or conditionally-chunked reply in `execute()` (e.g.
  `test_command.py`). If left false, the keyword-matcher would pre-render and send a
  generic templated reply and `execute()` would never run — the personalization would
  silently never happen.
- `monitor_channels` (`[Channels] monitor_channels`) is the channel allowlist gate: a
  channel message is only matched if its channel is in `monitor_channels` (or the
  command defines its own `channels =` override). DMs bypass this (governed by
  `respond_to_dms`). `channel_keywords` can further restrict which triggers are
  allowed in channels.

## How to add a new command (worked example)
Put a new file in `modules/commands/` (or `local/commands/` to stay out of the fork —
same API, additive loading). One file, one class, subclassing `BaseCommand`:

```python
# modules/commands/echo_command.py
from ..models import MeshMessage
from .base_command import BaseCommand


class EchoCommand(BaseCommand):
    name = "echo"
    keywords = ["echo"]
    description = "Echoes back whatever follows 'echo'"
    category = "basic"

    def __init__(self, bot):
        super().__init__(bot)
        self.echo_enabled = self.get_config_value(
            "Echo_Command", "enabled", fallback=True, value_type="bool"
        )

    def can_execute(self, message: MeshMessage, skip_channel_check: bool = False) -> bool:
        if not self.echo_enabled:
            return False
        return super().can_execute(message)

    def get_help_text(self) -> str:
        return self.description

    async def execute(self, message: MeshMessage) -> bool:
        phrase = message.content[len("echo"):].strip()
        return await self.send_response(message, phrase or "(nothing to echo)")
```

- `get_response_format()` left at the default (`None`), so this always goes through
  `execute()` — no need to set `handles_own_response`.
- For a single templated line instead, override `get_response_format()` to return the
  template and let `BaseCommand` render+send it — no custom `execute()` needed (see
  `PingCommand`).
- Registration is automatic — no import list/manifest. A bot restart picks it up.
- Document a config section in `config.ini.example` so it appears in the Config Editor:

```ini
[Echo_Command]
enabled = true
# channels = general,test    (optional channel override)
```

- Test it like the existing command tests (`tests/test_joke_command.py`): a
  `MagicMock()` bot with a real `configparser.ConfigParser()`, mocked `bot.translator`,
  instantiate the command directly, call against a `mock_message(...)` helper from
  `tests/conftest.py`. Run `make test-no-cov` for fast iteration, `make lint` before
  committing.

## Configuration system
- `config.ini.example` is the canonical, commented schema — every section/key with
  explanatory comments (units, defaults, ranges). It is ALSO machine-parsed: the web
  Config Editor derives its entire schema from this file (`parse_config_schema()`), so
  EVERY new config key a command reads must be documented here to appear in the editor.
- `config.ini` is the live, git-ignored copy (on the Pi: `/opt/meshcore-bot/app/
  config.ini`, mode 0600). `config.ini.d/` overlay directories are also read.
- Commands read config through `BaseCommand.get_config_value(section, key, fallback,
  value_type)`, never `bot.config.get()` directly, because it handles legacy
  section-name/key migrations.
- The web Config Editor (`/config/editor`, backed by `/api/config/schema` and
  `/api/config/set`) is FAIL-CLOSED (`_config_editor_guard()`): refuses to serve or
  write unless `[Web_Viewer] web_viewer_password` is set. Writes are a targeted,
  comment-preserving line edit (matches the ACTIVE `key =` line inside the right
  `[section]`, replaces just it, or appends if missing) written atomically via temp +
  `os.replace`. A blank secret value is rejected (would disable auth on reload). A
  write queues a `reload_config` op the scheduler applies without a full restart.

## Web viewer internals
- `modules/web_viewer/app.py` = a `Flask` app + `SocketIO()`, configured in
  `_setup_routes()`. It runs as its own OS subprocess launched by
  `WebViewerIntegration.start_viewer()`.
- Key `/api/*` routes: `/api/mesh/{nodes,edges,stats}`, `/api/contacts`, `/api/stats`,
  `/api/config/*` (notifications, logging, admin ACL, maintenance, radio-debug,
  zombie/offline alerts, the schema-driven editor), `/api/radio/*`, `/api/channels/*`,
  `/api/feeds/*`, `/api/greeter`, `/api/export/*`, `/api/maintenance/*`, `/api/health`.
- `require_auth()` is a Flask `before_request`: if no `web_viewer_password`, auth is
  fully disabled (open dashboard); otherwise every path except a small login/static
  exemption — and except a valid `X-Stream-Token`-authenticated `POST /api/stream_data`
  — requires `session['authenticated']`. A `csrf_protection()` hook rejects
  state-changing requests missing `X-Requested-With`; `set_security_headers()` adds
  CSP/X-Frame-Options/nosniff and scrubs internal error detail from 5xx JSON.
- The internal bot→viewer bridge: `BotIntegration` (in the bot process) generates a
  random 32-byte `_stream_token`, persists it to DB metadata (`internal.stream_token`),
  and attaches it as `X-Stream-Token` on every POST to the viewer's `/api/stream_data`;
  the route compares with `hmac.compare_digest`.
- `packet_stream` is a shared SQLite table: the bot batches writes through a bounded
  queue + drain thread; the viewer reads it for the live packet log and history.
- SocketIO events: clients `subscribe_{commands,packets,mesh,messages,logs}`; the
  server emits `command_data`, `packet_data`, mesh edge/node events, `message_data`.

## Message & RF flow
- The `meshcore` library surfaces low-level RF events as `EventType.RX_LOG_DATA`
  (companion opcode `0x88`). `MessageHandler.handle_rf_log_data()` extracts
  `snr`/`rssi`/`raw_hex`, keys them by a packet-prefix hash, and LRU-caches them to
  join to the higher-level message arriving moments later.
- The same handler parses `routing_info`: `path_length`, `path_hex`/`path_nodes` (hop
  list), `bytes_per_hop` (1 or 2-byte hashes), `route_type` (direct/flood/routed),
  `payload_length`/`payload_type`.
- For a channel message, `handle_channel_message()` decodes the packet, correlates it
  with cached RF data (`_correlate_channel_message_rf_data` / `find_recent_rf_data`),
  attaches `routing_info` (and derived SNR/RSSI/path) onto a `MeshMessage`, updates the
  mesh graph, and calls `process_message()`.
- `process_message()` is the single funnel: records stats/path_stats for EVERY message
  first, runs the greeter, applies `respond_to_mentions` filtering, then calls
  `check_keywords()` and either sends the pre-rendered response or falls through to
  `execute_commands()` for `handles_own_response`/no-format commands.
- A reply goes out through `send_channel_message()` / `send_dm()` → `meshcore` →
  companion → LoRa.

## Deploy, test, and where things live
- Git flow: deployed fork is `github.com/k4dfd/meshcore-bot` (upstream:
  `github.com/agessaman/meshcore-bot`). Feature branches merge to `main`; `main` is
  what the Pi runs.
- Pi deploy (config.ini is git-ignored there):
  ```
  sudo -u meshcore-bot git -C /opt/meshcore-bot/app pull --ff-only origin main
  sudo systemctl restart meshcore-bot
  ```
- The companion (`swvamesh-bot-companion`, `pymc_core` on the SX1262) is a separate
  systemd service and is NOT restarted by a bot deploy — only restart it if RF
  parameters or channel PSKs change.
- `Makefile`: `make dev` (venv + deps), `make test` (pytest+coverage), `make
  test-no-cov` (faster), `make lint` (ruff + mypy), `make fix` (ruff autofix).
- Tests in `tests/`, one file per command/module, using `MagicMock` bots + real
  `configparser` + `tests/conftest.py` helpers.

## Source-file map
- `meshcore_bot.py` — CLI entrypoint.
- `modules/core.py` — `MeshCoreBot`: connect, config load/reload, start scheduler +
  viewer, main event loop.
- `modules/command_manager.py` — `CommandManager`: dispatch, rate limits, cooldowns,
  send methods.
- `modules/message_handler.py` — `MessageHandler`: decode packets/adverts, correlate
  RF (SNR/RSSI/path), build `MeshMessage`, `process_message()`.
- `modules/commands/base_command.py` — `BaseCommand`: the plugin interface.
- `modules/commands/*.py` — one file per command plugin.
- `modules/plugin_loader.py` — `PluginLoader`: discover/load/validate plugins.
- `modules/web_viewer/app.py` — Flask + SocketIO dashboard: all `/api/*` routes, auth/
  CSRF, config editor, radio control, mesh graph, feeds.
- `modules/web_viewer/integration.py` — `BotIntegration` (push client + token bridge)
  and `WebViewerIntegration` (subprocess lifecycle).
- `config.ini.example` — canonical config schema; parsed live for the Config Editor.
- `modules/scheduler.py` — `MessageScheduler`: scheduled messages, backups,
  maintenance, radio alerts.
- `modules/db_manager.py` — SQLite access layer shared by bot and viewer.
- `modules/nodey/` — THIS assistant (persona, provider, knowledge, scrub).

## Custom features already built (recent additions)
- Enhanced `test` command — `modules/commands/test_command.py` + `modules/geocoder.py`
  (nearest city), `modules/link_quality.py` (SNR/RSSI-floor verdicts),
  `modules/radio_metrics.py` (signal bars/meter, airtime), `modules/telemetry_lpp.py`
  (CayenneLPP battery/temp/humidity). Personalized greeting + auto-chunked reply; a
  template case for any future multi-part `handles_own_response = True` command.
- Live packet streaming — `message_handler.py` (`_capture_raw_packet_for_web`) →
  `web_viewer/integration.py` (write-queue drain + emit) → `realtime.html`
  (`subscribe_packets` / `packet_data`).
- Longest Paths / path_stats — `modules/commands/stats_command.py` (`record_path_stats`,
  `path_stats` table) surfaced in `web_viewer/app.py` stats routes. Note: path_stats is
  populated from `message.routing_info` (path_nodes/path_length), and channel-message RF
  correlation prefers a path-bearing RF entry.
- Data-driven Config Editor — `web_viewer/app.py` (`_load_config_schema`,
  `/api/config/schema`, `/api/config/set`, `_config_editor_guard`) + `config_editor.html`.
- Imperial-units display — distances in miles, temperatures in °F throughout the viewer
  and `test` command.
- Companion RSSI fix — `handle_rf_log_data()` caches SNR/RSSI per packet-prefix from the
  companion's `RX_LOG_DATA` (`0x88`) events (a persistent raw-packet subscriber in the
  companion runner keeps them flowing after a client connects).

## Customizing the bot's greetings & random phrases
The chatty "{opening} {descriptor}! I'm <bot_name>." reply (e.g. "Hola friend! I'm
SWVAMESH-BOT.") is the **hello command** (`modules/commands/hello_command.py`),
triggered by GREETING KEYWORDS — hello, hi, hey, howdy, hola, bonjour, aloha,
konnichiwa, and more (see `hello_command.py` `keywords`). It is NOT the `test`
command and NOT the greeter, so a message containing a greeting word is what gets
this reply.
- Where the wording lives: `translations/en.json` → the `commands.hello` section:
  `greeting_openings` (Hello / Hola / Howdy / …), `human_descriptors` (friendly
  terms for the person — kept warm and polite: friend, fellow traveler, neighbor,
  earthling, …), `response_format` ("I'm {bot_name}."), plus
  `morning/afternoon/evening_greetings` and `emoji_responses`. Each language has its
  own file (`es.json`, `fr.json`, …). There are hardcoded FALLBACK copies of these
  lists in `hello_command.py` that are used ONLY if a translation key is missing.
- To change the wording: edit `translations/en.json` `commands.hello.*`, then
  restart the bot. (Translation files are not exposed in the web Config Editor —
  edit the JSON directly.)
- To turn the hello greeting OFF entirely: set `[Hello_Command] enabled = false`
  (works in the Config Editor; applies on reload).
- Separate mechanism: the NEW-USER onboarding greeting is the greeter, config
  `[Greeter_Command] greeting_message` (default "Welcome to the mesh, @[{sender}]!").

---

# PART 3 — MeshConnect / mcdash (the dashboard)

## What MeshConnect is
MeshConnect (internal package `mcdash`) is an all-in-one operator dashboard and
appliance for a MeshCore LoRa mesh network. It runs as a systemd service on a
Raspberry Pi and does three jobs: (1) Collector — ingests mesh packets/status from
observer radios over MQTT; (2) Companion — a full graphical client to a directly-
attached MeshCore radio (contacts, channels, messaging, device config, tools, CLI)
over USB / BLE / Wi-Fi; (3) Management — brokers, forwarding, fleet, alarms, users,
config. MeshCore is NOT Meshtastic — different firmware/protocol.

## mcdash architecture
- Backend: FastAPI (Python), SQLite (WAL, schema-versioned), the `meshcore` companion
  library, `paho-mqtt` ingest, uvicorn behind the `mcdash` systemd unit. Secrets
  encrypted at rest (pynacl) or env-only; never returned to the frontend, never logged.
- Frontend: React + Vite + TypeScript, TanStack Query, MapLibre GL, cyan-on-near-black.
- Auth: session cookie + CSRF; roles `admin`/`viewer` refined by granular capabilities
  (e.g. `mqtt.brokers`, `companion.transmit`). Admins hold all capabilities.
- Deploy: under `/opt/mcdash/app`; reachable over Tailscale HTTPS.

## mcdash pages (feature map)
- Overview — situational glance + system-health alarms.
- Live Feed — real-time packet stream (WebSocket), filter/export.
- Statistics — traffic/signal/node trends; "Top Talkers".
- Map · GIS: Topology (geographic node/edge graph + reachability + SNR edges), Radar
  (live map — node radio actions, replay/timeline, elevation/LoS), Coverage Map
  (terrain line-of-sight viewshed).
- Companion · MeshCore: Contacts, Direct/Channel Messages, Channel Setup, Configuration,
  Alerts, Companion CLI, Tools (trace/discover/neighbours/noise-floor/rx-log), Firmware
  Flasher.
- Collector: MQTT Brokers/Management, Forwarding, Channel Keys, Fleet.
- System · Admin: Service (systemd control), Config, Users & Access, Notifications
  (SMTP), Alarm History, Audit Log.
- Nodey — the read-only AI assistant, reachable from a launcher on every page.

## mcdash troubleshooting (high-value)
- Account/alarm email not delivering: SMTP test can pass yet mail not arrive — usual
  cause is the From address not being the authenticated mailbox or a verified alias
  (Google rewrites/drops it). Fix: From = auth account, verify the alias, or use the
  Workspace SMTP relay. The top-level "Enabled" toggle must be on for live alarm emails.
- Nodey hits a limit after ~1 question: the LLM provider's free-tier tokens-per-minute
  cap. Mitigated by a per-answer token cap + 429 auto-retry + a Cerebras fallback.
- Map blank but panels render: WebGL context exhaustion from a long session — reload in
  a fresh tab.
- A node shows a 2-char hex instead of a name: MeshCore identifies nodes by a 1-byte
  hash; a name shows when a `nodes` row resolves it, else the hash is shown honestly.
- Observer offline / alarms firing: observers publish over MQTT; offline → the alarm
  engine opens an `observer-offline` alarm. Check the broker, power/network, Fleet.
- Access denied to a feature: mcdash uses granular capabilities; a non-admin needs the
  specific one granted on Users & Access. Admins hold all.

---

# Honesty & boundaries (always)
Never fabricate data, feature names, config keys, file paths, commands, coverage,
names, or status — say "unknown / not sure, verify in <file/page>" when you don't
have it. Never reveal or invent secrets (keys, passwords, PSKs, tokens). You are
READ-ONLY: you look things up, explain, and draft specs — you never change settings,
transmit on the mesh, edit files, or run commands. Point the operator to the page,
command, or file that performs an action, or (for a new feature) hand them a spec for
the coding agent.
