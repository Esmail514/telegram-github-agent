"""
Tests for ProjectScanner and local project discovery.
"""
import tempfile
from pathlib import Path

from app.config.settings import settings
from app.runner.project_scanner import (
    ProjectScanner,
    parse_github_repo_from_url,
)


def test_parse_github_repo_from_url():
    # SSH format
    assert (
        parse_github_repo_from_url("git@github.com:owner/repo.git")
        == "owner/repo"
    )
    # HTTPS format
    assert (
        parse_github_repo_from_url("https://github.com/owner/repo.git")
        == "owner/repo"
    )
    # HTTPS without .git
    assert (
        parse_github_repo_from_url("https://github.com/owner/repo")
        == "owner/repo"
    )
    # Tokenized HTTPS
    assert (
        parse_github_repo_from_url("https://x-access-token:secret@github.com/owner/repo.git")
        == "owner/repo"
    )
    # Non-GitHub
    assert parse_github_repo_from_url("https://gitlab.com/owner/repo.git") is None
    assert parse_github_repo_from_url("invalid-url") is None


def test_project_scanner_discover_repos():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Create project 1: git repo with origin and branch
        p1 = root / "app_one"
        p1.mkdir()
        p1_git = p1 / ".git"
        p1_git.mkdir()
        (p1_git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (p1_git / "config").write_text(
            '[core]\n\trepositoryformatversion = 0\n'
            '[remote "origin"]\n\turl = https://github.com/acme/app-one.git\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n',
            encoding="utf-8",
        )

        # Create project 2: non-git folder
        p2 = root / "plain_docs"
        p2.mkdir()

        # Create project 3: git repo with detached head and ssh url
        p3 = root / "service_three"
        p3.mkdir()
        p3_git = p3 / ".git"
        p3_git.mkdir()
        (p3_git / "HEAD").write_text("a1b2c3d4e5f6\n", encoding="utf-8")
        (p3_git / "config").write_text(
            '[remote "origin"]\n\turl = git@github.com:acme/service-three.git\n',
            encoding="utf-8",
        )

        scanner = ProjectScanner()
        projects = scanner.scan(root)

        # Should discover all 3
        assert len(projects) == 3

        # p1 assertions
        p1_proj = scanner.get_project_by_name("app_one", directory=root)
        assert p1_proj is not None
        assert p1_proj.is_git is True
        assert p1_proj.branch == "main"
        assert p1_proj.repo_full_name == "acme/app-one"
        assert p1_proj.has_github_remote is True

        # p2 assertions
        p2_proj = scanner.get_project_by_name("plain_docs", directory=root)
        assert p2_proj is not None
        assert p2_proj.is_git is False
        assert p2_proj.repo_full_name is None
        assert p2_proj.has_github_remote is False

        # p3 assertions
        p3_proj = scanner.get_project_by_repo("acme/service-three", directory=root)
        assert p3_proj is not None
        assert p3_proj.name == "service_three"
        assert p3_proj.branch == "a1b2c3d"


def test_project_scanner_empty_or_missing():
    scanner = ProjectScanner()
    assert scanner.scan(None) == []
    assert scanner.scan(Path("/non/existent/path/for/sure/12345")) == []


def test_settings_set_projects_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir)
        settings.set_projects_dir(target)
        assert settings.PROJECTS_DIR == target.resolve()

        settings.set_projects_dir(None)
        assert settings.PROJECTS_DIR is None
