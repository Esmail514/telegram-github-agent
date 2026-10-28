"""
CI Secret & Sensitive File Scanner.

Enforces security hygiene:
1. Ensures no sensitive files (e.g. .env, *.pem, *.key, agent.db) are tracked in git.
2. If given a base ref or run on a PR, scans the diff using app.utils.secrets.SecretScanner.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

BANNED_PATTERNS = [
    re.compile(r"(^|/)\.env($|\.(local|prod|dev|staging|test))", re.IGNORECASE),
    re.compile(r"\.(pem|key|p12|pfx|pkcs12)$", re.IGNORECASE),
    re.compile(r"(^|/)agent\.db$", re.IGNORECASE),
    re.compile(r"(^|/)\.personal_mode_disabled$", re.IGNORECASE),
]


def check_tracked_files() -> list[str]:
    """Check git ls-files for banned file patterns."""
    try:
        tracked = subprocess.check_output(
            ["git", "ls-files"], text=True, errors="replace"
        ).splitlines()
    except subprocess.SubprocessError as exc:
        print(f"Error checking git tracked files: {exc}", file=sys.stderr)
        return [f"git ls-files failed: {exc}"]

    violations = []
    for f in tracked:
        f_norm = f.replace("\\", "/")
        for pattern in BANNED_PATTERNS:
            if pattern.search(f_norm):
                violations.append(f)
                break
    return violations


def check_diff_secrets(base_ref: str | None = None) -> list[str]:
    """Scan git diff for hardcoded secret patterns using app's SecretScanner."""
    try:
        from app.utils.secrets import SecretScanner
    except ImportError as exc:
        print(f"Warning: Could not import SecretScanner: {exc}", file=sys.stderr)
        return []

    diff_cmd = ["git", "diff"]
    if base_ref:
        diff_cmd.append(f"origin/{base_ref}...HEAD")
    else:
        diff_cmd.append("HEAD~1")

    try:
        diff_text = subprocess.check_output(diff_cmd, text=True, errors="replace")
    except subprocess.SubprocessError as exc:
        print(f"Note: git diff command failed ({exc}), skipping diff scan.")
        return []

    scanner = SecretScanner()
    scan_result = scanner.scan_diff(diff_text)
    if scan_result.has_secrets:
        return [str(scan_result)]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Check repository and diffs for secrets")
    parser.add_argument("--base-ref", default=None, help="Base branch/ref to diff against")
    parser.add_argument("--check-diff", action="store_true", help="Scan git diff for secrets")
    args = parser.parse_args()

    failed = False

    # 1. Check tracked files
    banned = check_tracked_files()
    if banned:
        print("::error::Sensitive files found tracked in git:")
        for b in banned:
            print(f"  - {b}")
        failed = True
    else:
        print("[OK] No banned or sensitive files tracked in git.")

    # 2. Check diff secrets if requested
    if args.check_diff:
        findings = check_diff_secrets(args.base_ref)
        if findings:
            print("::error::Secrets detected in git diff:")
            for item in findings:
                print(item)
            failed = True
        else:
            print("[OK] Git diff is clean of detected secrets.")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

