-- Delivery ledger for notifications.outbound.
--
-- Kafka delivers at-least-once, so the notification sender claims each reply
-- here before calling the provider. A row in 'sent', 'failed' or 'skipped'
-- is final; only 'pending' rows (a previous attempt crashed mid-send) may be
-- claimed again.
CREATE TABLE IF NOT EXISTS outbound_deliveries (
    idempotency_key VARCHAR(200) PRIMARY KEY,
    ticket_id UUID,
    channel VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sent', 'failed', 'skipped')),
    attempts INTEGER NOT NULL DEFAULT 0,
    provider_message_id VARCHAR(255),
    last_error TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_outbound_deliveries_ticket
    ON outbound_deliveries (ticket_id);
CREATE INDEX IF NOT EXISTS idx_outbound_deliveries_status
    ON outbound_deliveries (status);
