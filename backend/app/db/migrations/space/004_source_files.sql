-- space.db: per-file content hashes of linked folders, for incremental refresh (SPEC §7.5).
-- start_offset/end_offset: the file's text in the source's snapshot.md; chunk_ids: JSON list.

CREATE TABLE source_files (
    source_id TEXT NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    kind TEXT NOT NULL,
    chunker TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    chunk_ids TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (source_id, path)
);
