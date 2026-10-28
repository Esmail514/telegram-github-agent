# Architecture and data ownership

Paths below are relative to the repository root. Follow current callers and
tests; this map is not a claim that every intended integration is implemented.

## Runtime and entry points

| Concern | Source entry points | Verification anchors |
|---|---|---|
| Bootstrap and shutdown | `app/main.py`: `main`, `_async_main` | `tests/test_lifecycle_notifications.py` (scheduler notifications, not a full startup test) |
| Development reload | `app/reloader.py`: `wait_for_source_change`; `_async_main` waits for active work | `tests/test_reloader.py` |
| Settings | `app/config/settings.py`: `Settings`, `get_settings`, lazy settings proxy | `tests/test_settings.py`, `tests/conftest.py` |
| Handler registration and priority | `app/telegram/bot.py`: `build_application` | Trace the registered handler, not just the callback implementation |
| Authorized operator | `app/telegram/auth.py`: `auth_required` | `tests/test_auth.py` |
| Main reply keyboard | `app/telegram/keyboards/__init__.py`; `app/telegram/handlers/start.py` | `tests/test_keyboards.py`, `tests/test_ui_style.py` |

Python 3.11+ is declared. Telegram uses `python-telegram-bot` 21.x with async
handlers; SQLite uses `aiosqlite`; settings use `pydantic-settings`; GitHub uses
PyGithub. Read both `requirements.txt` and `pyproject.toml`: CI installs
`pip install -e ".[dev]"`, which is not the same dependency path as the README's
requirements installation. CI tests Python 3.11/3.12 on Linux; a Windows test run
does not verify that matrix.

## Main execution routes

```text
Telegram handler / confirmed schedule
  -> SQLite Issue schedule OR project CSV local-prompt schedule
  -> JobScheduler (due time + active-job check + claim)
  -> JobExecutor.start_job / start_local_prompt_job
  -> workspace + context -> AgentRouter -> AgentManager -> CLI adapter
  -> verification / approval / Git actions as applicable
  -> persistent job status + completion callback / queue wake
```

- `app/runner/executor.py` owns reservation of the execution slot and the job
  lifecycle. Its `_start_lock` serializes starts; status checks use SQLite.
- `app/runner/scheduler.py` owns due ordering, dispatch claims, and wakeups. The
  scheduler polls every 60 seconds and can be woken after task completion.
- `app/runner/jobs.py` owns generated job IDs and Issue branch names.
- `app/runner/context_builder.py` constructs `AgentContext` from task framing and
  external repository content. `app/agents/base.py` defines the agent contract.
- `app/runner/fallback.py` owns failure classification and agent routing;
  `app/agents/transport.py` owns transport abstractions. Do not assume every
  adapter uses the generic transport: trace its actual `run` method.
- `app/agents/manager.py` currently registers `codex` and `antigravity` only.
  Legacy `claude.py`, `gemini.py`, and `opencode.py` are not evidence of enabled
  agents. Current CLI arguments live in the concrete adapter.
- `app/runner/verification.py` loads controller-configured checks, not commands
  from the target repo. `app/runner/approvals.py` and
  `app/telegram/handlers/approvals.py` own controller approval records/decisions.
- `app/runner/recovery.py` owns `RecoveryService.recover` and resumability
  markers. Inspect `_async_main` and `JobExecutor.resume_job` separately to
  establish what is recovered and what execution options survive a restart.

An initial local-only run still performs verification, applicable approval
gates, and a local commit. It returns before push/PR; local-only does not mean
"no Git changes" or "no approvals". Verify that same contract on resume.

Relevant tests: `test_executor.py`, `test_agent_manager.py`,
`test_cli_only_agents.py`, `test_codex_agent.py`, `test_recovery.py`,
`test_token_limit_resume.py`, `test_job_state.py`, `test_approval_policy.py`,
`test_git_timeout.py`, `test_context_builder.py`.

## Persistent state: do not merge these owners

`app/database/repository.py` creates the SQLite connection, configures WAL,
applies idempotent column migrations, and exposes repositories. The default
`agent.db` path is relative to the process working directory. A test must use a
temporary path and reset the singleton as `tests/conftest.py` does.

| Storage | Meaning / owner |
|---|---|
| `jobs` | Execution status, branch, workspace, chat; `JobRepository` |
| `job_state` | Resume metadata/stage; `JobStateRepository` |
| `job_events` | Structured progress; `JobEventRepository` |
| `verification_results`, `agent_attempts`, `approvals` | Verification, fallback and human-decision audit records |
| `sessions` | Selected GitHub repo/Issue session data |
| `saved_projects` | Multiple named local paths per user; `SavedProjectRepository` |
| `selected_projects` | Currently selected local project per user; `SelectedProjectRepository` |
| `project_roots` | Per-user browsing/scanning root; `ProjectRootRepository` |
| `scheduled_jobs` | Issue schedules; `app/database/schedule_repository.py` |
| Project `local_prompt_schedules.csv` | Local prompt schedules; `app/runner/local_prompt_schedules.py` |

DDL is in `app/database/models.py` and `schedule_models.py`. The `MIGRATIONS`
list must cover existing DBs as well as fresh-table DDL. Inspect real DDL before
assuming table relations have foreign keys or cascade behavior; enabling SQLite
foreign-key support does not create constraints.

Project paths also occur in scheduled jobs and historical execution metadata.
Changing or clearing a chosen location is not equivalent to deleting projects,
jobs, or their audit history. A review or new skill never authorizes a path reset.

## Project identity and browsing

- `app/telegram/handlers/projects.py` coordinates adding/selecting projects and
  `/setdir`. `app/telegram/directory_browser.py` holds navigation state/rendering;
  `app/runner/directory_browser.py` performs filesystem/drive operations.
- `app/runner/project_scanner.py` inspects Git metadata and remotes, with a
  short-lived scan cache.
- `app/runner/local_projects.py` supports legacy IDs stored under Git metadata,
  resolved inside `PROJECTS_DIR`. A `saved:<id>` reference instead resolves via
  the saved-project database; do not interchange these identifiers.
- Selected local workspaces are user-owned. They are not disposable clones.
  `app/runner/workspace.py` and `git.py` must preserve that distinction.

Relevant tests: `test_directory_browser.py`, `test_project_scanner.py`,
`test_database.py`, `test_local_prompt_schedules.py`. Filesystem unit tests do
not by themselves prove the Telegram confirmation path delays database writes.

## Two scheduling contracts

- UI state/confirmation/editing: `app/telegram/handlers/schedule.py`.
- Issue records: `ScheduleRepository` and `ScheduledJob` (SQLite).
- Local prompt records: `LocalPromptScheduleStore` and `LocalPromptSchedule`
  (CSV). Fields include project ID/name, prompt, UTC schedule, chat ID, agent,
  model, reasoning effort, status, timestamps and last error. Use CSV quoting for
  multiline prompts; do not split rows manually.
- Local CSVs are stored next to selected projects. `discover` includes the
  configured/default CSV plus saved-project stores, deduplicated by resolved
  path. It does not mean every CSV anywhere on disk is scanned.
- Naive user time is interpreted using the host device timezone in the handler;
  stored due times are timezone-aware UTC. Display returns to device local time.
- SQLite Issue states are `PENDING`, `LAUNCHED`, `CANCELLED`; CSV also has
  completion/failure states. Never assume their schemas/statuses are identical.

Relevant tests: `test_schedule_time.py`, `test_local_prompt_schedules.py`,
`test_database.py`, `test_lifecycle_notifications.py`.

## External services and personal mode

- `app/github/authentication.py` and `client.py` own auth/client access;
  `issues.py`, `pull_requests.py`, `assets.py`, `issue_polisher.py` provide
  services. GitHub calls are synchronous and must not block async handlers.
- `app/runner/personal_guard.py` owns the local one-way personal-mode switch;
  `file_transfer.py` owns allowed-directory and sensitive-file checks;
  `personal_task.py` is a separate personal execution path. Review its agent
  ownership instead of assuming it shares `JobExecutor`'s slot automatically.
- `app/utils/secrets.py`, `logging.py`, and `security.py` own scanning,
  masking, and Telegram-output sanitization. Output sanitization is not the
  same as Markdown escaping or filesystem authorization.
- Resource information and cache utilities are in `app/utils/sysmonitor.py`
  and `cache.py`.

Relevant tests: `test_personal_mode.py`, `test_secrets.py`,
`test_issue_assets.py`, `test_issue_polisher.py`, `test_pull_requests.py`,
`test_antigravity_account_manager.py`, `test_repository_search.py`.
