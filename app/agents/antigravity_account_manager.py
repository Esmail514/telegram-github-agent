"""
Antigravity Account Profile Manager.

Manages switching between multiple Google Antigravity accounts by reading and
writing the OAuth session credential in Windows Credential Manager
(Target: 'gemini:antigravity'). Profiles are encrypted at rest using Windows DPAPI.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
import sys
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

CRED_TARGET_NAME = "gemini:antigravity"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.c_char_p),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


@dataclass
class ProfileMeta:
    name: str
    username: str
    comment: str
    blob_hash: str
    created_at: str
    updated_at: str


class AntigravityAccountManager:
    """
    Handles saving, listing, and switching Google Antigravity account profiles.
    """

    def __init__(self, profiles_dir: Path | None = None) -> None:
        if profiles_dir is None:
            base_dir = (
                Path(os.environ.get("LOCALAPPDATA", ""))
                / "Antigravity"
                / "profiles"
                if os.name == "nt"
                else Path.home() / ".gemini" / "antigravity_profiles"
            )
            self._profiles_dir = base_dir
        else:
            self._profiles_dir = profiles_dir

        self._profiles_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # DPAPI Cryptographic Protection
    # ------------------------------------------------------------------

    def _encrypt(self, data: bytes) -> bytes:
        """Encrypts data with Windows DPAPI (CryptProtectData)."""
        if os.name != "nt":
            return data
        try:
            crypt32 = ctypes.windll.crypt32
            in_blob = DATA_BLOB(
                len(data), ctypes.cast(ctypes.c_char_p(data), ctypes.POINTER(ctypes.c_byte))
            )
            out_blob = DATA_BLOB()
            if not crypt32.CryptProtectData(
                ctypes.byref(in_blob), "agy_profile", None, None, None, 0, ctypes.byref(out_blob)
            ):
                raise RuntimeError("CryptProtectData failed")
            encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)
            return encrypted
        except Exception as exc:
            logger.warning("DPAPI encryption fallback: %s", exc)
            return data

    def _decrypt(self, data: bytes) -> bytes:
        """Decrypts data with Windows DPAPI (CryptUnprotectData)."""
        if os.name != "nt":
            return data
        try:
            crypt32 = ctypes.windll.crypt32
            in_blob = DATA_BLOB(
                len(data), ctypes.cast(ctypes.c_char_p(data), ctypes.POINTER(ctypes.c_byte))
            )
            out_blob = DATA_BLOB()
            if not crypt32.CryptUnprotectData(
                ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
            ):
                raise RuntimeError("CryptUnprotectData failed")
            decrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)
            return decrypted
        except Exception as exc:
            logger.warning("DPAPI decryption fallback: %s", exc)
            return data

    # ------------------------------------------------------------------
    # System Credential Manager Operations
    # ------------------------------------------------------------------

    def _read_system_credential(self) -> tuple[bytes, str, str, int] | None:
        """
        Reads the active 'gemini:antigravity' credential from Windows Credential Manager.
        Returns: (credential_blob, user_name, comment, persist) or None.
        """
        if os.name != "nt":
            return None
        advapi32 = ctypes.windll.advapi32
        cred_ptr = ctypes.POINTER(CREDENTIAL)()
        res = advapi32.CredReadW(
            CRED_TARGET_NAME, CRED_TYPE_GENERIC, 0, ctypes.byref(cred_ptr)
        )
        if not res:
            return None
        try:
            c = cred_ptr.contents
            blob = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
            user_name = c.UserName or "antigravity"
            comment = c.Comment or ""
            persist = c.Persist or CRED_PERSIST_LOCAL_MACHINE
            return blob, user_name, comment, persist
        finally:
            advapi32.CredFree(cred_ptr)

    def _write_system_credential(
        self, blob: bytes, user_name: str = "antigravity", comment: str = "", persist: int = CRED_PERSIST_LOCAL_MACHINE
    ) -> bool:
        """
        Writes the given credential blob into Windows Credential Manager.
        """
        if os.name != "nt":
            return False
        advapi32 = ctypes.windll.advapi32
        new_cred = CREDENTIAL()
        new_cred.Type = CRED_TYPE_GENERIC
        new_cred.TargetName = CRED_TARGET_NAME
        new_cred.CredentialBlobSize = len(blob)
        new_cred.CredentialBlob = blob
        new_cred.Persist = persist
        new_cred.UserName = user_name
        new_cred.Comment = comment

        res = advapi32.CredWriteW(ctypes.byref(new_cred), 0)
        return bool(res)

    # ------------------------------------------------------------------
    # Profile Management API
    # ------------------------------------------------------------------

    def save_current_profile(self, name: str, display_name: str | None = None) -> bool:
        """
        Saves the currently active session credential as a named profile.
        """
        cred_info = self._read_system_credential()
        if cred_info is None:
            logger.error("No active Antigravity session found in Windows Credential Manager.")
            return False

        blob, username, comment, _ = cred_info
        blob_hash = hashlib.sha256(blob).hexdigest()
        now_iso = datetime.now(UTC).isoformat()

        meta = ProfileMeta(
            name=name,
            username=display_name or username,
            comment=comment,
            blob_hash=blob_hash,
            created_at=now_iso,
            updated_at=now_iso,
        )

        profile_file = self._profiles_dir / f"{name}.dat"
        meta_file = self._profiles_dir / f"{name}.json"

        encrypted_blob = self._encrypt(blob)
        profile_file.write_bytes(encrypted_blob)
        meta_file.write_text(json.dumps(asdict(meta), indent=2), encoding="utf-8")

        logger.info("Saved Antigravity profile '%s' (user: %s)", name, meta.username)
        return True

    def activate_profile(self, name: str) -> bool:
        """
        Activates a saved profile by injecting its credentials into the system.
        """
        profile_file = self._profiles_dir / f"{name}.dat"
        meta_file = self._profiles_dir / f"{name}.json"

        if not profile_file.exists() or not meta_file.exists():
            logger.error("Profile '%s' does not exist.", name)
            return False

        try:
            meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
            encrypted_blob = profile_file.read_bytes()
            blob = self._decrypt(encrypted_blob)

            success = self._write_system_credential(
                blob=blob,
                user_name=meta_data.get("username", "antigravity"),
                comment=meta_data.get("comment", ""),
            )
            if success:
                logger.info("Successfully activated Antigravity profile '%s'", name)
            return success
        except Exception as exc:
            logger.exception("Failed to activate profile '%s': %s", name, exc)
            return False

    def list_profiles(self) -> list[dict]:
        """
        Returns a list of all saved profiles and indicates which one is currently active.
        """
        profiles: list[dict] = []
        active_hash = None

        cred_info = self._read_system_credential()
        if cred_info:
            active_hash = hashlib.sha256(cred_info[0]).hexdigest()

        for meta_file in sorted(self._profiles_dir.glob("*.json")):
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                name = data.get("name", meta_file.stem)
                is_active = (data.get("blob_hash") == active_hash) if active_hash else False
                profiles.append({
                    "name": name,
                    "username": data.get("username", name),
                    "created_at": data.get("created_at"),
                    "is_active": is_active,
                })
            except Exception as exc:
                logger.warning("Could not read profile meta %s: %s", meta_file, exc)

        return profiles

    def get_active_profile(self) -> str | None:
        """
        Returns the name of the currently active profile, or None if unrecognized.
        """
        cred_info = self._read_system_credential()
        if not cred_info:
            return None

        active_hash = hashlib.sha256(cred_info[0]).hexdigest()
        for meta_file in self._profiles_dir.glob("*.json"):
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                if data.get("blob_hash") == active_hash:
                    return data.get("name")
            except Exception:
                continue
        return None

    def delete_profile(self, name: str) -> bool:
        """Deletes a saved profile."""
        profile_file = self._profiles_dir / f"{name}.dat"
        meta_file = self._profiles_dir / f"{name}.json"
        deleted = False
        if profile_file.exists():
            profile_file.unlink()
            deleted = True
        if meta_file.exists():
            meta_file.unlink()
            deleted = True
        return deleted


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Antigravity CLI account profiles.")
    parser.add_argument("--save", metavar="NAME", help="Save current active session under NAME")
    parser.add_argument("--user", metavar="DISPLAY", help="Optional display email/username for the profile")
    parser.add_argument("--activate", metavar="NAME", help="Activate saved profile NAME")
    parser.add_argument("--list", action="store_true", help="List all saved profiles")
    parser.add_argument("--current", action="store_true", help="Show currently active profile name")
    parser.add_argument("--delete", metavar="NAME", help="Delete profile NAME")

    args = parser.parse_args()
    mgr = AntigravityAccountManager()

    if args.save:
        ok = mgr.save_current_profile(args.save, display_name=args.user)
        if ok:
            print(f"Profile '{args.save}' saved successfully.")
        else:
            print("Failed to save profile. Is Antigravity logged in?")
            sys.exit(1)
    elif args.activate:
        ok = mgr.activate_profile(args.activate)
        if ok:
            print(f"Profile '{args.activate}' activated.")
        else:
            print(f"Failed to activate profile '{args.activate}'.")
            sys.exit(1)
    elif args.list:
        profiles = mgr.list_profiles()
        if not profiles:
            print("No saved Antigravity profiles found.")
        else:
            print("Available Antigravity Profiles:")
            for p in profiles:
                status = " [ACTIVE]" if p["is_active"] else ""
                print(f" - {p['name']} ({p['username']}){status}")
    elif args.current:
        active = mgr.get_active_profile()
        print(f"Current active profile: {active or 'Unknown (unsaved session)'}")
    elif args.delete:
        ok = mgr.delete_profile(args.delete)
        if ok:
            print(f"Profile '{args.delete}' deleted.")
        else:
            print(f"Profile '{args.delete}' not found.")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
