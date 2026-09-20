---
name: github-agent-orchestrator
description: >-
  Guidelines, runbooks, and patterns for orchestrating GitHub operations: cloning,
  isolated branch checkout, code inspection, commit scanning for secrets, PR creation,
  and automated or interactive merging. Use when modifying GitHub services, Git operations,
  pull request handlers, or repository workflows.
---

# GitHub Agent Orchestrator Skill

This skill documents how to safely orchestrate autonomous coding agents against GitHub
repositories, create Pull Requests, and execute merges with full safety checks.

## 1. Authentication Strategy

The bot supports two authentication strategies in order of preference:
1. **GitHub App (Recommended)**:
   - Uses `GITHUB_APP_ID`, `GITHUB_INSTALLATION_ID`, and a `.pem` private key.
   - Generates short-lived installation access tokens (1 hour expiry) via PyJWT and cryptography.
   - Higher rate limits (5,000 req/hr per installation).
2. **Personal Access Token (PAT Fallback)**:
   - Uses `GITHUB_TOKEN` with `repo` scopes.

Always access GitHub through `app.github.client.github_client` which manages token caching and refreshing automatically.

---

## 2. Safe Workspace & Branching Isolation

### Git Branch Workflow
1. **Never commit to default branch (`main` / `master`)**:
   Always create an isolated branch for every job:
   ```python
   branch = f"agent/issue-{issue_number}"
   ```
2. **Identity Configuration**:
   Before committing, configure the bot identity in the cloned repository:
   ```python
   await git_service.configure_identity(workspace_path, "AI Agent", "ai-agent@noreply.local")
   ```
3. **Workspace Isolation**:
   Keep cloned workspaces in `./workspaces/{owner}/{repo}` to allow reuse across jobs while isolating branches.

---

## 3. Secret Scanning Before Commit

Before creating a Git commit, **ALWAYS** inspect the staged changes for sensitive credentials:
- AWS keys (`AKIA...`)
- GitHub tokens (`ghp_...`, `gho_...`, `github_pat_...`)
- Private keys (`-----BEGIN PRIVATE KEY-----`)
- OpenAI / Anthropic API keys (`sk-...`)
- Generic password assignments in configuration files

If a secret pattern matches in `git diff`, abort the commit immediately:
```python
raise SecretDetectedError("Commit blocked: potential secret detected in changes.")
```

---

## 4. Pull Request Creation & Merging

### PR Creation
- **Title**: Standardized concise title (`make_pr_title(issue_title)`).
- **Body**: Structured template with agent summary, issue link, files changed, and validation results.
- **Base branch**: Default branch of the target repository (`repo_info.default_branch`).

### PR Merging
Supported merge strategies:
- **`squash`** (Default): Combines all agent iteration commits into a single clean commit on the base branch.
- **`merge`**: Creates a standard two-parent merge commit preserving full branch history.
- **`rebase`**: Re-applies individual commits linearly onto the base branch.

See [pr_workflows.md](./references/pr_workflows.md) for full examples and error handling.
