---
name: change-impact-analyzer
description: >-
  Workflow for assessing the blast radius of a proposed or completed change before
  it ships: which handlers, services, tests, config, database schema, and security
  surfaces are affected, plus concurrency and backward-compatibility risks. Use
  between implementation and final verification, or before merging a PR, to catch
  side effects the diff itself does not reveal.
---

# Change Impact Analyzer Skill

Use this skill once a change exists (in a workspace or as a PR diff) and before
it is finalized. Purpose: identify **side effects the diff does not show** so
fixes land without regressions.

## 1. Build the impact map

For each touched path, trace the references *graph*, not just the diff:

- **Entry points**: which handler in `app/telegram/handlers/*` reaches this
  code, and which callback prefix/keyboard in `app/telegram/keyboards/__init__.py`.
- **Services/singletons**: `issue_service`, `github_client`, `pr_service`,
  `Database`, `JobScheduler` — singletons mean state changes leak across jobs.
- **Config coupling**: any env var consumed (see `app/config/settings.py`);
  `SecretStr` secrets must never be logged or persisted (see `app/utils/logging.py`
  `SecretMaskingFilter`).
- **DB schema**: does the change depend on columns in `app/database/models.py`
  DDL? Old `agent.db` files won't auto-migrate.
- **Tests** that exercise the path (name the `tests/test_*.py` files).

## 2. Check the four risk axes

1. **Concurrency**: the executor enforces a single active job; scheduler marks
   jobs LAUNCHED to prevent double-fire. A change to these must keep both
   invariants (see runbooks in [systematic-debugger](../systematic-debugger/SKILL.md)).
2. **Untrusted input**: does the change follow file bytes, issue text, or tool
   output? Those cross the [trust boundary](../../../AGENTS.md#trust-boundary)
   (route result through [security-reviewer](../security-reviewer/SKILL.md)).
3. **Subprocess/timeout**: new `create_subprocess_exec` call sites must have
   timeout + kill-on-timeout, or jobs hang.
4. **Backward compatibility**: does a public dataclass/field/AGENTS.md contract
   change? Note what breaks.

## 3. Output

Return a concise impact report: affected files with references, the risk-axe
verdict per item, and a recommendation (proceed / adjust / add tests). When
done, hand off to [security-reviewer](../security-reviewer/SKILL.md) then
[final-verifier](../final-verifier/SKILL.md).

## When NOT to use

Not a substitute for debugging (use systematic-debugger) or planning (use
implementation-planner). Use at most once per change, right before final
verification.