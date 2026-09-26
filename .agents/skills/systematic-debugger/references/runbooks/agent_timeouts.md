# Runbook: agent subprocess hangs and timeouts

Applies to `app/agents/antigravity.py`, `app/agents/opencode.py`,
`app/agents/codex.py`, and the git/workspace subprocesses in
`app/runner/git.py` / `app/runner/workspace.py`.

## Symptoms

- `asyncio.wait_for(process.wait(), timeout=...)` raises `TimeoutError`; the
  executor reports a failed job.
- The job stays ACTIVE in the database with no further progress notifications.
- `get_active_job()` in `app/database/repository.py` returns a job that never
  advances.

## Known timeouts (settings)

- `MAX_AGENT_RUNTIME_MINUTES` — total agent runtime.
- `GIT_OPERATION_TIMEOUT_SECONDS` — git commit/push operations.
- `GIT_NETWORK_TIMEOUT_SECONDS` — clone/pull network operations.
- `TELEGRAM_REQUEST_TIMEOUT` — bot API calls (`app/config/settings.py`).

## Verification checklist (in order)

1. **Is it a real hang or slow network?** Inspect the failure message; the
   workspace remains available at `workspaces/{owner}/{repo}`. Check
   `agent.db` -> `jobs` for the failing `exit_code`/`error`.
2. **Is the process still alive?** If `stop()` failed, the child may still run.
   Verify the agent backend `stop()` terminates (`process.terminate()`) and,
   where implemented, kills after a short grace (**3s** in opencode) —
   see the *Graceful Termination* sections of each agent skill.
3. **Is the command resolvable on this host?** Antigravity and Codex do CLI
   discovery (`which`/LOCALAPPDATA) before spawning — a missing binary fails
   before execution, not as a hang.
4. **Is an agent writing progress at all?** The opencode backend throttles
   progress to every `_PROGRESS_THROTTLE_SECS` (10s). Zero progress + alive
   process ⇒ the CLI is reading stdin or waiting on interactive input; ensure
   non-interactive flags (`--auto`, `--format json`, `--prompt`).

## Fix pattern

- Reproduce headless with a mocked subprocess test, mirroring
  `tests/test_git_timeout.py` (patches `asyncio.create_subprocess_exec` and
  asserts the process is killed on timeout).
- Ensure every `create_subprocess_exec` call site wraps `process.wait()` in
  `asyncio.wait_for` and calls `process.kill()` in the except path.