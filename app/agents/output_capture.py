"""Drain agent pipes while retaining only a bounded tail of their output."""
from __future__ import annotations

import asyncio

OUTPUT_TAIL_BYTES = 128 * 1024
READ_CHUNK_BYTES = 16 * 1024
_QUOTA_SIGNALS = (
    b"quota exceeded", b"resource exhausted", b"resource_exhausted", b"rate limit",
    b"ratelimit", b"token limit", b"out of tokens", b"429", b"insufficient quota",
    b"capacity exhausted", b"exceeded your current quota", b"too many requests",
    b"credit balance is too low",
)
_SIGNAL_OVERLAP = max(map(len, _QUOTA_SIGNALS)) - 1


class OutputCapture:
    def __init__(self) -> None:
        self._tail = bytearray()
        self._signal_tail = b""
        self.token_exhausted = False

    def append(self, chunk: bytes) -> None:
        signal_text = self._signal_tail + chunk.lower()
        if not self.token_exhausted:
            self.token_exhausted = any(signal in signal_text for signal in _QUOTA_SIGNALS)
        self._signal_tail = signal_text[-_SIGNAL_OVERLAP:]
        self._tail.extend(chunk)
        if len(self._tail) > OUTPUT_TAIL_BYTES:
            del self._tail[:-OUTPUT_TAIL_BYTES]

    def last_lines(self, count: int) -> str:
        return "\n".join(self._tail.decode("utf-8", errors="replace").splitlines()[-count:])

    async def drain(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while chunk := await stream.read(READ_CHUNK_BYTES):
            self.append(chunk)
