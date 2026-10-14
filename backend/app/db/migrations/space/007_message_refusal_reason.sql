-- space.db: why an assistant message was refused (D-118, G3-2). NULL for answers, replies and
-- refusals stored before this migration (the UI and the CLI then show the stored text as before).
ALTER TABLE messages ADD COLUMN refusal_reason TEXT
    CHECK (refusal_reason IS NULL OR refusal_reason IN
        ('not_in_sources', 'no_sources', 'invalid_output', 'unsupported'));
