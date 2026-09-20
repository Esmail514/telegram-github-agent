# Pull Request Workflows & Merging Reference

## 1. Creating a Pull Request Programmatically

```python
from app.github.pull_requests import pr_service

pr = pr_service.create_pr(
    repo_full_name="owner/repo",
    title="fix: resolve login crash",
    body="Closes #42\n\n## Changes\n- Fixed null check in auth controller",
    head_branch="agent/issue-42",
    base_branch="main",
)
print(f"Created PR #{pr.number}: {pr.html_url}")
```

## 2. Merging a Pull Request

```python
result = pr_service.merge_pr(
    repo_full_name="owner/repo",
    number=42,
    commit_title="Merge PR #42: fix: resolve login crash",
    merge_method="squash",  # "squash" | "merge" | "rebase"
)

if result.merged:
    print(f"Successfully merged! SHA: {result.sha}")
else:
    print(f"Merge failed: {result.message}")
```

## 3. Handling Common GitHub Errors
- **HTTP 405 (Method Not Allowed)**: Pull Request is not mergeable (e.g. merge conflicts with base branch).
- **HTTP 409 (Conflict)**: Head branch was modified or base branch requires a status check that hasn't passed.
- **HTTP 404 (Not Found)**: Repository or PR does not exist, or GitHub App lacks write permissions.
