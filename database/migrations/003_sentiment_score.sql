-- Migration 003: Add sentiment score column to messages
-- Enables queryable customer-sentiment data for dashboards, routing, and analytics.

ALTER TABLE messages ADD COLUMN IF NOT EXISTS sentiment_score FLOAT;

COMMENT ON COLUMN messages.sentiment_score IS
    'Customer sentiment score 0.0 (most negative) to 1.0 (most positive), computed by VADER. NULL when the message pre-dates this column or analysis failed.';
