from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.github.client import RepoInfo
from app.telegram.handlers import issues, newissue, prs, repos, run, schedule
from app.telegram.keyboards import repos_keyboard


def _long_repo() -> RepoInfo:
    owner = "a" * 39
    name = "private-" + "b" * 92
    return RepoInfo(f"{owner}/{name}", name, owner, "", "", "", "main", True, "")


@pytest.mark.parametrize("prefix", ["repo:", "newissue_repo:", "run_repo:", "sched_repo:",
                                    "issues_for:", "prs_for:"])
def test_long_private_repository_selection_keeps_action_and_exact_identity(prefix):
    from app.telegram.callback_payloads import decode_callback_value

    repo = _long_repo()
    button = repos_keyboard([repo], callback_prefix=prefix).inline_keyboard[0][0]

    assert len(button.callback_data.encode("utf-8")) <= 64
    assert button.callback_data.startswith(prefix)
    assert button.text.startswith("🔒 ")
    assert decode_callback_value(button.callback_data, prefix) == repo.full_name


def test_callback_registry_is_bounded_expires_and_does_not_cross_action_prefixes():
    from app.telegram.callback_payloads import CallbackPayloadExpired, CallbackPayloadRegistry

    clock = [0.0]
    registry = CallbackPayloadRegistry(max_entries=2, ttl=10, clock=lambda: clock[0])
    first = registry.encode("repo:", "x" * 100)
    second = registry.encode("repo:", "y" * 100)
    third = registry.encode("repo:", "z" * 100)
    assert len(registry) == 2
    with pytest.raises(CallbackPayloadExpired):
        registry.decode(first, "repo:")
    with pytest.raises(CallbackPayloadExpired):
        registry.decode(second.replace("repo:", "run_repo:", 1), "run_repo:")
    assert registry.decode(third, "repo:") == "z" * 100
    clock[0] = 11.0
    with pytest.raises(CallbackPayloadExpired):
        registry.decode(third, "repo:")
    assert len(registry) == 0


def test_short_legacy_repository_callbacks_remain_unchanged():
    from app.telegram.callback_payloads import decode_callback_value, encode_callback_payload

    assert encode_callback_payload("repo:", "acme/example") == "repo:acme/example"
    assert decode_callback_value("repo:acme/example", "repo:") == "acme/example"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "callback", "prefix", "service", "state_key"),
    [(run, "run_repo_selected", "run_repo:", "_show_run_issues", "run_repo"),
     (schedule, "sched_repo_selected_callback", "sched_repo:", "_show_issue_select", "sched_repo"),
     (issues, "issues_for_repo_callback", "issues_for:", "_show_issues", "selected_repo"),
     (prs, "prs_for_repo_callback", "prs_for:", "show_prs_for_repo", None)],
)
async def test_picker_consumers_decode_repository_before_service_call(
    monkeypatch, module, callback, prefix, service, state_key,
):
    repo = _long_repo()
    encoded = repos_keyboard([repo], callback_prefix=prefix).inline_keyboard[0][0].callback_data
    query = SimpleNamespace(data=encoded, answer=AsyncMock(), edit_message_text=AsyncMock())
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=123456789, username=None))
    context = SimpleNamespace(user_data={})
    target = AsyncMock()
    monkeypatch.setattr(module, service, target)

    await getattr(module, callback)(update, context)

    assert target.await_count == 1
    assert repo.full_name in target.call_args.args
    assert query.data == encoded
    if state_key:
        assert context.user_data[state_key] == repo.full_name


@pytest.mark.asyncio
async def test_new_issue_picker_decodes_exact_repo_and_expiry_does_not_replace_selection(monkeypatch):
    import app.telegram.callback_payloads as codec

    clock = [0.0]
    monkeypatch.setattr(codec, "_registry", codec.CallbackPayloadRegistry(ttl=10, clock=lambda: clock[0]))
    repo = _long_repo()
    encoded = repos_keyboard([repo], callback_prefix="newissue_repo:").inline_keyboard[0][0].callback_data
    query = SimpleNamespace(data=encoded, answer=AsyncMock(), edit_message_text=AsyncMock())
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(user_data={})

    assert await newissue.ni_repo_selected(update, context) == newissue.NI_ENTER_TITLE
    assert context.user_data["ni_repo"] == repo.full_name
    context.user_data["ni_repo"] = "previous/selection"
    clock[0] = 11.0
    query.answer.reset_mock()
    query.edit_message_text.reset_mock()

    assert await newissue.ni_repo_selected(update, context) == newissue.NI_SELECT_REPO
    assert context.user_data["ni_repo"] == "previous/selection"
    assert query.answer.call_args.kwargs["show_alert"] is True
    assert "انتهت" in query.answer.call_args.args[0]
    query.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_run_picker_does_not_call_github_or_change_context(monkeypatch):
    target = AsyncMock()
    monkeypatch.setattr(run, "_show_run_issues", target)
    query = SimpleNamespace(data="run_repo:~unknown-token", answer=AsyncMock())
    context = SimpleNamespace(user_data={"run_repo": "previous/repo"})

    assert await run.run_repo_selected(SimpleNamespace(callback_query=query), context) == run.RUN_SELECT_REPO
    target.assert_not_awaited()
    assert context.user_data["run_repo"] == "previous/repo"
    assert query.answer.call_args.kwargs["show_alert"] is True


@pytest.mark.asyncio
async def test_repo_detail_picker_decodes_before_github_lookup(monkeypatch):
    repo = _long_repo()
    encoded = repos_keyboard([repo]).inline_keyboard[0][0].callback_data
    query = SimpleNamespace(data=encoded, answer=AsyncMock(), edit_message_text=AsyncMock())
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=123456789, username=None))
    context = SimpleNamespace(user_data={})
    calls = []

    def get_repo(name):
        calls.append(name)
        return repo

    monkeypatch.setattr(repos.github_client, "get_repo_info", get_repo)
    await repos.repo_selected_callback(update, context)
    assert calls == [repo.full_name]
    assert context.user_data["selected_repo"] == repo.full_name
