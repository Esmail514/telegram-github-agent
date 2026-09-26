"""
Lightweight in-memory TTL (Time-To-Live) cache.

No external dependencies — uses standard library only.

Usage:
    cache = TTLCache(ttl=60)
    cache.set("key", value)
    value = cache.get("key")   # returns None if expired
    cache.invalidate("key")
    cache.clear()
"""
from __future__ import annotations

import time
from typing import Any


class TTLCache:
    """
    Simple in-memory cache where entries expire after `ttl` seconds.

    Thread-safe for asyncio (single-threaded event loop). Not safe for
    multi-threaded use — use a Lock if needed.
    """

    def __init__(self, ttl: float = 60.0) -> None:
        self._ttl = ttl
        # {key: (value, expiry_timestamp)}
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any:
        """Return cached value, or None if missing / expired."""
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if time.monotonic() > expiry:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        """Store a value with optional per-entry TTL override."""
        effective_ttl = ttl if ttl is not None else self._ttl
        self._store[key] = (value, time.monotonic() + effective_ttl)

    def invalidate(self, key: str) -> None:
        """Remove a specific key immediately."""
        self._store.pop(key, None)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._store.clear()

    def _evict_expired(self) -> None:
        """Remove all expired entries (optional maintenance)."""
        now = time.monotonic()
        expired = [k for k, (_, exp) in self._store.items() if now > exp]
        for k in expired:
            del self._store[k]

    def __len__(self) -> int:
        self._evict_expired()
        return len(self._store)

    def __repr__(self) -> str:
        return f"TTLCache(ttl={self._ttl}, entries={len(self._store)})"
