-- Migration 005: Add aspect-based sentiment support
-- Enables querying sentiment by aspect (pricing, features, support, etc.)
-- in the messages metadata JSONB field.

-- The aspect_scores are stored inside metadata->'aspect_scores' as a JSON object:
--   {"pricing": 0.1, "features": 0.8, "support": 0.4, ...}
-- where each value is a sentiment score 0.0–1.0 for that aspect.

COMMENT ON COLUMN messages.metadata IS
    'Stores sentiment metadata including emotion, urgency_score, and aspect_scores. '
    'Updated by agent/sentiment_analyzer.py on each inbound message.';
