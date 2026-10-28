"""Bounded, expiring references for callback payloads exceeding Telegram's limit."""
from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from collections.abc import Callable

from telegram import CallbackQuery


class CallbackPayloadExpired(ValueError):
    pass


class CallbackPayloadRegistry:
    def __init__(
        self, max_entries: int = 512, ttl: float = 1800,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_entries < 1 or ttl <= 0:
            raise ValueError("Callback storage requires a positive size and lifetime")
        self._max_entries = max_entries
        self._ttl = ttl
        self._clock = clock or time.monotonic
        self._entries: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def _prune(self) -> None:
        now = self._clock()
        for key, (expires, _) in list(self._entries.items()):
            if expires <= now:
                self._entries.pop(key, None)

    def encode(self, prefix: str, payload: str) -> str:
        plain = prefix + payload
        if len(plain.encode("utf-8")) <= 64 and not payload.startswith("~"):
            return plain
        if len(prefix.encode("utf-8")) + 17 > 64:
            raise ValueError("Callback action prefix exceeds Telegram's size limit")
        self._prune()
        while len(self._entries) >= self._max_entries:
            self._entries.popitem(last=False)
        encoded = prefix + "~" + secrets.token_urlsafe(12)
        while encoded in self._entries:
            encoded = prefix + "~" + secrets.token_urlsafe(12)
        self._entries[encoded] = (self._clock() + self._ttl, payload)
        return encoded

    def decode(self, data: str, prefix: str) -> str:
        if not data.startswith(prefix):
            raise CallbackPayloadExpired("Unexpected callback action")
        value = data.removeprefix(prefix)
        if not value.startswith("~"):
            return value
        self._prune()
        entry = self._entries.get(data)
        if entry is None:
            raise CallbackPayloadExpired("Callback selection has expired")
        self._entries.move_to_end(data)
        return entry[1]

    def __len__(self) -> int:
        self._prune()
        return len(self._entries)


_registry = CallbackPayloadRegistry()


def encode_callback_payload(prefix: str, payload: str) -> str:
    return _registry.encode(prefix, payload)


def decode_callback_value(data: str, prefix: str) -> str:
    return _registry.decode(data, prefix)


async def decode_callback_payload(query: CallbackQuery, prefix: str) -> str | None:
    try:
        if not isinstance(query.data, str):
            raise CallbackPayloadExpired("Invalid callback selection")
        return decode_callback_value(query.data, prefix)
    except CallbackPayloadExpired:
        await query.answer("انتهت صلاحية هذا الاختيار. حدّث القائمة أو افتحها مجددًا.", show_alert=True)
        return None
