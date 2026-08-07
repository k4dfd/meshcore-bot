#!/usr/bin/env python3
"""
Test command for the MeshCore Bot
Handles the 'test' keyword response
"""

import asyncio
import math
import re
from datetime import datetime
from typing import Any, Optional

from ..geocoder import nearest_city
from ..link_quality import DEFAULT_RSSI_FLOOR_DBM, DEFAULT_SNR_FLOOR_DB, assess_link
from ..models import MeshMessage
from ..radio_metrics import format_airtime, signal_bars, signal_meter
from ..response_template import format_piped_template
from ..telemetry_lpp import parse_lpp
from ..utils import calculate_distance, extract_path_node_ids_from_message
from .base_command import BaseCommand


class TestCommand(BaseCommand):
    """Handles the test command.

    Responds to 'test' or 't' with connection info. Supports an optional phrase.
    Can utilize repeater geographic location data to estimate path distance.
    """

    # Plugin metadata
    name = "test"
    keywords = ['test', 't']
    description = "Responds to 'test' or 't' with connection info"
    category = "basic"
    # We build a personalized, multi-part reply in execute() (greeting + metrics +
    # distance), so the keyword-matcher must defer to execute() rather than
    # pre-rendering/sending the response_format itself.
    handles_own_response = True

    # Documentation
    short_description = "Get test response with connection info"
    usage = "test [phrase]"
    examples = ["test", "t hello world"]

    def __init__(self, bot):
        super().__init__(bot)
        self.test_enabled = self.get_config_value('Test_Command', 'enabled', fallback=True, value_type='bool')
        # Get bot location from config for geographic proximity calculations
        self.geographic_guessing_enabled = False
        self.bot_latitude = None
        self.bot_longitude = None

        # Get recency/proximity weighting from config (same as path command)
        recency_weight = bot.config.getfloat('Path_Command', 'recency_weight', fallback=0.2)
        self.recency_weight = max(0.0, min(1.0, recency_weight))  # Clamp to 0.0-1.0
        self.proximity_weight = 1.0 - self.recency_weight

        try:
            # Try to get location from Bot section
            if bot.config.has_section('Bot'):
                lat = bot.config.getfloat('Bot', 'bot_latitude', fallback=None)
                lon = bot.config.getfloat('Bot', 'bot_longitude', fallback=None)

                if lat is not None and lon is not None:
                    # Validate coordinates
                    if -90 <= lat <= 90 and -180 <= lon <= 180:
                        self.bot_latitude = lat
                        self.bot_longitude = lon
                        self.geographic_guessing_enabled = True
                        self.logger.debug(f"Test command: Geographic proximity enabled with bot location: {lat:.4f}, {lon:.4f}")
                    else:
                        self.logger.warning(f"Invalid bot coordinates in config: {lat}, {lon}")
        except Exception as e:
            self.logger.warning(f"Error reading bot location from config: {e}")

        # ── Enhanced link-report options (all additive; safe defaults) ──────────
        # Nearest-city naming (offline geocoder). A non-empty bot_city_label
        # forces the bot's displayed city (e.g. "Radford, VA") instead of the
        # geocoded nearest place.
        self.geocode_enabled = self.get_config_value(
            'Test_Command', 'geocode_enabled', fallback=True, value_type='bool')
        self.bot_city_label = self._strip_quotes_from_config(
            self.get_config_value('Test_Command', 'bot_city_label', fallback='') or '').strip()
        # Bounded, best-effort telemetry pull from the sender's node.
        self.telemetry_in_test = self.get_config_value(
            'Test_Command', 'telemetry_in_test', fallback=True, value_type='bool')
        try:
            self.telemetry_timeout = max(
                1.0, min(8.0, bot.config.getfloat(
                    'Test_Command', 'telemetry_timeout_seconds', fallback=2.0)))
        except Exception:
            self.telemetry_timeout = 2.0
        # Link-quality floors (SF7/BW62.5 defaults; override for other presets).
        try:
            self.snr_floor_db = bot.config.getfloat(
                'Test_Command', 'snr_floor_db', fallback=DEFAULT_SNR_FLOOR_DB)
        except Exception:
            self.snr_floor_db = DEFAULT_SNR_FLOOR_DB
        try:
            self.rssi_floor_dbm = bot.config.getfloat(
                'Test_Command', 'rssi_floor_dbm', fallback=DEFAULT_RSSI_FLOOR_DBM)
        except Exception:
            self.rssi_floor_dbm = DEFAULT_RSSI_FLOOR_DBM
        self.full_enabled = self.get_config_value(
            'Test_Command', 'full_report_enabled', fallback=True, value_type='bool')
        self._bot_city_cache: Optional[str] = None
        # Per-execute stash for telemetry (set in execute(), read in format).
        self._node_batt: Optional[float] = None
        self._node_temp: Optional[float] = None
        self._node_humidity: Optional[float] = None

    def can_execute(self, message: MeshMessage, skip_channel_check: bool = False) -> bool:
        """Check if this command can be executed with the given message.

        Args:
            message: The message triggering the command.

        Returns:
            bool: True if command is enabled and checks pass, False otherwise.
        """
        if not self.test_enabled:
            return False
        return super().can_execute(message)

    def get_help_text(self) -> str:
        """Get help text for the command.

        Returns:
            str: Help text string.
        """
        return self.translate('commands.test.help')

    def clean_content(self, content: str) -> str:
        """Clean content by removing control characters and normalizing whitespace.

        Args:
            content: The raw message content.

        Returns:
            str: Cleaned and normalized content string.
        """
        # Remove control characters (except newline, tab, carriage return)
        cleaned = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', content)
        # Normalize whitespace
        cleaned = ' '.join(cleaned.split())
        return cleaned

    def _extract_phrase(self, raw_content: str) -> Optional[str]:
        """Single source of truth for test-keyword parsing.

        Returns the phrase after 'test'/'t' ('' for a bare command), or None when
        the message is not a test command at all. Used by matches_keyword,
        _assemble_fields, and _is_full_request so the parse never drifts.
        """
        content = self.clean_content(raw_content)
        if content.startswith('!'):
            content = content[1:].strip()
        low = content.lower()
        if low in ("test", "t"):
            return ""
        if content.startswith('test ') or content.startswith('Test '):
            phrase = content[5:].strip()
            return phrase if phrase else None  # "test " with no phrase isn't a match
        if content.startswith('t ') or content.startswith('T '):
            phrase = content[2:].strip()
            return phrase if phrase else None
        return None

    def matches_keyword(self, message: MeshMessage) -> bool:
        """Match 'test', 't', 'test <phrase>', or 't <phrase>' (see _extract_phrase)."""
        return self._extract_phrase(message.content) is not None

    # Metrics only — the personalized greeting ("Hi <node>, here's your test
    # results:") is prepended in _build_default_report, so this no longer carries
    # the sender/ack prefix.
    # Compact one-line default: the TRUE green signal meter (not the 📶 emoji), and
    # trimmed to fit a single mesh message (~145-byte budget) so a `test` is just the
    # greeting + one clean line — the hash / battery / named-path detail lives in
    # `test full`.
    DEFAULT_FORMAT = (
        "{hops_label} | SNR {snr}dB RSSI {rssi}dBm {signal_meter} "
        "| {link_quality} link ({link_margin}dB) | reached {bot_city} {reach_distance} "
        "| {timestamp}{tune_nudge}"
    )

    def get_response_format(self) -> Optional[str]:
        """Get the response format from config, falling back to the built-in default.

        Returns:
            Optional[str]: The configured or default response format string.
        """
        if self.bot.config.has_section('Test_Command'):
            raw = self.bot.config.get('Test_Command', 'response_format', fallback='')
            if raw:
                cleaned = self._strip_quotes_from_config(raw).strip()
                if cleaned:
                    return cleaned
        if self.bot.config.has_section('Keywords'):
            format_str = self.bot.config.get('Keywords', 'test', fallback=None)
            if format_str:
                return self._strip_quotes_from_config(format_str)
        return self.DEFAULT_FORMAT

    def _extract_path_node_ids(self, message: MeshMessage) -> list[str]:
        """Extract path node IDs from message. Prefers routing_info.path_nodes (multi-byte); else parses message.path.

        Returns:
            List[str]: List of node IDs (2, 4, or 6 hex chars per node, uppercase).
        """
        return extract_path_node_ids_from_message(message)


    def _lookup_repeater_location(self, node_id: str, path_context: Optional[list[str]] = None) -> Optional[tuple[float, float]]:
        """Look up repeater location for a node ID using geographic proximity selection when path context is available.

        Args:
            node_id: The node ID to look up.
            path_context: Optional list of all node IDs in the path for context-aware selection.

        Returns:
            Optional[Tuple[float, float]]: (latitude, longitude) or None if not found.
        """
        try:
            if not hasattr(self.bot, 'db_manager'):
                return None

            # Query for all repeaters with matching prefix
            query = '''
                SELECT latitude, longitude, public_key, name,
                       last_advert_timestamp, last_heard, advert_count, is_starred
                FROM complete_contact_tracking
                WHERE public_key LIKE ? AND role IN ('repeater', 'roomserver')
                AND latitude IS NOT NULL AND longitude IS NOT NULL
                AND latitude != 0 AND longitude != 0
            '''

            prefix_pattern = f"{node_id}%"
            results = self.bot.db_manager.execute_query(query, (prefix_pattern,))

            if not results or len(results) == 0:
                return None

            # Convert to list of dicts for processing
            repeaters = []
            for row in results:
                repeaters.append({
                    'latitude': row.get('latitude'),
                    'longitude': row.get('longitude'),
                    'public_key': row.get('public_key'),
                    'name': row.get('name'),
                    'last_advert_timestamp': row.get('last_advert_timestamp'),
                    'last_heard': row.get('last_heard'),
                    'advert_count': row.get('advert_count', 0),
                    'is_starred': bool(row.get('is_starred', 0))
                })

            # If only one repeater, return it
            if len(repeaters) == 1:
                r = repeaters[0]
                return (float(r['latitude']), float(r['longitude']))

            # Multiple repeaters - use geographic proximity selection if path context available
            if path_context and len(path_context) > 1:
                # Get sender location if available (for first repeater selection)
                sender_location = self._get_sender_location()
                selected = self._select_by_path_proximity(repeaters, node_id, path_context, sender_location)
                if selected:
                    return (float(selected['latitude']), float(selected['longitude']))

            # Fall back to most recent repeater
            scored = self._calculate_recency_weighted_scores(repeaters)
            if scored:
                best_repeater = scored[0][0]
                return (float(best_repeater['latitude']), float(best_repeater['longitude']))

            return None
        except Exception as e:
            self.logger.debug(f"Error looking up repeater location for {node_id}: {e}")
            return None

    def _get_sender_location(self) -> Optional[tuple[float, float]]:
        """Get sender location from current message if available.

        Returns:
            Optional[Tuple[float, float]]: (latitude, longitude) or None if unavailable/error.
        """
        try:
            if not hasattr(self, '_current_message') or not self._current_message:
                return None

            sender_pubkey = getattr(self._current_message, 'sender_pubkey', None) or ''
            sender_name = getattr(self._current_message, 'sender_id', None) or ''
            if not sender_pubkey and not sender_name:
                return None

            # Channel messages carry NO pubkey (sender is identified by name in the
            # text), so match on public_key OR name — most recent advert/heard wins.
            query = '''
                SELECT latitude, longitude
                FROM complete_contact_tracking
                WHERE ( (? != '' AND public_key = ?) OR (? != '' AND name = ?) )
                AND latitude IS NOT NULL AND longitude IS NOT NULL
                AND latitude != 0 AND longitude != 0
                ORDER BY COALESCE(last_advert_timestamp, last_heard) DESC
                LIMIT 1
            '''

            results = self.bot.db_manager.execute_query(
                query, (sender_pubkey, sender_pubkey, sender_name, sender_name))

            if results:
                row = results[0]
                return (row['latitude'], row['longitude'])
            return None
        except Exception as e:
            self.logger.debug(f"Error getting sender location: {e}")
            return None

    def _calculate_recency_weighted_scores(self, repeaters: list[dict[str, Any]]) -> list[tuple[dict[str, Any], float]]:
        """Calculate recency-weighted scores for repeaters (0.0 to 1.0, higher = more recent).

        Args:
            repeaters: List of repeater dictionaries.

        Returns:
            List[Tuple[Dict[str, Any], float]]: List of (repeater, score) tuples sorting by score descending.
        """
        scored_repeaters = []
        now = datetime.now()

        for repeater in repeaters:
            most_recent_time = None

            # Check last_heard
            last_heard = repeater.get('last_heard')
            if last_heard:
                try:
                    if isinstance(last_heard, str):
                        dt = datetime.fromisoformat(last_heard.replace('Z', '+00:00'))
                    else:
                        dt = last_heard
                    if most_recent_time is None or dt > most_recent_time:
                        most_recent_time = dt
                except:
                    pass

            # Check last_advert_timestamp
            last_advert = repeater.get('last_advert_timestamp')
            if last_advert:
                try:
                    if isinstance(last_advert, str):
                        dt = datetime.fromisoformat(last_advert.replace('Z', '+00:00'))
                    else:
                        dt = last_advert
                    if most_recent_time is None or dt > most_recent_time:
                        most_recent_time = dt
                except:
                    pass

            if most_recent_time is None:
                recency_score = 0.1
            else:
                hours_ago = (now - most_recent_time).total_seconds() / 3600.0
                recency_score = math.exp(-hours_ago / 12.0)
                recency_score = max(0.0, min(1.0, recency_score))

            scored_repeaters.append((repeater, recency_score))

        # Sort by recency score (highest first)
        scored_repeaters.sort(key=lambda x: x[1], reverse=True)
        return scored_repeaters

    def _get_node_location_simple(self, node_id: str) -> Optional[tuple[float, float]]:
        """Simple lookup without proximity selection - used for reference nodes.

        Args:
            node_id: The node ID to look up.

        Returns:
            Optional[Tuple[float, float]]: (latitude, longitude) or None if not found.
        """
        try:
            if not hasattr(self.bot, 'db_manager'):
                return None

            query = '''
                SELECT latitude, longitude
                FROM complete_contact_tracking
                WHERE public_key LIKE ? AND role IN ('repeater', 'roomserver')
                AND latitude IS NOT NULL AND longitude IS NOT NULL
                AND latitude != 0 AND longitude != 0
                ORDER BY is_starred DESC, COALESCE(last_advert_timestamp, last_heard) DESC
                LIMIT 1
            '''

            prefix_pattern = f"{node_id}%"
            results = self.bot.db_manager.execute_query(query, (prefix_pattern,))

            if results and len(results) > 0:
                row = results[0]
                lat = row.get('latitude')
                lon = row.get('longitude')
                if lat is not None and lon is not None:
                    return (float(lat), float(lon))

            return None
        except Exception as e:
            self.logger.debug(f"Error in simple location lookup for {node_id}: {e}")
            return None

    def _select_by_path_proximity(self, repeaters: list[dict[str, Any]], node_id: str, path_context: list[str], sender_location: Optional[tuple[float, float]] = None) -> Optional[dict[str, Any]]:
        """Select repeater based on proximity to previous/next nodes in path.

        Args:
            repeaters: List of candidate repeaters.
            node_id: Current node ID being resolved.
            path_context: Full path of node IDs.
            sender_location: Optional sender location for first hop optimization.

        Returns:
            Optional[Dict[str, Any]]: Selected repeater dict or None.
        """
        try:
            # Filter by recency first
            scored_repeaters = self._calculate_recency_weighted_scores(repeaters)
            min_recency_threshold = 0.01  # Approximately 55 hours ago or less
            recent_repeaters = [r for r, score in scored_repeaters if score >= min_recency_threshold]

            if not recent_repeaters:
                return None

            # Find current node position in path
            current_index = path_context.index(node_id) if node_id in path_context else -1
            if current_index == -1:
                return None

            # Get previous and next node locations
            prev_location = None
            next_location = None

            if current_index > 0:
                prev_node_id = path_context[current_index - 1]
                prev_location = self._get_node_location_simple(prev_node_id)

            if current_index < len(path_context) - 1:
                next_node_id = path_context[current_index + 1]
                next_location = self._get_node_location_simple(next_node_id)

            # For the first repeater in the path, prioritize sender location as the source
            # The first repeater's primary job is to receive from the sender, so use sender location if available
            is_first_repeater = (current_index == 0)
            if is_first_repeater and sender_location:
                # For first repeater, use sender location only (not averaged with next node)
                self.logger.debug(f"Test command: Using sender location for proximity calculation of first repeater: {sender_location[0]:.4f}, {sender_location[1]:.4f}")
                return self._select_by_single_proximity(recent_repeaters, sender_location, "sender")

            # For the last repeater in the path, prioritize bot location as the destination
            # The last repeater's primary job is to deliver to the bot, so use bot location only
            is_last_repeater = (current_index == len(path_context) - 1)
            if is_last_repeater and self.geographic_guessing_enabled:
                if self.bot_latitude is not None and self.bot_longitude is not None:
                    # For last repeater, use bot location only (not averaged with previous node)
                    bot_location = (self.bot_latitude, self.bot_longitude)
                    self.logger.debug(f"Test command: Using bot location for proximity calculation of last repeater: {self.bot_latitude:.4f}, {self.bot_longitude:.4f}")
                    return self._select_by_single_proximity(recent_repeaters, bot_location, "bot")

            # For non-first/non-last repeaters, use both previous and next locations if available
            # Use proximity selection
            if prev_location and next_location:
                return self._select_by_dual_proximity(recent_repeaters, prev_location, next_location)
            elif prev_location:
                return self._select_by_single_proximity(recent_repeaters, prev_location, "previous")
            elif next_location:
                return self._select_by_single_proximity(recent_repeaters, next_location, "next")
            else:
                return None

        except Exception as e:
            self.logger.debug(f"Error in path proximity selection: {e}")
            return None

    def _select_by_dual_proximity(self, repeaters: list[dict[str, Any]], prev_location: tuple[float, float], next_location: tuple[float, float]) -> Optional[dict[str, Any]]:
        """Select repeater based on proximity to both previous and next nodes.

        Args:
            repeaters: List of candidate repeaters.
            prev_location: Coordinates of previous node.
            next_location: Coordinates of next node.

        Returns:
            Optional[Dict[str, Any]]: Best matching repeater or None.
        """
        scored_repeaters = self._calculate_recency_weighted_scores(repeaters)
        min_recency_threshold = 0.01
        scored_repeaters = [(r, score) for r, score in scored_repeaters if score >= min_recency_threshold]

        if not scored_repeaters:
            return None

        best_repeater = None
        best_combined_score = 0.0

        for repeater, recency_score in scored_repeaters:
            # Calculate distance to previous node
            prev_distance = calculate_distance(
                prev_location[0], prev_location[1],
                repeater['latitude'], repeater['longitude']
            )

            # Calculate distance to next node
            next_distance = calculate_distance(
                next_location[0], next_location[1],
                repeater['latitude'], repeater['longitude']
            )

            # Combined proximity score (lower distance = higher score)
            avg_distance = (prev_distance + next_distance) / 2
            normalized_distance = min(avg_distance / 1000.0, 1.0)
            proximity_score = 1.0 - normalized_distance

            # Use configurable weighting (from Path_Command config)
            combined_score = (recency_score * self.recency_weight) + (proximity_score * self.proximity_weight)

            # Apply star bias multiplier if repeater is starred (use same config as path command)
            star_bias_multiplier = self.bot.config.getfloat('Path_Command', 'star_bias_multiplier', fallback=2.5)
            if repeater.get('is_starred', False):
                combined_score *= star_bias_multiplier

            if combined_score > best_combined_score:
                best_combined_score = combined_score
                best_repeater = repeater

        return best_repeater

    def _select_by_single_proximity(self, repeaters: list[dict[str, Any]], reference_location: tuple[float, float], direction: str = "unknown") -> Optional[dict[str, Any]]:
        """Select repeater based on proximity to single reference node.

        Args:
            repeaters: List of candidate repeaters.
            reference_location: Coordinates of reference node (sender, bot, next, previous).
            direction: Direction indicator ('sender', 'bot', 'next', 'previous').

        Returns:
            Optional[Dict[str, Any]]: Best matching repeater or None.
        """
        scored_repeaters = self._calculate_recency_weighted_scores(repeaters)
        min_recency_threshold = 0.01
        scored_repeaters = [(r, score) for r, score in scored_repeaters if score >= min_recency_threshold]

        if not scored_repeaters:
            return None

        # For last repeater (direction="bot") or first repeater (direction="sender"), use 100% proximity (0% recency)
        # The final hop to the bot and first hop from sender should prioritize distance above all else
        # Recency still matters for filtering (min_recency_threshold), but not for scoring
        if direction == "bot" or direction == "sender":
            proximity_weight = 1.0
            recency_weight = 0.0
        else:
            # Use configurable weighting for other cases (from Path_Command config)
            proximity_weight = self.proximity_weight
            recency_weight = self.recency_weight

        best_repeater = None
        best_combined_score = 0.0

        for repeater, recency_score in scored_repeaters:
            distance = calculate_distance(
                reference_location[0], reference_location[1],
                repeater['latitude'], repeater['longitude']
            )

            # Proximity score (closer = higher score)
            normalized_distance = min(distance / 1000.0, 1.0)
            proximity_score = 1.0 - normalized_distance

            # Use appropriate weighting based on direction
            combined_score = (recency_score * recency_weight) + (proximity_score * proximity_weight)

            # Apply star bias multiplier if repeater is starred (use same config as path command)
            star_bias_multiplier = self.bot.config.getfloat('Path_Command', 'star_bias_multiplier', fallback=2.5)
            if repeater.get('is_starred', False):
                combined_score *= star_bias_multiplier

            if combined_score > best_combined_score:
                best_combined_score = combined_score
                best_repeater = repeater

        return best_repeater

    def _calculate_path_distance(self, message: MeshMessage) -> str:
        """Calculate total distance along path (sum of distances between consecutive repeaters with locations).

        Args:
            message: The message containing the path.

        Returns:
            str: Formatted distance string used in response.
        """
        node_ids = self._extract_path_node_ids(message)
        if len(node_ids) < 2:
            routing_info = getattr(message, 'routing_info', None)
            is_direct = (
                (routing_info is not None and routing_info.get('path_length', 0) == 0)
                or not message.path
                or "Direct" in (message.path or "")
                or "0 hops" in (message.path or "")
            )
            if is_direct:
                return "N/A"  # Direct connection, no path to calculate
            return ""  # Path exists but insufficient nodes

        total_distance = 0.0
        valid_segments = 0
        skipped_nodes = 0

        # Get locations for all nodes using path context for proximity selection
        locations = []
        for i, node_id in enumerate(node_ids):
            location = self._lookup_repeater_location(node_id, path_context=node_ids)
            if location:
                locations.append((node_id, location))
            else:
                skipped_nodes += 1

        # Calculate distances between consecutive nodes with locations
        # This skips nodes without locations but continues the path
        for i in range(len(locations) - 1):
            prev_node_id, prev_location = locations[i]
            next_node_id, next_location = locations[i + 1]

            # Calculate distance between consecutive repeaters with locations
            distance = calculate_distance(
                prev_location[0], prev_location[1],
                next_location[0], next_location[1]
            )
            total_distance += distance
            valid_segments += 1

        if valid_segments == 0:
            return ""  # No valid segments found

        # Format the result compactly
        total_mi = self._km_to_mi(total_distance)
        if skipped_nodes > 0:
            return f"{total_mi:.1f}mi ({valid_segments} segs, {skipped_nodes} no-loc)"
        else:
            return f"{total_mi:.1f}mi ({valid_segments} segs)"

    def _calculate_firstlast_distance(self, message: MeshMessage) -> str:
        """Calculate straight-line distance between first and last repeater in path.

        Args:
            message: The message containing the path.

        Returns:
            str: Formatted distance string used in response.
        """
        node_ids = self._extract_path_node_ids(message)
        if len(node_ids) < 2:
            routing_info = getattr(message, 'routing_info', None)
            is_direct = (
                (routing_info is not None and routing_info.get('path_length', 0) == 0)
                or not message.path
                or "Direct" in (message.path or "")
                or "0 hops" in (message.path or "")
            )
            if is_direct:
                return "N/A"  # Direct connection, no path to calculate
            return ""  # Path exists but insufficient nodes

        # Get first and last node IDs
        first_node_id = node_ids[0]
        last_node_id = node_ids[-1]

        # Use path context for better selection when multiple repeaters share prefix
        first_location = self._lookup_repeater_location(first_node_id, path_context=node_ids)
        last_location = self._lookup_repeater_location(last_node_id, path_context=node_ids)

        # Both locations must be available
        if not first_location or not last_location:
            return ""  # Fail if either location is missing

        # Calculate straight-line distance
        distance = calculate_distance(
            first_location[0], first_location[1],
            last_location[0], last_location[1]
        )

        return f"{self._km_to_mi(distance):.1f}mi"

    # ── Enhanced link-report helpers ────────────────────────────────────────
    def _hops_value(self, message: MeshMessage) -> Optional[int]:
        """Best hop count: message.hops, else routing_info.path_length/len(path_nodes)."""
        if getattr(message, 'hops', None) is not None:
            return message.hops
        routing_info = getattr(message, 'routing_info', None)
        if routing_info is not None:
            hv = routing_info.get('path_length')
            if hv is None and routing_info.get('path_nodes'):
                hv = len(routing_info['path_nodes'])
            return hv
        return None

    def _bot_city(self) -> str:
        """Bot's displayed city: config label override, else geocoded nearest place."""
        if self.bot_city_label:
            return self.bot_city_label
        if self._bot_city_cache is not None:
            return self._bot_city_cache
        city = ""
        if self.geocode_enabled and self.bot_latitude is not None and self.bot_longitude is not None:
            city = nearest_city(self.bot_latitude, self.bot_longitude) or ""
        self._bot_city_cache = city
        return city

    def _sender_city(self, message: MeshMessage) -> str:
        """Nearest city of the sender's node (needs an advertised location)."""
        if not self.geocode_enabled:
            return ""
        loc = self._get_sender_location()
        if not loc:
            return ""
        return nearest_city(loc[0], loc[1]) or ""

    def _reach_distance_km(self, message: MeshMessage) -> Optional[float]:
        """Straight-line km from the sender's node to the bot (None if unknown)."""
        loc = self._get_sender_location()
        if not loc or self.bot_latitude is None or self.bot_longitude is None:
            return None
        try:
            return calculate_distance(loc[0], loc[1], self.bot_latitude, self.bot_longitude)
        except Exception:
            return None

    @staticmethod
    def _km_to_mi(km: float) -> float:
        return km * 0.621371

    def _reach_distance_str(self, message: MeshMessage) -> str:
        km = self._reach_distance_km(message)
        return f"~{self._km_to_mi(km):.1f} mi" if km is not None else ""

    def _link(self, message: MeshMessage):
        return assess_link(
            getattr(message, 'snr', None),
            getattr(message, 'rssi', None),
            self._hops_value(message),
            snr_floor_db=self.snr_floor_db,
            rssi_floor_dbm=self.rssi_floor_dbm,
        )

    def _path_bytes_str(self, message: MeshMessage) -> str:
        routing_info = getattr(message, 'routing_info', None) or {}
        bph = routing_info.get('bytes_per_hop')
        hops = self._hops_value(message)
        if bph and hops:
            return f"{int(bph) * int(hops)}B path"
        path_hex = routing_info.get('path_hex')
        if path_hex:
            return f"{len(path_hex) // 2}B path"
        return ""

    def _lookup_repeater_name(self, node_id: str) -> Optional[str]:
        """Most-recent repeater/roomserver name for a node-id prefix, or None."""
        try:
            if not hasattr(self.bot, 'db_manager'):
                return None
            query = '''
                SELECT name FROM complete_contact_tracking
                WHERE public_key LIKE ? AND role IN ('repeater', 'roomserver')
                AND name IS NOT NULL AND name != ''
                ORDER BY is_starred DESC, COALESCE(last_advert_timestamp, last_heard) DESC
                LIMIT 1
            '''
            results = self.bot.db_manager.execute_query(query, (f"{node_id}%",))
            if results:
                return results[0].get('name')
            return None
        except Exception as e:
            self.logger.debug(f"repeater name lookup failed for {node_id}: {e}")
            return None

    def _path_named(self, message: MeshMessage, include_city: bool = True) -> str:
        """Human path from the multi-byte path nodes.

        include_city=True → 'NAME (City) > NAME (City)'; False → 'NAME > NAME'
        (the plain form is the length fallback when the city form won't fit).
        """
        node_ids = self._extract_path_node_ids(message)
        if not node_ids:
            return ""
        parts = []
        for nid in node_ids:
            label = self._lookup_repeater_name(nid) or nid
            if include_city and self.geocode_enabled:
                loc = self._lookup_repeater_location(nid, path_context=node_ids)
                city = nearest_city(loc[0], loc[1]) if loc else None
                parts.append(f"{label} ({city})" if city else label)
            else:
                parts.append(label)
        return " > ".join(parts)

    def _split_mode(self, phrase: str) -> tuple[bool, str]:
        """Detect a leading verbosity token ('full'/'detail'/'-v') → (full, phrase)."""
        p = (phrase or "").strip()
        low = p.lower()
        for tok in ("full", "details", "detail", "verbose", "-v", "-f"):
            if low == tok:
                return True, ""
            if low.startswith(tok + " "):
                return True, p[len(tok):].strip()
        return False, p

    def _find_contact(self, pubkey: Optional[str]) -> Optional[dict]:
        """Locate the sender's contact dict for a telemetry request (by pubkey)."""
        if not pubkey:
            return None
        mc = getattr(self.bot, 'meshcore', None)
        contacts = getattr(mc, 'contacts', None) or {}
        try:
            for _k, cd in contacts.items():
                pk = cd.get('public_key', '')
                # sender_pubkey is a full key; match exact, or a stored full key
                # that our (possibly prefix) value begins — never the reverse, so
                # a short prefix can't resolve an unrelated node.
                if pk and (pk == pubkey or pk.startswith(pubkey)):
                    return cd
        except Exception:
            return None
        return None

    async def _pull_node_telemetry(self, message: MeshMessage) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """Bounded, best-effort telemetry pull from the sender's node.

        Returns (battery_v, temp_c, humidity_pct); any of them None when the node
        doesn't answer in time (repeaters need login; some nodes disable telemetry)
        or on any error — the caller then shows an honest "—".
        """
        if not self.telemetry_in_test:
            return (None, None, None)
        try:
            contact = self._find_contact(getattr(message, 'sender_pubkey', None))
            if not contact:
                return (None, None, None)
            mc = self.bot.meshcore
            lpp = await asyncio.wait_for(
                mc.commands.req_telemetry_sync(contact, timeout=self.telemetry_timeout),
                timeout=self.telemetry_timeout + 0.5,  # hard ceiling just above the lib's own wait
            )
            parsed = parse_lpp(lpp)
            return (parsed['battery_v'], parsed['temp_c'], parsed['humidity_pct'])
        except Exception as e:  # asyncio.TimeoutError is an Exception; CancelledError (BaseException) still propagates
            self.logger.debug(f"test telemetry pull failed/timed out: {e}")
            return (None, None, None)

    def _enhanced_fields(self, message: MeshMessage) -> dict[str, str]:
        """Derived display fields for the response template (all honest-empty)."""
        link = self._link(message)
        rssi = getattr(message, 'rssi', None)
        reach = self._reach_distance_str(message)
        bot_city = self._bot_city()
        bot_name = self.bot.config.get('Bot', 'bot_name', fallback='') or 'the bot'
        batt = self._node_batt
        temp = self._node_temp
        margin = link.margin_db
        # "Reached <bot> in <City, ST>" (drop "in <city>" when the city is unknown).
        reached = f"Reached {bot_name} in {bot_city}" if bot_city else f"Reached {bot_name}"
        routing = getattr(message, 'routing_info', None) or {}
        hops = self._hops_value(message)
        if hops == 0:
            route_type = "direct"
        else:
            rt = routing.get('route_type')
            route_type = str(rt).lower() if rt else "routed"
        # Path hash size (1B/2B per hop). Only meaningful on routed messages — a
        # direct (0-hop) packet has no path hashes.
        bph = routing.get('bytes_per_hop')
        if bph and hops and hops > 0:
            hash_bytes = f"Path Hash: {int(bph)}-byte"
            hash_suffix = f" | Path Hash: {int(bph)}-byte"
        else:
            hash_bytes = ""
            hash_suffix = ""
        return {
            'rssi': str(rssi) if rssi is not None else '—',
            'signal_icon': signal_bars(rssi),   # compact 📶
            'signal_meter': signal_meter(rssi),  # green level meter for full report
            'bot_city': bot_city or bot_name,
            'bot_name': bot_name,
            'reached': reached,
            'sender_city': self._sender_city(message),
            'reach_distance': reach,
            # phrased for the compact line; the full report spells it out too
            'reach_suffix': f" ({reach} from your node)" if reach else "",
            'link_quality': link.verdict,
            'quality_hint': link.hint,
            # Opt-in nudge: only surface "reply 'test full'" when the link is weak
            # enough that the diagnostic advice would actually help — keeps strong
            # links clean and the mesh quiet.
            'tune_nudge': (" · reply 'test full' for tuning tips"
                           if link.verdict in ("Fair", "Weak", "Marginal") else ""),
            'link_margin': f"{margin}" if margin is not None else '—',
            'route_type': route_type,
            'hash_bytes': hash_bytes,
            'hash_suffix': hash_suffix,
            'airtime': format_airtime(routing.get('payload_length')),
            'path_bytes': self._path_bytes_str(message),
            'path_named': self._path_named(message),
            'node_batt': f"{batt:.2f}" if batt is not None else '—',
            # Telemetry temperature is Celsius (CayenneLPP); display in Fahrenheit (US/imperial).
            'node_temp': f"{temp * 9 / 5 + 32:.1f}" if temp is not None else '—',
            'batt_suffix': f" | batt {batt:.2f}V" if batt is not None else "",
        }

    def _split_to_budget(self, text: str, budget: int) -> list[str]:
        """Split text into <=budget-byte chunks, preferring ' | ' boundaries."""
        if budget <= 0 or len(text.encode('utf-8')) <= budget:
            return [text]
        parts: list[str] = []
        cur = ''
        for seg in text.split(' | '):
            candidate = seg if not cur else f"{cur} | {seg}"
            if len(candidate.encode('utf-8')) <= budget:
                cur = candidate
                continue
            if cur:
                parts.append(cur)
                cur = ''
            if len(seg.encode('utf-8')) > budget:
                parts.append(self._clip_to_budget(seg, budget))
            else:
                cur = seg
        if cur:
            parts.append(cur)
        return parts

    def _build_default_report(self, message: MeshMessage) -> list[str]:
        """Personalized, auto-chunked default report:
        "Hi <node>, here's your test results:" + the metrics line (split if long)."""
        fields = self._assemble_fields(message)
        raw_sender = fields.get('sender')
        sender = raw_sender or 'there'
        greeting = f"Hi {sender}, here's your test results:"
        metrics = format_piped_template(
            self.get_response_format(),
            fields,
            message=message,
            logger=self.logger,
            prefix_hex_chars=getattr(self.bot, 'prefix_hex_chars', 2),
        )
        try:
            budget = int(self.get_max_message_length(message))
        except Exception:
            budget = 150
        combined = f"{greeting} {metrics}"
        if budget and len(combined.encode('utf-8')) <= budget:
            return [combined]  # fits one message — greeting already personalizes it
        # Split reply: the results ride in a separate message from the greeting, so lead
        # them with the node tag — that message is never nameless even if the greeting
        # transmission is lost. Skipped when the sender name could not be resolved.
        metrics_out = f"@[{raw_sender}] | {metrics}" if raw_sender else metrics
        return [greeting] + self._split_to_budget(metrics_out, budget)

    def _clip_to_budget(self, text: str, budget: int) -> str:
        """UTF-8 byte-aware truncation to `budget` bytes (…-terminated) so a chunk
        can't exceed the firmware cipher-block limit and get silently cut."""
        if budget <= 0:
            return text
        encoded = text.encode('utf-8')
        if len(encoded) <= budget:
            return text
        clipped = encoded[:max(0, budget - 3)].decode('utf-8', 'ignore').rstrip()
        return clipped + "…"

    def _build_full_report(self, message: MeshMessage, base_fields: dict[str, str]) -> list[str]:
        """Multi-line detailed report for 'test full' (empty lines dropped).

        Each line is budgeted to the message's max length so a long named path
        can't overflow a chunk; the Path line first sheds its (City) suffixes,
        then truncates as a last resort.
        """
        ef = base_fields
        try:
            budget = int(self.get_max_message_length(message))
        except Exception:
            budget = 150
        sender = ef.get('sender') or 'node'
        reach = ef.get('reach_distance')
        snr = ef.get('snr')
        rssi = ef.get('rssi')
        rt = ef.get('route_type')
        at = ef.get('airtime')
        hb = ef.get('hash_bytes')
        greeting = f"Hi {sender}, here's your test results:"
        line1 = f"{ef.get('reached')}" + (f", {reach} from your node" if reach else "")
        line2 = (
            f"Link: {ef.get('hops_label')}"
            + (f" ({rt})" if rt else "")
            + (f" · {hb}" if hb else "")
            + f" · SNR {snr}dB · RSSI {rssi}dBm {ef.get('signal_meter')} · "
            + f"{ef.get('link_quality')} (margin {ef.get('link_margin')}dB)"
            + (f" · air {at}" if at else "")
        )
        pn = ef.get('path_named')
        pb = ef.get('path_bytes')
        line3 = ""
        if pn:
            line3 = f"Path: {pn}" + (f" · {pb}" if pb else "")
            if budget and len(line3.encode('utf-8')) > budget:
                # Too long with cities — fall back to plain node names before clipping.
                pn_plain = self._path_named(message, include_city=False)
                line3 = f"Path: {pn_plain}" + (f" · {pb}" if pb else "")
        elif pb:
            line3 = f"Path: {pb}"
        hint = ef.get('quality_hint')
        line4 = f"Tune: {hint}" if hint else ""
        node = ""
        if self._node_batt is not None or self._node_temp is not None:
            node = f"Node: batt {ef.get('node_batt')}V · {ef.get('node_temp')}°F"
        return [self._clip_to_budget(ln, budget)
                for ln in (greeting, line1, line2, line3, line4, node) if ln]

    def _assemble_fields(self, message: MeshMessage) -> dict[str, str]:
        """Build the complete template field set (core + enhanced) for a message."""
        phrase = self._extract_phrase(message.content) or ""
        # Strip a leading verbosity token ('full'/'detail') so it isn't echoed.
        _full, phrase = self._split_mode(phrase)

        hops_val = self._hops_value(message)
        hops_str = str(hops_val) if hops_val is not None else "?"
        if hops_val is None:
            hops_label = "?"
        elif hops_val == 1:
            hops_label = "1 hop"
        else:
            hops_label = f"{hops_val} hops"
        phrase_part = f": {phrase}" if phrase else ""
        fields = {
            'sender': message.sender_id or self.translate('common.unknown_sender'),
            'phrase': phrase,
            'phrase_part': phrase_part,
            'connection_info': self.build_enhanced_connection_info(message),
            'path': self.get_path_display_string(message),
            'hops': hops_str,
            'hops_label': hops_label,
            'timestamp': self.format_timestamp(message),
            'elapsed': self.format_elapsed(message),
            'snr': str(message.snr) if message.snr is not None else '—',
            'path_distance': self._calculate_path_distance(message) or '',
            'firstlast_distance': self._calculate_firstlast_distance(message) or '',
        }
        fields.update(self._enhanced_fields(message))
        return fields

    def format_response(self, message: MeshMessage, response_format: str) -> str:
        """Render the single-line response from the configured template."""
        try:
            fields = self._assemble_fields(message)
            return format_piped_template(
                response_format,
                fields,
                message=message,
                logger=self.logger,
                prefix_hex_chars=getattr(self.bot, 'prefix_hex_chars', 2),
            )
        except (KeyError, ValueError) as e:
            self.logger.warning(f"Error formatting test response: {e}")
            return response_format

    async def execute(self, message: MeshMessage) -> bool:
        """Execute the test command.

        Args:
            message: The input message trigger.

        Returns:
            bool: True if execution was successful.
        """
        if not await self.enforce_path_byte_requirement(message, 'Test_Command'):
            return True

        # Store the current message for use in location lookups
        self._current_message = message
        # Reset per-execute telemetry stash.
        self._node_batt = self._node_temp = self._node_humidity = None
        # Bounded, best-effort telemetry pull (included in the default test).
        if self.telemetry_in_test:
            self._node_batt, self._node_temp, self._node_humidity = (
                await self._pull_node_telemetry(message)
            )
        # 'test full' / 'test detail' → multi-part detailed report.
        if self.full_enabled and self._is_full_request(message):
            fields = self._assemble_fields(message)
            chunks = self._build_full_report(message, fields)
            if chunks:
                return await self.send_response_chunked(message, chunks)
        # Default: personalized ("Hi <node>, here's your test results:") + metrics,
        # auto-split into multiple messages when it won't fit one packet.
        return await self.send_response_chunked(message, self._build_default_report(message))

    def _is_full_request(self, message: MeshMessage) -> bool:
        """True when the message is 'test full' / 't detail' etc."""
        phrase = self._extract_phrase(message.content)
        if not phrase:  # None (not a test) or "" (bare test) → not a full request
            return False
        full, _ = self._split_mode(phrase)
        return full
