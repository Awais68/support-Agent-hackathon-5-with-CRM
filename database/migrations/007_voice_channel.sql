-- Migration 007: Add 'voice' to channel CHECK constraints
-- The voice channel (voice messages + phone calls) reuses the existing
-- ticket/message tables, so we only relax the channel constraint.

ALTER TABLE tickets
    DROP CONSTRAINT IF EXISTS tickets_channel_check;
ALTER TABLE tickets
ADD CONSTRAINT tickets_channel_check
        CHECK (channel IN ('email', 'whatsapp', 'webform', 'voice', 'api'));

ALTER TABLE messages
    DROP CONSTRAINT IF EXISTS messages_channel_check;
ALTER TABLE messages
ADD CONSTRAINT messages_channel_check
        CHECK (channel IN ('email', 'whatsapp', 'webform', 'voice', 'api'));