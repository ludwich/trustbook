-- space.db: each chunk's parent (D-077). parent_key names the chunk's document section, class or
-- module ("{file}::Class", "{file}::", "{file}#L<line>", "{file}#o<offset>", "#o<offset>");
-- structure is a small JSON record about that parent (app/chunking/structure.py). Both are
-- file-relative and never citable. NULL for rows chunked before chunker p3-treesitter-6,
-- transcripts, heading-less text and fallback-chunked code.

ALTER TABLE chunks ADD COLUMN parent_key TEXT;
ALTER TABLE chunks ADD COLUMN structure TEXT;
CREATE INDEX chunks_parent ON chunks (source_id, parent_key);
