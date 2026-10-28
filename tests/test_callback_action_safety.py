from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.github.pull_requests import PRInfo
from app.telegram import keyboards
from app.telegram.callback_payloads import decode_callback_value, encode_callback_payload
from app.telegram.handlers import newissue, prs, run

LONG_REPO = "a" * 39 + "/" + "b" * 100


def _update(data, user_id=123456789):
    return SimpleNamespace(
        callback_query=SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock()),
        effective_user=SimpleNamespace(id=user_id, username=None),
    )


def test_all_repository_actions_and_merge_options_fit_telegram_limit():
    pr = PRInfo(123, "Example", "https://github.com/example/repo/pull/123", "open", "topic", "main")
    builders = [
        keyboards.project_detail_keyboard(1, True, LONG_REPO),
        keyboards.saved_project_detail_keyboard(2, True, LONG_REPO),
        keyboards.pr_detail_keyboard(pr, LONG_REPO),
        keyboards.pr_action_keyboard(pr.html_url, LONG_REPO, 123),
        keyboards.merge_options_keyboard(LONG_REPO, 123),
    ]
    for keyboard in builders:
        for row in keyboard.inline_keyboard:
            for button in row:
                if button.callback_data:
                    assert len(button.callback_data.encode("utf-8")) <= 64
                    for prefix in ("issues_for:", "newissue_for:", "prs_for:"):
                        if button.callback_data.startswith(prefix):
                            assert decode_callback_value(button.callback_data, prefix) == LONG_REPO
                    if button.callback_data.startswith("merge_pr:"):
                        assert decode_callback_value(button.callback_data, "merge_pr:") == f"{LONG_REPO}:123"
                    if button.callback_data.startswith("do_merge:"):
                        decoded = decode_callback_value(button.callback_data, "do_merge:")
                        method, repo, number = decoded.split(":")
                        assert method in {"squash", "merge", "rebase"}
                        assert repo == LONG_REPO and number == "123"


@pytest.mark.asyncio
async def test_encoded_newissue_and_run_actions_restore_exact_repo(monkeypatch):
    context = SimpleNamespace(user_data={})
    new_update = _update(encode_callback_payload("newissue_for:", LONG_REPO))
    assert await newissue.start_newissue_for_repo(new_update, context) == newissue.NI_ENTER_TITLE
    assert context.user_data["ni_repo"] == LONG_REPO

    show = AsyncMock(return_value=run.RUN_SELECT_ISSUE)
    monkeypatch.setattr(run, "_show_run_issues", show)
    run_update = _update(encode_callback_payload("run_for:", LONG_REPO))
    assert await run._run_for_repo(run_update, context) == run.RUN_SELECT_ISSUE
    show.assert_awaited_once_with(run_update, context, LONG_REPO, page=0)


@pytest.mark.asyncio
async def test_merge_action_decodes_before_mutating_github(monkeypatch):
    target = Mock(return_value=SimpleNamespace(merged=True, sha="abc123", message="merged"))
    monkeypatch.setattr(prs.pr_service, "merge_pr", target)
    update = _update(encode_callback_payload("do_merge:", f"rebase:{LONG_REPO}:123"))

    await prs.merge_pr_execute_callback(update, SimpleNamespace(user_data={}))

    assert target.call_args.kwargs["repo_full_name"] == LONG_REPO
    assert target.call_args.kwargs["number"] == 123
    assert target.call_args.kwargs["merge_method"] == "rebase"


@pytest.mark.asyncio
async def test_expired_merge_action_never_calls_github(monkeypatch):
    target = Mock()
    monkeypatch.setattr(prs.pr_service, "merge_pr", target)
    update = _update("do_merge:~expired")

    await prs.merge_pr_execute_callback(update, SimpleNamespace(user_data={}))

    target.assert_not_called()
    assert update.callback_query.answer.call_args.kwargs["show_alert"] is True


@pytest.mark.parametrize("keyboard", [
    keyboards.personal_keyboard(), keyboards.personal_task_running_keyboard(),
    keyboards.status_keyboard(), keyboards.accounts_keyboard([]),
    keyboards.saved_projects_keyboard([]), keyboards.saved_project_detail_keyboard(1, False),
    keyboards.cancel_keyboard(), keyboards.polish_result_keyboard(), keyboards.polish_newissue_keyboard(),
    keyboards.repos_keyboard([], include_cancel=True),
    keyboards.issues_keyboard([], "acme/example", include_cancel=True),
    keyboards.workspace_source_keyboard(), keyboards.confirm_run_keyboard("1"),
])
def test_persistent_menu_screens_do_not_add_finish_or_fake_back_buttons(keyboard):
    for row in keyboard.inline_keyboard:
        for button in row:
            assert "إنهاء" not in button.text
            assert button.callback_data != "cancel"


@pytest.mark.asyncio
@pytest.mark.parametrize(("module", "name", "data"), [
    (newissue, "start_newissue_for_repo", "newissue_for:acme/example"),
    (run, "show_run_start", "menu:run"),
    (run, "run_proj_selected", "run_proj:0"),
    (run, "run_issue_from_detail", "run_issue:1"),
    (run, "_run_for_repo", "run_for:acme/example"),
])
async def test_conversation_entries_reject_unauthorized_callbacks(monkeypatch, module, name, data):
    update = _update(data, user_id=999)
    context = SimpleNamespace(user_data={"selected_repo": "acme/example"})
    monkeypatch.setattr(run.project_scanner, "scan", Mock(return_value=[]))
    show = AsyncMock(return_value=run.RUN_SELECT_ISSUE)
    monkeypatch.setattr(run, "_show_run_issues", show)
    monkeypatch.setattr(run, "show_repo_select_for_run", show)
    monkeypatch.setattr(run.issue_service, "get_issue", Mock(return_value=SimpleNamespace(number=1)))

    assert await getattr(module, name)(update, context) is None
    update.callback_query.answer.assert_not_awaited()
    update.callback_query.edit_message_text.assert_not_awaited()
    show.assert_not_awaited()
