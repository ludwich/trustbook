-- space.db: the full SPEC §5 schema. Later phases only add migrations.

CREATE TABLE sources (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('upload', 'folder', 'url', 'youtube', 'search', 'paste')),
    title TEXT NOT NULL,
    origin TEXT,
    snapshot_at TEXT,
    commit_hash TEXT,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'reading', 'indexing', 'ready', 'failed')),
    failure_reason TEXT,
    failure_next_step TEXT,
    quality_warning TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    "group" TEXT NOT NULL CHECK ("group" IN ('documents', 'transcripts', 'web', 'code')),
    stats TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE chunks (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    text TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    locator TEXT NOT NULL DEFAULT '{}',
    token_count INTEGER NOT NULL,
    UNIQUE (source_id, ordinal)
);
CREATE INDEX ix_chunks_source_id ON chunks (source_id);

CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL DEFAULT '{}',
    badge TEXT CHECK (badge IS NULL OR badge IN ('verified', 'partially_verified', 'refused', 'unverified')),
    removed_sentences TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX ix_messages_conversation_id ON messages (conversation_id);

-- chunk_id has no foreign key: answers keep their citations after a source is refreshed.
CREATE TABLE citations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL REFERENCES messages (id) ON DELETE CASCADE,
    sentence_index INTEGER NOT NULL,
    chunk_id TEXT NOT NULL,
    verifier_score REAL
);
CREATE INDEX ix_citations_message_id ON citations (message_id);
CREATE INDEX ix_citations_chunk_id ON citations (chunk_id);

CREATE TABLE gate_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gate TEXT NOT NULL,
    input TEXT NOT NULL,
    decision TEXT NOT NULL,
    confidence REAL,
    user_correction TEXT,
    created TEXT NOT NULL
);

CREATE TABLE ideation_sessions (
    id TEXT PRIMARY KEY,
    idea TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'finished')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE ideation_nodes (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES ideation_sessions (id) ON DELETE CASCADE,
    parent_id TEXT REFERENCES ideation_nodes (id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'tentative'
        CHECK (status IN ('tentative', 'open', 'resolved', 'dismissed')),
    question TEXT,
    recommended_answer TEXT,
    final_answer TEXT,
    answer_source TEXT CHECK (answer_source IS NULL OR answer_source IN ('user', 'accepted', 'skipped')),
    citations TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX ix_ideation_nodes_session_id ON ideation_nodes (session_id);
