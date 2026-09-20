from unittest.mock import MagicMock, patch

import pytest
from github import GithubException

from app.github.pull_requests import PRInfo, PRMergeResult, PullRequestService
from app.telegram.keyboards import (
    merge_options_keyboard,
    pr_action_keyboard,
    pr_detail_keyboard,
    prs_keyboard,
)


@pytest.fixture
def pr_service():
    return PullRequestService()


def test_create_pr_success(pr_service):
    mock_repo = MagicMock()
    mock_pr = MagicMock()
    mock_pr.number = 42
    mock_pr.title = "feat: add feature"
    mock_pr.html_url = "https://github.com/owner/repo/pull/42"
    mock_pr.state = "open"
    mock_pr.head.ref = "agent/issue-42"
    mock_pr.base.ref = "main"
    mock_pr.user.login = "ai-bot"
    mock_repo.create_pull.return_value = mock_pr

    with patch("app.github.pull_requests.github_client.get_repo", return_value=mock_repo):
        result = pr_service.create_pr(
            repo_full_name="owner/repo",
            title="feat: add feature",
            body="PR description",
            head_branch="agent/issue-42",
            base_branch="main",
        )

        assert isinstance(result, PRInfo)
        assert result.number == 42
        assert result.title == "feat: add feature"
        assert result.head_branch == "agent/issue-42"
        assert result.base_branch == "main"
        assert result.user_login == "ai-bot"


def test_get_pr_success(pr_service):
    mock_repo = MagicMock()
    mock_pr = MagicMock()
    mock_pr.number = 10
    mock_pr.title = "fix: crash"
    mock_pr.html_url = "https://github.com/owner/repo/pull/10"
    mock_pr.state = "open"
    mock_pr.head.ref = "fix-branch"
    mock_pr.base.ref = "main"
    mock_pr.user.login = "octocat"
    mock_repo.get_pull.return_value = mock_pr

    with patch("app.github.pull_requests.github_client.get_repo", return_value=mock_repo):
        result = pr_service.get_pr("owner/repo", 10)

        assert isinstance(result, PRInfo)
        assert result.number == 10
        assert result.title == "fix: crash"
        assert result.user_login == "octocat"


def test_list_prs_success(pr_service):
    mock_repo = MagicMock()
    items = []
    for i in range(1, 4):
        item = MagicMock()
        item.number = i
        item.title = f"PR #{i}"
        item.html_url = f"https://github.com/owner/repo/pull/{i}"
        item.state = "open"
        item.head.ref = f"branch-{i}"
        item.base.ref = "main"
        item.user.login = "dev"
        items.append(item)
    mock_repo.get_pulls.return_value = items

    with patch("app.github.pull_requests.github_client.get_repo", return_value=mock_repo):
        results = pr_service.list_prs("owner/repo", state="open")

        assert len(results) == 3
        assert results[0].number == 1
        assert results[2].number == 3


def test_merge_pr_squash_success(pr_service):
    mock_repo = MagicMock()
    mock_pr = MagicMock()
    mock_status = MagicMock()
    mock_status.merged = True
    mock_status.message = "Pull Request successfully merged"
    mock_status.sha = "abcdef1234567890"
    mock_pr.merge.return_value = mock_status
    mock_repo.get_pull.return_value = mock_pr

    with patch("app.github.pull_requests.github_client.get_repo", return_value=mock_repo):
        result = pr_service.merge_pr(
            repo_full_name="owner/repo",
            number=42,
            commit_title="Merge PR #42",
            merge_method="squash",
        )

        assert isinstance(result, PRMergeResult)
        assert result.merged is True
        assert result.sha == "abcdef1234567890"
        mock_pr.merge.assert_called_once_with(
            merge_method="squash",
            commit_title="Merge PR #42",
        )


def test_merge_pr_rebase_success(pr_service):
    mock_repo = MagicMock()
    mock_pr = MagicMock()
    mock_status = MagicMock()
    mock_status.merged = True
    mock_status.message = "Rebased and merged"
    mock_status.sha = "123456abcdef"
    mock_pr.merge.return_value = mock_status
    mock_repo.get_pull.return_value = mock_pr

    with patch("app.github.pull_requests.github_client.get_repo", return_value=mock_repo):
        result = pr_service.merge_pr(
            repo_full_name="owner/repo",
            number=42,
            merge_method="rebase",
        )

        assert result.merged is True
        assert result.sha == "123456abcdef"
        mock_pr.merge.assert_called_once_with(merge_method="rebase")


def test_merge_pr_github_exception(pr_service):
    mock_repo = MagicMock()
    mock_pr = MagicMock()
    mock_pr.merge.side_effect = GithubException(
        status=405,
        data={"message": "Pull Request is not mergeable"},
        headers={},
    )
    mock_repo.get_pull.return_value = mock_pr

    with patch("app.github.pull_requests.github_client.get_repo", return_value=mock_repo):
        result = pr_service.merge_pr(
            repo_full_name="owner/repo",
            number=42,
        )

        assert isinstance(result, PRMergeResult)
        assert result.merged is False
        assert "not mergeable" in result.message


def test_pr_keyboards():
    pr = PRInfo(
        number=10,
        title="feat: awesome",
        html_url="https://github.com/owner/repo/pull/10",
        state="open",
        head_branch="feature",
        base_branch="main",
    )

    # 1. prs_keyboard
    kb_list = prs_keyboard([pr], "owner/repo", page=0)
    assert len(kb_list.inline_keyboard) == 1
    assert kb_list.inline_keyboard[0][0].callback_data == "pr:10"

    # 2. pr_detail_keyboard
    kb_detail = pr_detail_keyboard(pr, "owner/repo")
    assert any("merge_pr:owner/repo:10" in b.callback_data for row in kb_detail.inline_keyboard for b in row if b.callback_data)

    # 3. pr_action_keyboard
    kb_action = pr_action_keyboard(pr.html_url, "owner/repo", 10)
    assert any("merge_pr:owner/repo:10" in b.callback_data for row in kb_action.inline_keyboard for b in row if b.callback_data)

    # 4. merge_options_keyboard
    kb_opts = merge_options_keyboard("owner/repo", 10)
    callbacks = [b.callback_data for row in kb_opts.inline_keyboard for b in row if b.callback_data]
    assert "do_merge:squash:owner/repo:10" in callbacks
    assert "do_merge:merge:owner/repo:10" in callbacks
    assert "do_merge:rebase:owner/repo:10" in callbacks
