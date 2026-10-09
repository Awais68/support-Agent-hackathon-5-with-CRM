-- Processing ledger for inbound channel messages (AUDIT N4).
--
-- The consumer commits offsets only after a message is handled, so a crash
-- means redelivery. Each delivery claims its envelope message_id here first:
-- 'done' and 'dead' rows are final and the redelivery is skipped; a
-- 'processing' row means an earlier attempt died part-way, and the worker
-- resumes it, reusing the ticket it already created.
CREATE TABLE IF NOT EXISTS inbound_processing (
    message_id VARCHAR(200) PRIMARY KEY,
    topic VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'processing'
        CHECK (status IN ('processing', 'done', 'dead')),
    attempts INTEGER NOT NULL DEFAULT 1,
    ticket_id UUID,
    last_error TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_inbound_processing_status
    ON inbound_processing (status);
