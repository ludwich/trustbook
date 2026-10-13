-- space.db: ideation (SPEC §10, D-093 to D-101). Columns only, nullable or with a default.
-- ideation_nodes.origin: 'seed' (one of the first 3-4 branches) or 'answer' (opened by an answer).
-- ideation_nodes.recommendation: JSON {"grounding","badge","sentences":[{text,citations,kind}],
-- "removed":[str]}; recommended_answer keeps its plain text, citations the flat
-- [{chunk_id, verifier_score}] list. diagnostics: JSON (timings, scores; never shown).
-- ideation_sessions.risks: JSON [{"text","node_id"}]; brief_at: when brief.md was last written.

ALTER TABLE ideation_nodes ADD COLUMN origin TEXT NOT NULL DEFAULT 'answer'
    CHECK (origin IN ('seed', 'answer'));
ALTER TABLE ideation_nodes ADD COLUMN recommendation TEXT;
ALTER TABLE ideation_nodes ADD COLUMN asked_at TEXT;
ALTER TABLE ideation_nodes ADD COLUMN answered_at TEXT;
ALTER TABLE ideation_nodes ADD COLUMN diagnostics TEXT;
ALTER TABLE ideation_sessions ADD COLUMN risks TEXT NOT NULL DEFAULT '[]';
ALTER TABLE ideation_sessions ADD COLUMN brief_at TEXT;
