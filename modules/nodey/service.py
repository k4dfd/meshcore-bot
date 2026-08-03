"""Nodey service facade — reads [Nodey] config, builds the prompt, streams a reply.

Keeps the Flask endpoint thin: the endpoint handles auth/SSE plumbing, this module
owns the actual assistant logic (config, history sanitation, provider call, safe
error text). Knowledge-only v1: no tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from . import persona, provider, scrub

# Bound the conversation we send upstream (cost + latency + token safety).
MAX_HISTORY_MESSAGES = 20
MAX_MESSAGE_CHARS = 8000

_NOT_CONFIGURED_MSG = (
    "Nodey isn't set up yet. An admin needs to fill in the [Nodey] section of "
    "config.ini — an OpenAI-compatible LLM endpoint: base_url, model, and api_key "
    "(e.g. Cerebras, the mcdash LLM proxy, or a local model). Once that's set, I'm "
    "ready to help."
)


@dataclass(frozen=True)
class NodeyConfig:
    enabled: bool
    base_url: str
    model: str
    api_key: str
    fallback_base_url: str
    fallback_model: str
    fallback_api_key: str
    timeout_seconds: float
    max_tokens: int
    temperature: float


def _get(config: Any, key: str, fallback: str = "") -> str:
    try:
        return (config.get("Nodey", key, fallback=fallback) or "").strip()
    except Exception:
        return fallback


def load_config(config: Any) -> NodeyConfig:
    """Read the [Nodey] section from a ConfigParser into a NodeyConfig (safe defaults)."""
    def _float(key: str, default: float) -> float:
        try:
            return float(_get(config, key) or default)
        except (ValueError, TypeError):
            return default

    def _int(key: str, default: int) -> int:
        try:
            return int(float(_get(config, key) or default))
        except (ValueError, TypeError):
            return default

    enabled = _get(config, "enabled", "false").lower() in ("1", "true", "yes", "on")
    return NodeyConfig(
        enabled=enabled,
        base_url=_get(config, "base_url"),
        model=_get(config, "model", "gpt-oss-120b"),
        api_key=_get(config, "api_key"),
        fallback_base_url=_get(config, "fallback_base_url"),
        fallback_model=_get(config, "fallback_model"),
        fallback_api_key=_get(config, "fallback_api_key"),
        timeout_seconds=_float("timeout_seconds", 60.0),
        max_tokens=_int("max_tokens", 1024),
        temperature=_float("temperature", 0.2),
    )


def is_configured(cfg: NodeyConfig) -> bool:
    """True when Nodey can actually reach a brain (enabled + a well-formed primary
    endpoint set). The base_url must be an http(s) URL — rejects file:/gopher:/etc.
    schemes so a mistyped or malicious config value can't point the client at a
    non-HTTP target."""
    return bool(
        cfg.enabled
        and cfg.api_key
        and cfg.model
        and cfg.base_url
        and cfg.base_url.lower().startswith(("http://", "https://"))
    )


def build_messages(operator_name: str, history: list[dict[str, Any]]) -> list[dict[str, str]]:
    """System prompt + a sanitized, bounded slice of the conversation history."""
    messages: list[dict[str, str]] = [
        {"role": "system", "content": persona.build_system_prompt(operator_name)}
    ]
    for m in (history or [])[-MAX_HISTORY_MESSAGES:]:
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content[:MAX_MESSAGE_CHARS]})
    return messages


def _providers(cfg: NodeyConfig) -> list[dict[str, str]]:
    out = [{"base_url": cfg.base_url, "api_key": cfg.api_key, "model": cfg.model}]
    if cfg.fallback_base_url and cfg.fallback_api_key:
        out.append({
            "base_url": cfg.fallback_base_url,
            "api_key": cfg.fallback_api_key,
            "model": cfg.fallback_model or cfg.model,
        })
    return out


def stream_reply(
    cfg: NodeyConfig, operator_name: str, history: list[dict[str, Any]]
) -> Iterator[str]:
    """Yield the assistant's reply as text deltas. Never raises — a not-configured
    state or a provider failure is surfaced as a short, safe message instead."""
    if not is_configured(cfg):
        yield _NOT_CONFIGURED_MSG
        return

    messages = build_messages(operator_name, history)
    providers = _providers(cfg)
    try:
        if len(providers) > 1:
            yield from provider.stream_chat_failover(
                providers=providers,
                messages=messages,
                timeout=cfg.timeout_seconds,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
            )
        else:
            yield from provider.stream_chat(
                base_url=cfg.base_url,
                api_key=cfg.api_key,
                model=cfg.model,
                messages=messages,
                timeout=cfg.timeout_seconds,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
            )
    except provider.ProviderError as exc:
        # The key is never in the exception text; scrub_text is belt-and-suspenders.
        yield f"\n\n_(Nodey couldn't reach the LLM: {scrub.scrub_text(str(exc))})_"
