"""
Tests for AntigravityAccountManager.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.agents.antigravity_account_manager import AntigravityAccountManager


@pytest.fixture
def temp_profiles_dir(tmp_path: Path) -> Path:
    return tmp_path / "profiles"


@pytest.fixture
def account_mgr(temp_profiles_dir: Path) -> AntigravityAccountManager:
    return AntigravityAccountManager(profiles_dir=temp_profiles_dir)


def test_list_profiles_empty(account_mgr: AntigravityAccountManager) -> None:
    profiles = account_mgr.list_profiles()
    assert profiles == []


def test_save_profile_mocked(account_mgr: AntigravityAccountManager) -> None:
    dummy_blob = b"fake_oauth_blob_data_12345"
    with patch.object(account_mgr, "_read_system_credential", return_value=(dummy_blob, "antigravity", "comment", 2)):
        saved = account_mgr.save_current_profile("account_one")
        assert saved is True

    profiles = account_mgr.list_profiles()
    assert len(profiles) == 1
    assert profiles[0]["name"] == "account_one"
    assert profiles[0]["username"] == "antigravity"


def test_activate_profile_mocked(account_mgr: AntigravityAccountManager) -> None:
    dummy_blob = b"fake_oauth_blob_data_12345"
    with patch.object(account_mgr, "_read_system_credential", return_value=(dummy_blob, "antigravity", "comment", 2)):
        account_mgr.save_current_profile("acc1")

    with patch.object(account_mgr, "_write_system_credential", return_value=True) as mock_write:
        activated = account_mgr.activate_profile("acc1")
        assert activated is True
        mock_write.assert_called_once()
        written_blob = mock_write.call_args.kwargs.get("blob") or (mock_write.call_args.args[0] if mock_write.call_args.args else None)
        assert written_blob == dummy_blob


def test_detect_active_profile(account_mgr: AntigravityAccountManager) -> None:
    blob1 = b"blob_account_1"
    blob2 = b"blob_account_2"

    with patch.object(account_mgr, "_read_system_credential", return_value=(blob1, "user1", "", 2)):
        account_mgr.save_current_profile("acc1")

    with patch.object(account_mgr, "_read_system_credential", return_value=(blob2, "user2", "", 2)):
        account_mgr.save_current_profile("acc2")

    # Current system credential matches blob2
    with patch.object(account_mgr, "_read_system_credential", return_value=(blob2, "user2", "", 2)):
        active = account_mgr.get_active_profile()
        assert active == "acc2"


def test_delete_profile(account_mgr: AntigravityAccountManager) -> None:
    dummy_blob = b"fake_blob"
    with patch.object(account_mgr, "_read_system_credential", return_value=(dummy_blob, "user", "", 2)):
        account_mgr.save_current_profile("to_delete")

    assert len(account_mgr.list_profiles()) == 1
    assert account_mgr.delete_profile("to_delete") is True
    assert len(account_mgr.list_profiles()) == 0
    assert account_mgr.delete_profile("non_existent") is False
