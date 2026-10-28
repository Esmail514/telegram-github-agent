"""
Tests for CI secret and sensitive file scanner script.
"""
from __future__ import annotations

from unittest.mock import patch

from scripts.ci_check_secrets import check_diff_secrets, check_tracked_files, main


def test_check_tracked_files_detects_banned_files() -> None:
    mock_files = "\n".join([
        "app/main.py",
        "README.md",
        ".env",
        "keys/private.pem",
        "certs/auth.key",
        "agent.db",
        ".personal_mode_disabled",
    ])
    with patch("subprocess.check_output", return_value=mock_files):
        violations = check_tracked_files()

    assert ".env" in violations
    assert "keys/private.pem" in violations
    assert "certs/auth.key" in violations
    assert "agent.db" in violations
    assert ".personal_mode_disabled" in violations
    assert "app/main.py" not in violations
    assert "README.md" not in violations


def test_check_tracked_files_allows_env_example() -> None:
    mock_files = "\n".join([
        "app/main.py",
        ".env.example",
        "pyproject.toml",
    ])
    with patch("subprocess.check_output", return_value=mock_files):
        violations = check_tracked_files()

    assert violations == []


def test_check_diff_secrets_detects_secrets() -> None:
    token_prefix = "gh" + "p_"
    token_val = token_prefix + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef1234"
    diff = f"+ GITHUB_TOKEN={token_val}\n"
    with patch("subprocess.check_output", return_value=diff):
        findings = check_diff_secrets()
    assert len(findings) > 0


def test_main_cli_returns_zero_on_clean() -> None:
    with (
        patch("scripts.ci_check_secrets.check_tracked_files", return_value=[]),
        patch("scripts.ci_check_secrets.check_diff_secrets", return_value=[]),
        patch("sys.argv", ["ci_check_secrets.py", "--check-diff"]),
    ):
        exit_code = main()
        assert exit_code == 0


def test_main_cli_returns_one_on_violation() -> None:
    with (
        patch("scripts.ci_check_secrets.check_tracked_files", return_value=[".env"]),
        patch("sys.argv", ["ci_check_secrets.py"]),
    ):
        exit_code = main()
        assert exit_code == 1

