"""
Live terminal viewer for AI agents.

Launches a visible native PowerShell terminal window on Windows so the user
can observe the AI agent's execution live on desktop, and keeps it visible
so the user can review results.
"""
from __future__ import annotations

import logging
import os
import platform
import subprocess
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)


def generate_powershell_viewer_script(
    log_file: Path,
    done_file: Path,
    title: str,
    auto_close_seconds: int = 0,
) -> str:
    escaped_title = title.replace("'", "''")
    escaped_log = str(log_file.resolve()).replace("'", "''")
    escaped_done = str(done_file.resolve()).replace("'", "''")

    return f"""\
$Host.UI.RawUI.WindowTitle = '{escaped_title}'
Clear-Host
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  {escaped_title}" -ForegroundColor Green
Write-Host "  Live Output Stream" -ForegroundColor Yellow
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

$logPath = '{escaped_log}'
$doneFile = '{escaped_done}'
$autoClose = {auto_close_seconds}

if (-not (Test-Path $logPath)) {{
    New-Item -ItemType File -Path $logPath -Force | Out-Null
}}

$fileStream = [System.IO.File]::Open($logPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
$reader = New-Object System.IO.StreamReader($fileStream, [System.Text.Encoding]::UTF8)

try {{
    while ($true) {{
        while (($line = $reader.ReadLine()) -ne $null) {{
            Write-Host $line
        }}
        if (Test-Path $doneFile) {{
            while (($line = $reader.ReadLine()) -ne $null) {{
                Write-Host $line
            }}
            break
        }}
        Start-Sleep -Milliseconds 250
    }}
}}
finally {{
    $reader.Close()
    $fileStream.Close()
}}

$statusText = "COMPLETED"
if (Test-Path $doneFile) {{
    $content = Get-Content $doneFile -Raw -ErrorAction SilentlyContinue
    if ($content) {{ $statusText = $content.Trim() }}
}}

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
if ($statusText -like "*SUCCESS*" -or $statusText -like "*COMPLETED*") {{
    Write-Host "  [OK] AI Agent Finished: $statusText" -ForegroundColor Green
}} else {{
    Write-Host "  [!] AI Agent Stopped: $statusText" -ForegroundColor Red
}}

if ($autoClose -gt 0) {{
    Write-Host "  This window will close automatically in $autoClose seconds..." -ForegroundColor Yellow
    Write-Host "  (Press Ctrl+C to keep this window open)" -ForegroundColor DarkGray
    Write-Host "======================================================================" -ForegroundColor Cyan
    for ($i = $autoClose; $i -gt 0; $i--) {{
        Write-Host -NoNewline "`r  Closing in $i seconds... "
        try {{
            if ([Console]::KeyAvailable) {{
                $null = [Console]::ReadKey($true)
                Write-Host "`r  Key pressed. Closing now.                          "
                exit
            }}
        }} catch {{}}
        Start-Sleep -Seconds 1
    }}
    exit
}} else {{
    Write-Host "  Terminal will remain open so you can inspect all output." -ForegroundColor Yellow
    Write-Host "  You can run commands here or close this window when done." -ForegroundColor DarkGray
    Write-Host "======================================================================" -ForegroundColor Cyan
}}
"""


class TerminalViewer:
    """
    Manages a desktop terminal window displaying real-time agent output.
    """

    def __init__(
        self,
        workspace_path: Path,
        title: str = "AI Agent Live Console",
        auto_close_seconds: int = 0,
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
        self._proc: subprocess.Popen[bytes] | None = None
        self._active = False

    async def start(self) -> None:
        """Initialize the viewer and launch the visible terminal window if eligible."""
        if not self.enabled:
            return

        # Do not pop up windows during automated pytest suites
        if "PYTEST_CURRENT_TEST" in os.environ or os.environ.get("HEADLESS_TERMINAL_TEST") == "1":
            return

        if platform.system() != "Windows":
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

            # Prepare initial empty log and write generated PowerShell script
            self.log_file.write_text("", encoding="utf-8")
            script_code = generate_powershell_viewer_script(
                log_file=self.log_file,
                done_file=self.done_file,
                title=self.title,
                auto_close_seconds=self.auto_close_seconds,
            )
            self.script_file.write_text(script_code, encoding="utf-8")
            self._file_handle = open(self.log_file, "a", encoding="utf-8", errors="replace")
            self._active = True

            # Launch visible native console window with -NoExit so it stays open
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            self._proc = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoExit",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(self.script_file.resolve()),
                ],
                creationflags=flags,
            )
            logger.info("Launched live terminal viewer for %s (PID: %s)", self.title, self._proc.pid)
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
        """Signal completion to the viewer."""
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
