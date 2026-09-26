# Runbook: flaky or environment-dependent tests

All 83 tests in this repo are mocked: no real subprocess, no real network. A
flake usually means a test leaked state or depends on the host environment.

## Common causes

1. **Leaked settings singleton** — `pydantic-settings` caches the instance in
   `settings_module._settings_instance`. `tests/conftest.py` resets it per test;
   a test that constructs `Settings(...)` directly instead of monkeypatching env
   can leak state into the next test.
2. **Filesystem side effects** — `test_project_scanner.py` writes real fake
   `.git` dirs; `personal_task`/`file_transfer` tests touch temp paths. Cleanup
   failures make subsequent runs fail.
3. **Order dependence** — a module-level singleton (`issue_service`,
   `github_client`, `pr_service`) mutated by one test affects another.
4. **Host variance** — `app/utils/sysmonitor.py` (psutil optional) and Windows
   path assumptions (`C:\`, LOCALAPPDATA) differ per machine; CI is
   ubuntu-only (`.github/workflows/ci.yml`).

## Verification checklist

1. Run the suite in isolation and then shuffled to find order dependence:
   `py -m pytest tests/test_<flaky>.py -p no:randomly`
2. Assert no leaked env: confirm `conftest.py` autouse fixture applies and
   re-run a single test twice.
3. If the test touches a real path, confirm it uses `tmp_path` / `mocker` and
   cleans up.
4. Compare against CI: CI only runs `ruff check .` + `pytest -v --tb=short` on
   Python 3.11/3.12 with `pip install -e ".[dev]"` — a locally-passing,
   CI-failing test likely uses Windows-only assumptions.

## Fix pattern

- Use `mocker.patch` for subprocess/network boundaries; use `tmp_path` for
  filesystem; reset singletons in the fixture, not the test body.