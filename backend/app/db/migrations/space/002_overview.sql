-- space.db: the space overview (SPEC §9). One row per space.

CREATE TABLE overview (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'none' CHECK (status IN ('none', 'pending', 'ready', 'failed')),
    sentences TEXT NOT NULL DEFAULT '[]',
    questions TEXT NOT NULL DEFAULT '[]',
    generated_at TEXT,
    sources_signature TEXT,
    job_id TEXT,
    error TEXT,
    updated_at TEXT NOT NULL
);

INSERT INTO overview (id, enabled, status, updated_at)
VALUES (1, 1, 'none', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
