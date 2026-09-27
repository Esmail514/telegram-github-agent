from __future__ import annotations

from app.github.client import RepoInfo


def filter_repositories(
    repositories: list[RepoInfo], query: str, *, limit: int = 8
) -> list[RepoInfo]:
    needle = query.strip().casefold()
    if not needle:
        return []
    matches = [repo for repo in repositories if needle in repo.full_name.casefold()]
    return matches[: max(1, limit)]
