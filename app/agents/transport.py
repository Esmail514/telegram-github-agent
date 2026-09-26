"""
Agent transport abstraction — Cli / Desktop / Api.

A transport is *how* the controller talks to an agent. The goal of this layer
is to make the fallback architecture explicit and testable without inventing
interfaces:

- `CliTransport`     — real subprocess execution of a native CLI (opencode,
                       codex, agy). Fully supported.
- `DesktopTransport` — deliberately probes UNAVAILABLE unless a concrete,
                       actually-documented local IPC spec is implemented.
                       This controller will NOT invent ports/IPC protocols,
                       so today it is honest about not supporting one.
- `ApiTransport`     — reserved for documented HTTP APIs; UNAVAILABLE unless a
                       subclass provides a real endpoint.

Every transport exposes `probe()` → `TransportAvailability`; the fallback
router drives decisions off these probes plus the failure classifier.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Transport kinds
CLI = "cli"
DESKTOP = "desktop"
API = "api"

# Availability
AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"
NOT_SUPPORTED = "NOT_SUPPORTED"


class TransportError(Exception):
    """Base transport error."""


class TransportUnavailableError(TransportError):
    """Transport cannot run the task right now (missing binary, no IPC...)."""


class TransportStartupError(TransportError):
    """Transport binary/API started but failed during startup."""


class TransportTimedOutError(TransportError):
    """Transport exceeded its runtime budget."""


@dataclass
class TransportAvailability:
    status: str            # AVAILABLE / UNAVAILABLE / NOT_SUPPORTED
    transport_type: str    # "cli" | "desktop" | "api"
    detail: str = ""

    @property
    def available(self) -> bool:
        return self.status == AVAILABLE


@dataclass
class AgentCapabilities:
    agent_name: str
    transport_type: str | None = None
    availability: str | None = None
    can_run_task: bool = False
    can_stream_progress: bool = False
    can_cancel: bool = False
    can_resume: bool = False
    can_report_usage: bool = False
    supports_workspace: bool = False

    def describe(self) -> str:
        return (
            f"{self.agent_name}: {self.availability or 'unknown'}"
            f" (transport={self.transport_type or 'none'}, "
            f"streams={self.can_stream_progress}, cancels={self.can_cancel}, "
            f"resumes={self.can_resume}, workspace={self.supports_workspace})"
        )


@dataclass
class TransportRunResult:
    returncode: int
    output: str = ""
    error: str = ""
    timed_out: bool = False


OnOutput = Callable[[str], Awaitable[None]]


class AgentTransport(ABC):
    """Abstract transport for executing an agent (or a probe target)."""

    transport_type: str = "base"
    agent_name: str = "unknown"
    family: str = "agent"

    @abstractmethod
    async def probe(self) -> TransportAvailability:
        """Report whether this transport can run the agent right now."""

    @abstractmethod
    async def run(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
        timeout: float = 60.0,
        on_output: OnOutput | None = None,
    ) -> TransportRunResult:
        """Run argv in cwd with a hard timeout. Never uses a shell."""

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully stop the current run (terminate → kill)."""


class CliTransport(AgentTransport):
    """Runs an agent's native CLI binary as a subprocess."""

    transport_type = CLI

    def __init__(self, command: str, agent_name: str | None = None) -> None:
        self._command = command
        self._executable: str | None = None
        self._active_proc: asyncio.subprocess.Process | None = None
        self._agent_name = agent_name or command

    @property
    def command(self) -> str:
        return self._command

    async def probe(self) -> TransportAvailability:
        self._executable = shutil.which(self._command) or (
            self._command if _looks_like_abs_or_relative(self._command) else None
        )
        if self._executable and Path(self._executable).exists():
            return TransportAvailability(AVAILABLE, CLI, f"found: {self._executable}")
        return TransportAvailability(
            UNAVAILABLE,
            CLI,
            f"executable '{self._command}' not found on PATH",
        )

    async def run(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
        timeout: float = 60.0,
        on_output: OnOutput | None = None,
    ) -> TransportRunResult:
        availability = await self.probe()
        if not availability.available:
            raise TransportUnavailableError(availability.detail)
        assert self._executable
        full_argv = [self._executable, *argv]
        proc = await asyncio.create_subprocess_exec(
            *full_argv,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env or None,
        )
        self._active_proc = proc
        out_lines: list[str] = []
        err_lines: list[str] = []

        async def _read(stream: asyncio.StreamReader | None, sink: list[str]) -> None:
            if stream is None:
                return
            total = 0
            while not stream.at_eof():
                line = await stream.readline()
                if not line:
                    break
                decoded = line.decode(errors="replace").rstrip("\r\n")
                if decoded:
                    sink.append(decoded)
                    total += len(decoded)
                    if total > 32 * 1024:
                        sink.append("...[output truncated]")
                        break
                    if on_output is not None:
                        await on_output(decoded)

        out_task = asyncio.create_task(_read(proc.stdout, out_lines))
        err_task = asyncio.create_task(_read(proc.stderr, err_lines))
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except TimeoutError:
            await self.stop()
            await asyncio.gather(out_task, err_task, return_exceptions=True)
            self._active_proc = None
            return TransportRunResult(
                returncode=-1,
                output="\n".join(out_lines),
                error="\n".join(err_lines),
                timed_out=True,
            )
        await asyncio.gather(out_task, err_task, return_exceptions=True)
        self._active_proc = None
        return TransportRunResult(
            returncode=proc.returncode or 0,
            output="\n".join(out_lines),
            error="\n".join(err_lines),
        )

    async def stop(self) -> None:
        proc = self._active_proc
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass
        self._active_proc = None


class DesktopTransport(AgentTransport):
    """Deliberately honest placeholder.

    The controller has no verified, documented local IPC specification for any
    desktop agent. Rather than inventing ports / message protocols (which the
    trust boundary forbids), this transport always probes UNAVAILABLE.
    """

    transport_type = DESKTOP

    def __init__(self, agent_name: str) -> None:
        self._name = agent_name

    async def probe(self) -> TransportAvailability:
        return TransportAvailability(
            UNAVAILABLE,
            DESKTOP,
            "no supported local IPC mechanism is implemented for "
            f"desktop agent '{self._name}'; refusing to invent an API",
        )

    async def run(self, *_: Any, **__: Any) -> TransportRunResult:  # type: ignore[override]
        raise TransportUnavailableError(
            "Desktop transport is not supported; it probes UNAVAILABLE."
        )

    async def stop(self) -> None:
        return None


class ApiTransport(AgentTransport):
    """Reserved for documented HTTP APIs. UNAVAILABLE unless subclassed with a real base URL."""

    transport_type = API

    def __init__(self, agent_name: str, base_url: str | None = None) -> None:
        self._name = agent_name
        self._base_url = base_url

    async def probe(self) -> TransportAvailability:
        if self._base_url:
            return TransportAvailability(AVAILABLE, API, f"base_url: {self._base_url}")
        return TransportAvailability(
            UNAVAILABLE,
            API,
            f"no configured API endpoint for agent '{self._name}'",
        )

    async def run(self, *_: Any, **__: Any) -> TransportRunResult:  # type: ignore[override]
        raise TransportUnavailableError("ApiTransport is not wired to a real endpoint.")

    async def stop(self) -> None:
        return None


def _looks_like_abs_or_relative(command: str) -> bool:
    return "/" in command or "\\" in command or "." in command


# ---------------------------------------------------------------------------
# Per-agent transport resolution
# ---------------------------------------------------------------------------

def agent_cli_command(agent_name: str) -> str | None:
    """Return the CLI command configured for the given agent (or None)."""
    from app.config.settings import settings

    return {
        "antigravity": settings.ANTIGRAVITY_COMMAND,
        "codex": settings.CODEX_COMMAND,
        "opencode": settings.OPENCODE_COMMAND,
        "claude": "claude",
        "gemini": "gemini",
    }.get(agent_name.lower())


def transports_for_agent(agent_name: str) -> list[AgentTransport]:
    """Ordered candidates: native CLI first, then desktop (probe-only)."""
    name = agent_name.lower()
    command = agent_cli_command(name)
    transports: list[AgentTransport] = []
    if command:
        transports.append(CliTransport(command, agent_name=name))
    transports.append(DesktopTransport(name))
    return transports


async def probe_agent(agent_name: str, timeout: float = 10.0) -> AgentCapabilities:
    """Build an `AgentCapabilities` snapshot by probing available transports."""
    capabilities = AgentCapabilities(agent_name=agent_name)
    for transport in transports_for_agent(agent_name):
        try:
            availability = await asyncio.wait_for(transport.probe(), timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            availability = TransportAvailability(
                UNAVAILABLE, transport.transport_type, f"probe error: {exc}"
            )
        if availability.available:
            capabilities.transport_type = transport.transport_type
            capabilities.availability = availability.status
            capabilities.can_run_task = True
            capabilities.can_cancel = True
            capabilities.can_resume = False
            capabilities.can_report_usage = False
            capabilities.can_stream_progress = transport.transport_type == CLI
            capabilities.supports_workspace = transport.transport_type == CLI
            return capabilities
        logger.debug(
            "Agent %s: %s transport unavailable: %s",
            agent_name, transport.transport_type, availability.detail,
        )
    capabilities.availability = UNAVAILABLE
    return capabilities
