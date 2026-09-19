"""
SQLite DDL for all application tables.
"""

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
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
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

ALL_DDL = [JOBS_TABLE, SESSIONS_TABLE, CREATE_JOBS_INDEX]
