"""Small, dependency-free directory browsing primitives for Telegram flows."""
from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class DirectoryError:
    NOT_FOUND = "not_found"
    NOT_DIRECTORY = "not_directory"
    PERMISSION_DENIED = "permission_denied"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class DirectoryListing:
    path: Path
    directories: list[Path]
    error: str | None = None


DriveProvider = Callable[[], list[Path]]


class DirectoryBrowser:
    """Lists real drives and immediate child directories without shell commands."""

    def __init__(self, drive_provider: DriveProvider | None = None) -> None:
        self._drive_provider = drive_provider or _native_drives

    def list_drives(self) -> list[Path]:
        drives: list[Path] = []
        for drive in self._drive_provider():
            resolved, error = self.validate_directory(drive)
            if resolved is not None and error is None:
                drives.append(resolved)
        return sorted(set(drives), key=lambda path: str(path).casefold())

    def list_directories(self, path: Path) -> DirectoryListing:
        resolved, error = self.validate_directory(path)
        if resolved is None:
            return DirectoryListing(path=path, directories=[], error=error)
        try:
            directories = [
                entry.resolve()
                for entry in resolved.iterdir()
                if entry.is_dir()
            ]
        except PermissionError:
            return DirectoryListing(path=resolved, directories=[], error=DirectoryError.PERMISSION_DENIED)
        except FileNotFoundError:
            return DirectoryListing(path=resolved, directories=[], error=DirectoryError.NOT_FOUND)
        except OSError:
            return DirectoryListing(path=resolved, directories=[], error=DirectoryError.UNAVAILABLE)
        return DirectoryListing(
            path=resolved,
            directories=sorted(directories, key=lambda item: item.name.casefold()),
        )

    @staticmethod
    def validate_directory(path: Path) -> tuple[Path | None, str | None]:
        try:
            resolved = path.resolve(strict=True)
        except PermissionError:
            return None, DirectoryError.PERMISSION_DENIED
        except FileNotFoundError:
            return None, DirectoryError.NOT_FOUND
        except OSError:
            return None, DirectoryError.UNAVAILABLE
        try:
            if not resolved.is_dir():
                return None, DirectoryError.NOT_DIRECTORY
            next(resolved.iterdir(), None)
        except PermissionError:
            return None, DirectoryError.PERMISSION_DENIED
        except FileNotFoundError:
            return None, DirectoryError.NOT_FOUND
        except OSError:
            return None, DirectoryError.UNAVAILABLE
        return resolved, None


def _native_drives() -> list[Path]:
    """Discover mounted roots; on Windows use the OS drive bitmask."""
    if os.name != "nt":
        return [Path("/")]
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    except (AttributeError, OSError):
        return []
    return [Path(f"{letter}:\\") for index, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ") if bitmask & (1 << index)]


directory_browser = DirectoryBrowser()
