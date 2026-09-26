"""
Unit tests for Advanced Personal Use Mode:
- One-Way Safety Guard & Lockfile
- File Transfer Security (Upload / Download / Sensitive File Blocklist)
- Personal Task Manager (Agent task execution & lifecycle)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.settings import settings
from app.runner.file_transfer import (
    _is_sensitive_file,
    save_incoming_file,
    validate_download_path,
)
from app.runner.personal_guard import (
    disable_personal_mode_from_telegram,
    enable_personal_mode_locally,
    get_lockfile_path,
    is_locked_by_switch,
    is_personal_mode_active,
)
from app.runner.personal_task import PersonalTaskManager, PersonalTaskResult


@pytest.fixture(autouse=True)
def cleanup_lockfile():
    """Ensure lockfile is cleaned up before and after each test."""
    lockfile = get_lockfile_path()
    if lockfile.exists():
        lockfile.unlink()
    orig_mode = settings.PERSONAL_MODE
    yield
    if lockfile.exists():
        lockfile.unlink()
    settings.PERSONAL_MODE = orig_mode


# ---------------------------------------------------------------------------
# Test One-Way Safety Guard & Lockfile
# ---------------------------------------------------------------------------

def test_personal_mode_guard_config_disabled():
    settings.PERSONAL_MODE = False
    active, reason = is_personal_mode_active()
    assert not active
    assert reason == "disabled_by_config"


def test_personal_mode_guard_active():
    settings.PERSONAL_MODE = True
    active, reason = is_personal_mode_active()
    assert active
    assert reason == "active"


def test_kill_switch_disables_and_locks():
    settings.PERSONAL_MODE = True
    assert not is_locked_by_switch()

    # Disable from Telegram
    success = disable_personal_mode_from_telegram(user_id=989198190)
    assert success
    assert is_locked_by_switch()
    assert get_lockfile_path().exists()

    # Must be disabled now
    active, reason = is_personal_mode_active()
    assert not active
    assert reason == "disabled_by_lockfile"

    # Even if someone tries to set settings.PERSONAL_MODE = True, lockfile blocks it
    settings.PERSONAL_MODE = True
    active, reason = is_personal_mode_active()
    assert not active
    assert reason == "disabled_by_lockfile"

    # Re-enable locally
    enable_success = enable_personal_mode_locally()
    assert enable_success
    assert not is_locked_by_switch()

    active, reason = is_personal_mode_active()
    assert active
    assert reason == "active"


# ---------------------------------------------------------------------------
# Test File Transfer Security
# ---------------------------------------------------------------------------

def test_sensitive_file_pattern_matching():
    assert _is_sensitive_file(".env")
    assert _is_sensitive_file(".env.production")
    assert _is_sensitive_file(".env.local")
    assert _is_sensitive_file("id_rsa")
    assert _is_sensitive_file("my_id_rsa.pub")
    assert _is_sensitive_file("server.key")
    assert _is_sensitive_file("cert.pem")
    assert _is_sensitive_file("agent.db")
    assert _is_sensitive_file("identity.p12")

    # Safe files
    assert not _is_sensitive_file("script.py")
    assert not _is_sensitive_file("README.md")
    assert not _is_sensitive_file("data.json")
    assert not _is_sensitive_file("output.log")


def test_save_incoming_file(tmp_path: Path):
    content = b"print('hello world')\n"
    success, msg, path = save_incoming_file(
        content=content,
        original_filename="test_script.py",
        destination_dir=tmp_path,
    )
    assert success
    assert path is not None
    assert path.exists()
    assert path.name == "test_script.py"
    assert path.read_bytes() == content


def test_save_incoming_file_path_traversal_sanitization(tmp_path: Path):
    content = b"malicious content"
    success, msg, path = save_incoming_file(
        content=content,
        original_filename="../../../etc/passwd",
        destination_dir=tmp_path,
    )
    assert success
    assert path is not None
    # Must be stripped of ../ and saved inside tmp_path
    assert path.parent == tmp_path
    assert ".." not in str(path)


def test_save_incoming_file_oversized(tmp_path: Path):
    # Set limit to 1MB for test
    with patch.object(settings, "PERSONAL_MAX_UPLOAD_SIZE_MB", 1):
        big_content = b"x" * (2 * 1024 * 1024)
        success, msg, path = save_incoming_file(
            content=big_content,
            original_filename="large.zip",
            destination_dir=tmp_path,
        )
        assert not success
        assert path is None
        assert "يتجاوز الحد الأقصى" in msg


def test_validate_download_path_safe_file(tmp_path: Path):
    safe_file = tmp_path / "report.txt"
    safe_file.write_text("All tests passed", encoding="utf-8")

    valid, reason, resolved = validate_download_path(safe_file)
    assert valid
    assert reason == "OK"
    assert resolved == safe_file.resolve()


def test_validate_download_path_sensitive_file(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET=12345", encoding="utf-8")

    valid, reason, resolved = validate_download_path(env_file)
    assert not valid
    assert "حساس ومحمي" in reason
    assert resolved is None


def test_validate_download_path_nonexistent():
    valid, reason, resolved = validate_download_path("non_existent_file_xyz.txt")
    assert not valid
    assert "غير موجود" in reason


def test_validate_download_path_directory(tmp_path: Path):
    valid, reason, resolved = validate_download_path(tmp_path)
    assert not valid
    assert "ليس ملفاً" in reason


# ---------------------------------------------------------------------------
# Test PersonalTaskManager
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_personal_task_runner_success(tmp_path: Path):
    manager = PersonalTaskManager()
    assert not manager.is_busy()

    # Mock agent
    mock_agent = MagicMock()
    mock_agent.name = "mock_agent"
    mock_agent.run = AsyncMock()

    async def fake_run(context, workspace, on_progress):
        await on_progress("Doing step 1...")
        # Create a file in workspace
        (workspace / "result.txt").write_text("Generated by agent", encoding="utf-8")
        from app.agents.base import AgentResult
        return AgentResult(
            success=True,
            exit_code=0,
            summary="Completed successfully",
        )

    mock_agent.run.side_effect = fake_run

    with patch("app.runner.personal_task.agent_manager.get_agent", return_value=mock_agent):
        progress_events = []

        async def track_progress(msg: str):
            progress_events.append(msg)

        result: PersonalTaskResult = await manager.run_task(
            prompt="Create result.txt with greeting",
            workspace_path=tmp_path,
            on_progress=track_progress,
        )

        assert result.success
        assert result.summary == "Completed successfully"
        assert "result.txt" in result.files_created
        assert not manager.is_busy()
        assert len(progress_events) >= 2


@pytest.mark.asyncio
async def test_personal_task_stop(tmp_path: Path):
    manager = PersonalTaskManager()

    mock_agent = MagicMock()
    mock_agent.name = "mock_agent"
    mock_agent.stop = AsyncMock()

    # Run a long task in background
    async def fake_long_run(context, workspace, on_progress):
        await asyncio.sleep(5)
        from app.agents.base import AgentResult
        return AgentResult(success=True, exit_code=0, summary="Done")

    mock_agent.run = AsyncMock(side_effect=fake_long_run)

    with patch("app.runner.personal_task.agent_manager.get_agent", return_value=mock_agent):
        task_future = asyncio.create_task(
            manager.run_task("Long running task", workspace_path=tmp_path)
        )
        # Give it a moment to start
        await asyncio.sleep(0.05)
        assert manager.is_busy()

        stopped = await manager.stop_task()
        assert stopped
        mock_agent.stop.assert_awaited_once()

        result = await task_future
        assert not result.success
        assert not manager.is_busy()
