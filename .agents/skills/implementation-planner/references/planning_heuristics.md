# Planning heuristics (implementation-planner)

Use these rules of thumb to size and sequence work quickly.

## Question heuristics

Ask yourself before planning:

1. "Which single module owns this behavior?" — preconditions: the behavior is
   reachable from a known entry point (`handlers/*`, scheduler, github services).
2. "Does this change the persisted schema?" — if yes, it touches
   `app/database/models.py` (DDL strings) AND `app/database/repository.py` or
   `schedule_repository.py`. Recreate the DB or migrate explicitly; tests create
   their own temp DB via fixtures.
3. "Does this change what bytes are written to disk?" — if it writes untracked
   files into `workspaces/` or uploads assets, route through
   `app/runner/file_transfer.py` validation or `app/github/assets.py` rules.
4. "Does this spawn or time out a subprocess?" — then respect
   `MAX_AGENT_RUNTIME_MINUTES`, `GIT_OPERATION_TIMEOUT_SECONDS`,
   `GIT_NETWORK_TIMEOUT_SECONDS`, and the kill-on-timeout pattern used in
   `app/agents/*` and `app/runner/git.py`.

## Skip heuristics

- Single-file test → fix: skip the plan, use test-driven-fixer.
- Pure reference-doc update in `.agents/skills/*`: no plan needed; follow
  [skill-maintainer](../skill-maintainer/SKILL.md).
- Change affecting only new untested code: still plan, tests are required.

## Ordering default

1. Keyboards / callback constants (contract).
2. Service / repository logic.
3. Handler wiring.
4. Tests for the changed slice.
5. Full suite (`py -m pytest`) + ruff + mypy at the end.