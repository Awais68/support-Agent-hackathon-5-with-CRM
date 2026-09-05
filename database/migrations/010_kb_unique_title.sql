-- Make the knowledge_base seed actually idempotent.
--
-- seed.py ends its article INSERT with ON CONFLICT DO NOTHING, but no unique
-- constraint existed for it to conflict against, so the clause never fired:
-- every seed run appended another full copy of the article set. Beyond wasting
-- embedding quota, duplicates crowd out distinct articles in top-N search
-- results — a five-result query was returning the same two articles twice.

-- Collapse existing duplicates, keeping the oldest row per title.
DELETE FROM knowledge_base kb
WHERE kb.id NOT IN (
    SELECT DISTINCT ON (title) id
    FROM knowledge_base
    ORDER BY title, created_at ASC, id ASC
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_base_title_unique
    ON knowledge_base (title);
