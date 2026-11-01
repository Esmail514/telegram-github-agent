"""
Live terminal viewer for AI agents.

Launches a visible PowerShell terminal window on Windows so the user
can observe the AI agent's execution live on desktop, complete with an
automatic countdown timer before closing.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import subprocess
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)

_POWERSHELL_VIEWER_TEMPLATE = """\
param(
    [string]$LogPath,
    [string]$DoneFile,
    [string]$Title = "AI Agent Live Terminal",
    [int]$AutoCloseSeconds = 15
)

$Host.UI.RawUI.WindowTitle = $Title
Clear-Host
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  $Title" -ForegroundColor Green
Write-Host "  Live Output Stream" -ForegroundColor Yellow
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $LogPath)) {
    New-Item -ItemType File -Path $LogPath -Force | Out-Null
}

$fileStream = [System.IO.File]::Open($LogPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
$reader = New-Object System.IO.StreamReader($fileStream, [System.Text.Encoding]::UTF8)

try {
    while ($true) {
        while (($line = $reader.ReadLine()) -ne $null) {
            Write-Host $line
        }
        if (Test-Path $DoneFile) {
            # Drain any remaining lines written at completion
            while (($line = $reader.ReadLine()) -ne $null) {
                Write-Host $line
            }
            break
        }
        Start-Sleep -Milliseconds 250
    }
}
finally {
    $reader.Close()
    $fileStream.Close()
}

$statusText = "COMPLETED"
if (Test-Path $DoneFile) {
    $content = Get-Content $DoneFile -Raw -ErrorAction SilentlyContinue
    if ($content) { $statusText = $content.Trim() }
}

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
if ($statusText -like "*SUCCESS*" -or $statusText -like "*COMPLETED*") {
    Write-Host "  [OK] AI Agent Finished: $statusText" -ForegroundColor Green
} else {
    Write-Host "  [!] AI Agent Stopped: $statusText" -ForegroundColor Red
}

if ($AutoCloseSeconds -gt 0) {
    Write-Host "  This window will close automatically in $AutoCloseSeconds seconds..." -ForegroundColor Yellow
    Write-Host "  (Press any key to close immediately, or Ctrl+C to keep open)" -ForegroundColor DarkGray
    Write-Host "======================================================================" -ForegroundColor Cyan
    for ($i = $AutoCloseSeconds; $i -gt 0; $i--) {
        Write-Host -NoNewline "`r  Closing in $i seconds... "
        if ([Console]::KeyAvailable) {
            $null = [Console]::ReadKey($true)
            Write-Host "`r  Closing now.                          "
            break
        }
        Start-Sleep -Seconds 1
    }
} else {
    Write-Host "  Press any key to close this terminal..." -ForegroundColor Yellow
    Write-Host "======================================================================" -ForegroundColor Cyan
    $null = [Console]::ReadKey($true)
}
"""


class TerminalViewer:
    """
    Manages a desktop terminal window displaying real-time agent output.
    """

    def __init__(
        self,
        workspace_path: Path,
        title: str = "AI Agent Live Console",
        auto_close_seconds: int = 15,
        enabled: bool = True,
    ) -> None:
        self.workspace_path = workspace_path
        self.title = title
        self.auto_close_seconds = max(0, auto_close_seconds)
        self.enabled = enabled
        self.log_file = workspace_path / ".agent_terminal.log"
        self.done_file = workspace_path / ".agent_terminal.done"
        self.script_file = workspace_path / ".agent_terminal_viewer.ps1"
        self._file_handle: IO[str] | None = None
        self._active = False

    async def start(self) -> None:
        """Initialize the viewer and launch the visible terminal window if eligible."""
        if not self.enabled:
            return

        # Do not pop up windows during automated pytest suites
        if "PYTEST_CURRENT_TEST" in os.environ or os.environ.get("HEADLESS_TERMINAL_TEST") == "1":
            return

        if platform.system() != "Windows":
            # Currently optimized for Windows desktop sessions
            return

        try:
            self.workspace_path.mkdir(parents=True, exist_ok=True)
            # Clean up stale files from previous runs
            for path in (self.log_file, self.done_file, self.script_file):
                if path.exists():
                    try:
                        path.unlink()
                    except OSError:
                        pass

            # Prepare initial empty log and write PowerShell script
            self.log_file.write_text("", encoding="utf-8")
            self.script_file.write_text(_POWERSHELL_VIEWER_TEMPLATE, encoding="utf-8")
            self._file_handle = open(self.log_file, "a", encoding="utf-8", errors="replace")
            self._active = True

            # Launch detached visible console window on Windows
            cmd = (
                f'cmd.exe /c start "{self.title}" powershell.exe -NoProfile -ExecutionPolicy Bypass '
                f'-File "{self.script_file}" -LogPath "{self.log_file}" -DoneFile "{self.done_file}" '
                f'-Title "{self.title}" -AutoCloseSeconds {self.auto_close_seconds}'
            )
            await asyncio.to_thread(
                subprocess.run,
                cmd,
                shell=True,
                check=False,
                capture_output=True,
            )
            logger.info("Launched live terminal viewer for %s", self.title)
        except Exception as exc:
            logger.warning("Could not launch live terminal viewer: %s", exc)
            self._active = False
            if self._file_handle:
                try:
                    self._file_handle.close()
                except OSError:
                    pass
                self._file_handle = None

    def write(self, chunk: bytes) -> None:
        """Stream new output chunk to the terminal log file."""
        if not self._active or not self._file_handle:
            return
        try:
            text = chunk.decode("utf-8", errors="replace")
            self._file_handle.write(text)
            self._file_handle.flush()
        except Exception:
            pass

    async def finish(self, success: bool = True, summary: str = "") -> None:
        """Signal completion to the viewer so it begins its auto-close countdown."""
        if not self._active:
            return
        try:
            if self._file_handle:
                self._file_handle.flush()
                self._file_handle.close()
                self._file_handle = None

            status_msg = f"SUCCESS: {summary}" if success else f"FAILED: {summary or 'Agent stopped'}"
            self.done_file.write_text(status_msg, encoding="utf-8")
        except Exception as exc:
            logger.debug("Error signaling terminal viewer finish: %s", exc)
        finally:
            self._active = False
