"""
Tests for Telegram inline keyboards.
"""
from app.github.client import RepoInfo
from app.github.issues import IssueInfo
from app.telegram.keyboards import (
    confirm_run_keyboard,
    issue_detail_keyboard,
    issues_keyboard,
    main_menu_keyboard,
    repos_keyboard,
)


def _make_mock_repo(name: str = "demo", private: bool = False) -> RepoInfo:
    return RepoInfo(
        full_name=f"octocat/{name}",
        name=name,
        owner="octocat",
        description="A demo repo",
        clone_url=f"https://github.com/octocat/{name}.git",
        ssh_url=f"git@github.com:octocat/{name}.git",
        default_branch="main",
        private=private,
        html_url=f"https://github.com/octocat/{name}",
    )


def _make_mock_issue(number: int = 1, title: str = "Test Issue") -> IssueInfo:
    return IssueInfo(
        number=number,
        title=title,
        body="Issue description",
        state="open",
        labels=["bug"],
        html_url=f"https://github.com/octocat/demo/issues/{number}",
        repo_full_name="octocat/demo",
    )


def test_main_menu_keyboard():
    kb = main_menu_keyboard()
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "menu:repos" in callbacks
    assert "menu:issues" in callbacks
    assert "menu:run" in callbacks
    assert "menu:newissue" in callbacks
    assert "menu:status" in callbacks


def test_repos_keyboard_pagination_and_cancel():
    # 8 repos triggers Next button
    repos = [_make_mock_repo(f"repo-{i}") for i in range(8)]
    kb = repos_keyboard(
        repos,
        page=1,
        callback_prefix="run_repo:",
        page_prefix="run_repo_page:",
        include_cancel=True,
    )

    all_buttons = [btn for row in kb.inline_keyboard for btn in row]
    callbacks = [btn.callback_data for btn in all_buttons]

    # Check repo callback
    assert "run_repo:octocat/repo-0" in callbacks
    # Check pagination callbacks
    assert "run_repo_page:0" in callbacks  # Prev
    assert "run_repo_page:2" in callbacks  # Next
    # Check cancel button
    assert "cancel" in callbacks


def test_issues_keyboard_pagination_and_cancel():
    # 10 issues triggers Next button
    issues = [_make_mock_issue(i, f"Issue {i}") for i in range(10)]
    kb = issues_keyboard(
        issues,
        repo_full_name="octocat/demo",
        page=0,
        callback_prefix="issue:",
        page_prefix="run_issue_page:",
        include_cancel=True,
    )

    all_buttons = [btn for row in kb.inline_keyboard for btn in row]
    callbacks = [btn.callback_data for btn in all_buttons]

    assert "issue:0" in callbacks
    assert "run_issue_page:1" in callbacks
    assert "cancel" in callbacks


def test_confirm_run_keyboard():
    kb = confirm_run_keyboard("42")
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "confirm_run:42" in callbacks
    assert "cancel" in callbacks


def test_issue_detail_keyboard():
    issue = _make_mock_issue(123)
    kb = issue_detail_keyboard(issue)
    assert kb.inline_keyboard[0][0].url == issue.html_url
    assert kb.inline_keyboard[1][0].callback_data == "run_issue:123"
