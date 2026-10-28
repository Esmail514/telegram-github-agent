from unittest.mock import AsyncMock

import pytest

from app.runner.git import GitService


@pytest.mark.asyncio
async def test_automatic_stage_excludes_runtime_csv(tmp_path, monkeypatch):
    service = GitService()
    run = AsyncMock(return_value=(0, "", ""))
    monkeypatch.setattr(service, "_run", run)

    await service.add_all(tmp_path)

    args = run.call_args.args
    assert args[:3] == ("git", "add", "-A")
    assert ":(exclude,top)local_prompt_schedules.csv" in args
    assert ":(exclude,top)local_prompt_schedules.csv.tmp" in args


@pytest.mark.asyncio
async def test_tracked_runtime_csv_blocks_commit_without_removal(tmp_path, monkeypatch):
    service = GitService()
    run = AsyncMock(return_value=(0, "local_prompt_schedules.csv\x00", ""))
    monkeypatch.setattr(service, "_run", run)

    with pytest.raises(RuntimeError, match="runtime|schedule"):
        await service.commit(tmp_path, "fix: task")

    assert not any(call.args[:2] == ("git", "commit") for call in run.call_args_list)
    assert not any("rm" in call.args or "reset" in call.args for call in run.call_args_list)


@pytest.mark.asyncio
async def test_status_ignores_only_root_runtime_files(tmp_path, monkeypatch):
    service = GitService()
    run = AsyncMock(side_effect=[
        (0, "ai/issue-1\n", ""),
        (0, "?? local_prompt_schedules.csv\0?? local_prompt_schedules.csv.tmp\0"
         " M main.py\0?? fixtures/local_prompt_schedules.csv\0", ""),
    ])
    monkeypatch.setattr(service, "_run", run)
    status = await service.status(tmp_path)
    assert status.modified == ["main.py"]
    assert status.untracked == ["fixtures/local_prompt_schedules.csv"]


@pytest.mark.asyncio
async def test_push_blocks_private_runtime_data_in_outgoing_history(tmp_path, monkeypatch):
    service = GitService()
    async def git_result(*args, **kwargs):
        if args[1] == "log":
            return 0, "private-runtime-commit\n", ""
        return 0, "", ""
    run = AsyncMock(side_effect=git_result)
    monkeypatch.setattr(service, "_run", run)

    with pytest.raises(RuntimeError, match="history|outgoing"):
        await service.push(tmp_path, branch="ai/issue-1")
    assert not any(call.args[:2] == ("git", "push") for call in run.call_args_list)


@pytest.mark.asyncio
async def test_review_contains_staged_and_untracked_changes(tmp_path, monkeypatch):
    service = GitService()
    (tmp_path / "new.py").write_text("print('new change')\n", encoding="utf-8")
    async def git_result(*args, **kwargs):
        if args[1] == "rev-parse":
            return 0, "ai/issue-1\n", ""
        if args[1] == "status":
            return 0, "?? new.py\0", ""
        return 0, "staged change" if "--staged" in args else "unstaged change", ""
    monkeypatch.setattr(service, "_run", AsyncMock(side_effect=git_result))
    review = await service.review_diff(tmp_path)
    assert "staged change" in review
    assert "unstaged change" in review
    assert "print('new change')" in review
