"""
SQLite DDL for all application tables.
"""

from app.database.schedule_models import CREATE_SCHEDULED_INDEX, SCHEDULED_JOBS_TABLE

JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    repo_full_name  TEXT NOT NULL,
    issue_number    INTEGER NOT NULL,
    branch          TEXT NOT NULL,
    agent           TEXT NOT NULL DEFAULT 'opencode',
    status          TEXT NOT NULL DEFAULT 'QUEUED',
    started_at      TEXT,
    finished_at     TEXT,
    current_phase   TEXT,
    fix_iterations  INTEGER NOT NULL DEFAULT 0,
    files_changed   INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    pr_url          TEXT,
    pr_number       INTEGER,
    telegram_chat_id INTEGER NOT NULL,
    workspace_path  TEXT,
    local_workspace INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

# Structured event log for live progress / /logs (feature: Better /logs + Live Progress).
JOB_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS job_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    level       TEXT NOT NULL DEFAULT 'INFO',
    phase       TEXT,
    message     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

# Persistent per-job runtime state + resume bundle (feature: Persistent Job State + Crash Recovery).
JOB_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS job_state (
    job_id      TEXT PRIMARY KEY,
    stage       TEXT,
    metadata    TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

# Controller-enforced verification check results (feature: Verification Pipeline).
VERIFICATION_RESULTS_TABLE = """
CREATE TABLE IF NOT EXISTS verification_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    check_name  TEXT NOT NULL,
    command_id  INTEGER,
    status      TEXT NOT NULL,
    required    INTEGER NOT NULL DEFAULT 1,
    started_at  TEXT,
    finished_at TEXT,
    duration_ms REAL,
    exit_code   INTEGER,
    output      TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

# Agent run attempts persisted for fallback audit trail (feature: Desktop-Agent Fallback Architecture).
AGENT_ATTEMPTS_TABLE = """
CREATE TABLE IF NOT EXISTS agent_attempts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL,
    attempt_number  INTEGER NOT NULL,
    agent_name      TEXT NOT NULL,
    transport       TEXT,
    status          TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

# Human-in-the-loop approval records (feature: Human-in-the-Loop Approval Policies).
APPROVALS_TABLE = """
CREATE TABLE IF NOT EXISTS approvals (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id         TEXT NOT NULL,
    stage          TEXT NOT NULL,
    risk           TEXT,
    status         TEXT NOT NULL,
    diff_fingerprint TEXT,
    requested_at   TEXT NOT NULL,
    decided_at     TEXT,
    decision_by    INTEGER,
    note           TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS sessions (
    user_id         INTEGER PRIMARY KEY,
    selected_repo   TEXT,
    selected_issue  INTEGER,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

CREATE_JOBS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_jobs_status
    ON jobs (status, created_at);
"""

CREATE_JOB_EVENTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_job_events_job_id
    ON job_events (job_id, id);
"""

CREATE_VERIFICATION_INDEX = """
CREATE INDEX IF NOT EXISTS idx_verification_job_id
    ON verification_results (job_id, id);
"""

CREATE_ATTEMPTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_agent_attempts_job_id
    ON agent_attempts (job_id, id);
"""

CREATE_APPROVALS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_approvals_job_id
    ON approvals (job_id, id);
"""

# Runtime schema migrations for existing databases created before these
# columns/tables existed. Applied idempotently after ALL_DDL on every startup.
MIGRATIONS = [
    ("jobs", "workspace_path", "ALTER TABLE jobs ADD COLUMN workspace_path TEXT"),
    ("jobs", "local_workspace", "ALTER TABLE jobs ADD COLUMN local_workspace INTEGER NOT NULL DEFAULT 0"),
]

ALL_DDL = [
    JOBS_TABLE,
    SESSIONS_TABLE,
    CREATE_JOBS_INDEX,
    SCHEDULED_JOBS_TABLE,
    CREATE_SCHEDULED_INDEX,
    JOB_EVENTS_TABLE,
    JOB_STATE_TABLE,
    VERIFICATION_RESULTS_TABLE,
    AGENT_ATTEMPTS_TABLE,
    APPROVALS_TABLE,
    CREATE_JOB_EVENTS_INDEX,
    CREATE_VERIFICATION_INDEX,
    CREATE_ATTEMPTS_INDEX,
    CREATE_APPROVALS_INDEX,
]
