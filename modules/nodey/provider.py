"""Thin, sync OpenAI-compatible chat-completions client (requests, streaming).

A synchronous client for the bot's Flask (WSGI) web viewer, built on ``requests``
(already a bot dependency — no new package). A single streaming POST to
``{base_url}/chat/completions`` with a bearer key. Works unchanged against
Cerebras, an mcdash LLM proxy, OpenAI, OpenRouter, Groq, or a local Ollama
``/v1`` endpoint — only the base_url / model / key differ (all config-driven).

v1 is knowledge-only (no tools), so ``stream_chat`` yields assistant content
deltas (strings). The bearer key is sent ONLY in the Authorization header —
never logged, never placed in an exception message.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Iterator, Optional, Sequence

import requests


class ProviderError(RuntimeError):
    """Any transport/HTTP/parse failure talking to the LLM endpoint.

    The message is safe to surface to the operator (the key only appears in the
    Authorization header, which is not echoed into the exception text).
    ``status_code`` is set when the provider answered.
    """

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def provider_name(base_url: str) -> str:
    """Best-effort human provider label from the base URL host (descriptive only)."""
    low = (base_url or "").lower()
    if "cerebras" in low:
        return "cerebras"
    if "groq.com" in low:
        return "groq"
    if "openrouter" in low:
        return "openrouter"
    if "openai.com" in low:
        return "openai"
    if "localhost" in low or "127.0.0.1" in low or "11434" in low:
        return "local"
    return "custom"


def stream_chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    timeout: float = 60.0,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
    max_retries: int = 2,
) -> Iterator[str]:
    """Stream a chat completion, yielding assistant content deltas (strings).

    On a 429 (free-tier tokens-per-minute limit) the provider tells us how long
    to wait; we honour that and retry up to ``max_retries`` times. Because a 429
    is returned BEFORE any token is streamed, retrying never double-emits.

    Raises ``ProviderError`` on any non-retryable HTTP/transport failure.
    """
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens

    for attempt in range(max_retries + 1):
        try:
            with requests.post(
                url, headers=headers, json=payload, stream=True, timeout=timeout
            ) as resp:
                if resp.status_code == 429 and attempt < max_retries:
                    time.sleep(min(_retry_after_seconds(resp.headers, resp.content), 9.0))
                    continue  # re-issue the same request after the backoff
                if resp.status_code >= 400:
                    detail = _safe_error_detail(resp.content)
                    raise ProviderError(
                        f"LLM endpoint returned HTTP {resp.status_code}: {detail}",
                        status_code=resp.status_code,
                    )
                for line in resp.iter_lines(decode_unicode=True):
                    delta = _content_from_sse_line(line)
                    if delta:
                        yield delta
                return
        except ProviderError:
            raise
        except requests.RequestException as exc:
            raise ProviderError(f"Could not reach the LLM endpoint: {exc}") from exc


def stream_chat_failover(
    *,
    providers: Sequence[dict[str, str]],
    messages: list[dict[str, Any]],
    timeout: float = 60.0,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
) -> Iterator[str]:
    """Stream from the first provider that works, failing over to the next when
    one errors BEFORE emitting any token. Each provider dict is
    ``{base_url, api_key, model}``. Fail-over is only safe pre-stream: once a
    provider has yielded content, a mid-stream failure re-raises."""
    last_err: Optional[ProviderError] = None
    for prof in providers:
        started = False
        try:
            for delta in stream_chat(
                base_url=prof["base_url"],
                api_key=prof["api_key"],
                model=prof["model"],
                messages=messages,
                timeout=timeout,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                started = True
                yield delta
            return
        except ProviderError as exc:
            if started:
                raise
            last_err = exc
            continue
    if last_err is not None:
        raise last_err
    raise ProviderError("No LLM provider is configured.")


def _content_from_sse_line(line: Optional[str]) -> str:
    """Extract the assistant content delta from one SSE ``data:`` line, or ''."""
    if not line or not line.startswith("data:"):
        return ""
    data = line[len("data:"):].strip()
    if not data or data == "[DONE]":
        return ""
    try:
        obj = json.loads(data)
    except (ValueError, TypeError):
        return ""
    choices = obj.get("choices")
    if not (isinstance(choices, list) and choices):
        return ""
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    return content if isinstance(content, str) else ""


def _retry_after_seconds(headers: Any, body: bytes) -> float:
    """How long to wait before retrying a 429 — prefers ``Retry-After``, else the
    provider's "try again in 5.06s" hint, else a small default. Bounded by caller."""
    try:
        ra = headers.get("retry-after")
    except Exception:
        ra = None
    if ra:
        try:
            return float(ra) + 0.2
        except (ValueError, TypeError):
            pass
    try:
        text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body)
        m = re.search(r"try again in ([0-9.]+)\s*s", text)
        if m:
            return float(m.group(1)) + 0.3
    except Exception:
        pass
    return 3.0


def _safe_error_detail(body: Any) -> str:
    """A short, safe error message from an error body — never echoes request
    material, bounded length."""
    try:
        text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body or "")
    except Exception:
        text = ""
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            err = obj.get("error")
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"])[:300]
            if isinstance(err, str):
                return err[:300]
    except (ValueError, TypeError):
        pass
    return text[:200] if text else "no detail"
