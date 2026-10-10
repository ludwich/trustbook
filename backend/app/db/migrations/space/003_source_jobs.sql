-- space.db: each source remembers its latest ingest job (progress and cancellation).

ALTER TABLE sources ADD COLUMN job_id TEXT;
