from app.github.client import RepoInfo
from app.telegram.repository_search import filter_repositories


def _repo(owner: str, name: str) -> RepoInfo:
    return RepoInfo(
        full_name=f"{owner}/{name}",
        name=name,
        owner=owner,
        description=None,
        clone_url=f"https://github.com/{owner}/{name}.git",
        ssh_url=f"git@github.com:{owner}/{name}.git",
        default_branch="main",
        private=False,
        html_url=f"https://github.com/{owner}/{name}",
    )


def test_repository_search_accepts_one_character() -> None:
    repos = [_repo("acme", "bot"), _repo("other", "site")]

    assert filter_repositories(repos, "b") == [repos[0]]


def test_repository_search_matches_owner_and_ignores_case() -> None:
    repos = [_repo("OpenAI", "tools"), _repo("acme", "api")]

    assert filter_repositories(repos, "open") == [repos[0]]


def test_repository_search_limits_results() -> None:
    repos = [_repo("acme", f"bot-{index}") for index in range(20)]

    assert len(filter_repositories(repos, "bot", limit=8)) == 8
