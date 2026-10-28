"""
Tests for Telegram inline keyboards.
"""
from pathlib import Path

from app.github.client import RepoInfo
from app.github.issues import IssueInfo
from app.runner.project_scanner import LocalProject
from app.telegram.keyboards import (
    CB_SETDIR,
    CB_WS_CLONE,
    CB_WS_LOCAL,
    confirm_run_keyboard,
    issue_detail_keyboard,
    issues_keyboard,
    main_menu_keyboard,
    polish_newissue_keyboard,
    project_detail_keyboard,
    projects_keyboard,
    repos_keyboard,
    sysinfo_keyboard,
    workspace_source_keyboard,
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
    from app.telegram.keyboards import github_menu_keyboard
    kb = main_menu_keyboard()
    labels = [btn.text for row in kb.keyboard for btn in row]
    # Top-level items always present
    assert "📁 المشاريع المحلية" in labels
    assert "📊 حالة المهمة" in labels
    assert "🐙 GitHub" in labels
    assert "🖥️ موارد الجهاز" in labels
    assert "💻 الاستخدام الشخصي" in labels
    # GitHub items live inside the github submenu, not the main menu
    assert "المستودعات" not in labels
    github_callbacks = [btn.callback_data for row in github_menu_keyboard().inline_keyboard for btn in row]
    assert github_callbacks == ["menu:newissue", "menu:schedule:issue", "menu:schedule:local"]


def test_navigation_buttons_do_not_offer_cancel():
    keyboard = repos_keyboard([], include_cancel=True)
    labels = [button.text for row in keyboard.inline_keyboard for button in row]

    assert all("إلغاء" not in label for label in labels)


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
    assert "cancel" not in callbacks


def test_private_repository_has_lock_indicator():
    keyboard = repos_keyboard([_make_mock_repo("private-repo", private=True)])

    assert keyboard.inline_keyboard[0][0].text == "🔒 private-repo"


def test_sysinfo_keyboard_offers_only_refresh_and_back():
    keyboard = sysinfo_keyboard()
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]

    assert callbacks == ["sysinfo:refresh", "sysinfo:back"]


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
    assert "cancel" not in callbacks


def test_confirm_run_keyboard():
    kb = confirm_run_keyboard("42")
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "confirm_run:antigravity:42" in callbacks
    assert "confirm_run:codex:42" in callbacks
    assert "cancel" not in callbacks


def test_issue_detail_keyboard():
    issue = _make_mock_issue(123)
    kb = issue_detail_keyboard(issue)
    callbacks = [button.callback_data for row in kb.inline_keyboard for button in row]
    assert "polish_issue:123" in callbacks
    assert "run_issue:123" not in callbacks
    assert all(button.url is None for row in kb.inline_keyboard for button in row)


def test_new_issue_confirmation_offers_create_and_edit() -> None:
    keyboard = polish_newissue_keyboard()
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]

    assert "إنشاء الآن" in labels
    assert "تعديل المسودة" in labels
    assert "newissue:confirm" in callbacks
    assert "polish_newissue" in callbacks


def test_workspace_source_keyboard():
    kb = workspace_source_keyboard()
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert CB_WS_CLONE in callbacks
    assert CB_WS_LOCAL in callbacks
    assert "cancel" not in callbacks


def test_projects_keyboard():
    projects = [
        LocalProject(
            name=f"project_{i}",
            path=Path(f"/workspace/project_{i}"),
            repo_full_name=f"acme/project-{i}",
            is_git=True,
        )
        for i in range(10)
    ]
    kb = projects_keyboard(projects, page=0, include_cancel=True, include_setdir=True)
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]

    # First page should contain project 0
    assert "proj:0" in callbacks
    # Next page button
    assert "proj_page:1" in callbacks
    # Change dir and cancel
    assert CB_SETDIR in callbacks
    assert "cancel" not in callbacks


def test_project_detail_keyboard():
    # With GitHub remote
    kb1 = project_detail_keyboard(0, has_github_remote=True, repo_full_name="acme/demo")
    cbs1 = [btn.callback_data for row in kb1.inline_keyboard for btn in row]
    assert "issues_for:acme/demo" in cbs1
    assert "newissue_for:acme/demo" in cbs1
    assert "run_proj:0" in cbs1
    assert "prs_for:acme/demo" not in cbs1

    # Local only (no GitHub remote)
    kb2 = project_detail_keyboard(1, has_github_remote=False)
    cbs2 = [btn.callback_data for row in kb2.inline_keyboard for btn in row]
    assert "run_proj:1" in cbs2
    assert "issues_for:acme/demo" not in cbs2



