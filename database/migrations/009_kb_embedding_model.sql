-- Record which model produced each knowledge_base embedding.
--
-- Vectors from different embedding models live in unrelated coordinate spaces.
-- Searching a Gemini-indexed knowledge base with an OpenAI-generated query
-- vector returns rows in essentially random order at ~0.04 similarity instead
-- of ~0.63, and nothing in the response marks the result as untrustworthy.
-- Tagging each row lets the search path restrict itself to vectors it can
-- actually compare against, and degrade to lexical search otherwise.

ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS embedding_model TEXT;

CREATE INDEX IF NOT EXISTS idx_knowledge_base_embedding_model
    ON knowledge_base (embedding_model)
    WHERE embedding IS NOT NULL;
