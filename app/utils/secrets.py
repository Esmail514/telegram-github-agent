"""
Secret scanner used before git commits to prevent accidental credential leaks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

@dataclass
class SecretPattern:
    name: str
    pattern: re.Pattern[str]
    description: str


_PATTERNS: list[SecretPattern] = [
    SecretPattern(
        name="github_pat",
        pattern=re.compile(r"ghp_[A-Za-z0-9]{36}", re.IGNORECASE),
        description="GitHub Personal Access Token",
    ),
    SecretPattern(
        name="github_app_token",
        pattern=re.compile(r"ghs_[A-Za-z0-9]{36}", re.IGNORECASE),
        description="GitHub App installation token",
    ),
    SecretPattern(
        name="openai_key",
        pattern=re.compile(r"sk-[A-Za-z0-9]{32,}", re.IGNORECASE),
        description="OpenAI / Anthropic API key",
    ),
    SecretPattern(
        name="aws_access_key",
        pattern=re.compile(r"AKIA[0-9A-Z]{16}", re.IGNORECASE),
        description="AWS access key ID",
    ),
    SecretPattern(
        name="aws_secret_key",
        pattern=re.compile(r"(?i)aws[_\-]?secret[_\-]?access[_\-]?key\s*=\s*['\"]?[A-Za-z0-9/+]{40}"),
        description="AWS secret access key",
    ),
    SecretPattern(
        name="google_api_key",
        pattern=re.compile(r"AIza[0-9A-Za-z_\-]{35}", re.IGNORECASE),
        description="Google API key",
    ),
    SecretPattern(
        name="private_key_header",
        pattern=re.compile(r"-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----"),
        description="PEM private key",
    ),
    SecretPattern(
        name="telegram_bot_token",
        pattern=re.compile(r"\d{8,10}:[A-Za-z0-9_\-]{35}", re.IGNORECASE),
        description="Telegram bot token",
    ),
    SecretPattern(
        name="slack_token",
        pattern=re.compile(r"xox[baprs]-[0-9A-Za-z\-]{10,}"),
        description="Slack token",
    ),
    SecretPattern(
        name="bearer_token",
        pattern=re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._\-]+"),
        description="Bearer authorization header",
    ),
    SecretPattern(
        name="password_assignment",
        pattern=re.compile(
            r'(?i)(?:password|passwd|pwd)\s*[=:]\s*["\'][^"\']{8,}["\']'
        ),
        description="Inline password assignment",
    ),
]

# Files to never scan (binary / lock files / examples)
_SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".pdf", ".zip", ".tar", ".gz", ".whl", ".pyc",
    ".lock", ".sum",
}
_SKIP_FILENAMES = {".env.example", "requirements.txt"}


# ---------------------------------------------------------------------------
# Detection result
# ---------------------------------------------------------------------------

@dataclass
class SecretFinding:
    file: str
    line: int
    pattern_name: str
    description: str
    snippet: str  # Redacted snippet for display


@dataclass
class ScanResult:
    findings: list[SecretFinding]

    @property
    def has_secrets(self) -> bool:
        return len(self.findings) > 0

    def __str__(self) -> str:
        if not self.has_secrets:
            return "No secrets detected."
        lines = [f"⚠️  {len(self.findings)} potential secret(s) detected:\n"]
        for f in self.findings:
            lines.append(f"  • {f.file}:{f.line} — {f.description}")
            lines.append(f"    {f.snippet}\n")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class SecretScanner:
    """Scans files or staged git diff output for secret patterns."""

    def scan_file(self, path: Path) -> ScanResult:
        """Scan a single file for secrets."""
        findings: list[SecretFinding] = []

        if path.suffix.lower() in _SKIP_EXTENSIONS:
            return ScanResult(findings)
        if path.name in _SKIP_FILENAMES:
            return ScanResult(findings)

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ScanResult(findings)

        for line_no, line in enumerate(content.splitlines(), start=1):
            for pattern in _PATTERNS:
                if pattern.pattern.search(line):
                    # Redact the matching value for display
                    snippet = line.strip()[:120]
                    snippet = _redact_line(snippet)
                    findings.append(
                        SecretFinding(
                            file=str(path),
                            line=line_no,
                            pattern_name=pattern.name,
                            description=pattern.description,
                            snippet=snippet,
                        )
                    )

        return ScanResult(findings)

    def scan_diff(self, diff_text: str) -> ScanResult:
        """Scan a git diff string for secrets in added lines."""
        findings: list[SecretFinding] = []
        current_file = "<unknown>"
        line_no = 0

        for line in diff_text.splitlines():
            if line.startswith("diff --git"):
                parts = line.split(" b/")
                current_file = parts[-1] if len(parts) > 1 else "<unknown>"
                line_no = 0
            elif line.startswith("@@"):
                # Extract starting line number from hunk header
                import re as _re
                m = _re.search(r"\+(\d+)", line)
                line_no = int(m.group(1)) - 1 if m else 0
            elif line.startswith("+") and not line.startswith("+++"):
                line_no += 1
                added = line[1:]  # Strip leading '+'
                for pattern in _PATTERNS:
                    if pattern.pattern.search(added):
                        snippet = added.strip()[:120]
                        snippet = _redact_line(snippet)
                        findings.append(
                            SecretFinding(
                                file=current_file,
                                line=line_no,
                                pattern_name=pattern.name,
                                description=pattern.description,
                                snippet=snippet,
                            )
                        )
            elif line.startswith(" "):
                line_no += 1

        return ScanResult(findings)

    def scan_directory(self, path: Path, extensions: set[str] | None = None) -> ScanResult:
        """Recursively scan all text files in a directory."""
        all_findings: list[SecretFinding] = []
        for file in path.rglob("*"):
            if not file.is_file():
                continue
            if any(part.startswith(".") for part in file.parts):
                # Skip hidden dirs like .git
                continue
            if extensions and file.suffix.lower() not in extensions:
                continue
            result = self.scan_file(file)
            all_findings.extend(result.findings)
        return ScanResult(all_findings)


def _redact_line(line: str) -> str:
    """Apply redaction to a display snippet."""
    for pattern in _PATTERNS:
        line = pattern.pattern.sub("***REDACTED***", line)
    return line
