-- global.db: job queue and outbound request log.

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    space_id TEXT,
    payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'paused', 'done', 'failed', 'cancelled')),
    progress REAL,
    position INTEGER NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX ix_jobs_status_position ON jobs (status, position);
CREATE INDEX ix_jobs_space_id ON jobs (space_id);

CREATE TABLE request_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    capability TEXT NOT NULL,
    endpoint_host TEXT NOT NULL,
    space_id TEXT,
    space_name TEXT,
    bytes_sent INTEGER NOT NULL DEFAULT 0,
    tokens_sent INTEGER,
    excerpt TEXT NOT NULL DEFAULT ''
);
