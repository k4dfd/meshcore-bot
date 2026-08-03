"""Secret-scrub (defense-in-depth), ported from mcdash's Nodey.

Nodey v1 is knowledge-only and feeds no live data, so the primary guarantee is
that the authored knowledge base contains no secrets. This module is the
belt-and-suspenders layer: a key denylist for any dict result, and a
conservative value-level pass for free text (error details, future notes) so a
PSK / private key / API key can never slip into the model context or a log.
"""

from __future__ import annotations

import re
from typing import Any

# Substring match (case-insensitive) — any key CONTAINING one of these is dropped.
_SECRET_SUBSTRINGS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "psk",
    "prv_key",
    "privkey",
    "private_key",
    "ble_pin",
    "api_key",
    "apikey",
    "signing_key",
    "session_secret",
)

# Exact-key match (case-insensitive) — dropped only when the WHOLE key matches.
_SECRET_EXACT: frozenset[str] = frozenset(
    {"pin", "token", "csrf", "jwt", "key", "prv", "session_token"}
)

REDACTED = "[redacted]"
_MAX_DEPTH = 12


def _is_secret_key(key: str) -> bool:
    low = key.lower()
    if low in _SECRET_EXACT:
        return True
    return any(sub in low for sub in _SECRET_SUBSTRINGS)


def scrub_secrets(value: Any, *, _depth: int = 0) -> Any:
    """Recursively replace secret-looking dict keys with ``REDACTED``. Lists are
    walked element-wise. Depth-bounded against pathological/cyclic structures."""
    if _depth > _MAX_DEPTH:
        return None
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            out[key] = REDACTED if _is_secret_key(key) else scrub_secrets(v, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub_secrets(v, _depth=_depth + 1) for v in value]
    return value


# A labelled secret: a secret word followed by is / = / : and a value.
_LABELED_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|psk|api[_-]?key|apikey|secret|private[_-]?key|"
    r"privkey|prv[_-]?key|ble[_-]?pin|session[_-]?secret|signing[_-]?key)\b"
    r"\s*(?:is|=|:)\s*\S+"
)
# A long opaque token (>= 24 chars of key charset), not embedded in a longer word.
_SECRET_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{24,}(?![A-Za-z0-9+/=_-])"
)


def scrub_text(text: Any) -> Any:
    """Redact secret-looking substrings from a free-text string (non-strings pass
    through). Redacts labelled secret assignments (keeping the label so the text
    still reads) and any long opaque token. Idempotent and conservative.

    SCOPE: a safety net over trusted, author-controlled text — not an adversarial
    filter; it does not catch a short, unlabelled secret fragment."""
    if not isinstance(text, str):
        return text
    out = _LABELED_SECRET_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    out = _SECRET_TOKEN_RE.sub(REDACTED, out)
    return out
