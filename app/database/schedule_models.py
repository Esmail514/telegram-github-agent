"""
SQLite DDL for the scheduled_jobs table.
"""

SCHEDULED_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_full_name  TEXT NOT NULL,
    issue_number    INTEGER NOT NULL,
    agent           TEXT NOT NULL DEFAULT 'antigravity',
    scheduled_at    TEXT NOT NULL,
    chat_id         INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'PENDING',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

CREATE_SCHEDULED_INDEX = """
CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_status_time
    ON scheduled_jobs (status, scheduled_at);
"""
