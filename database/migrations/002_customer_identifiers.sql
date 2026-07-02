-- Migration 002: Multi-Identifier Customer Resolution
-- Adds customer_identifiers table to allow customers to be identified
-- by email, phone (WhatsApp), or web session — all resolving to the same customer record.

-- Create customer_identifiers table
CREATE TABLE IF NOT EXISTS customer_identifiers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    identifier_type VARCHAR(50) NOT NULL CHECK (identifier_type IN ('email', 'phone', 'web_session')),
    identifier_value VARCHAR(500) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (identifier_type, identifier_value)
);

CREATE INDEX idx_customer_identifiers_lookup ON customer_identifiers(identifier_type, identifier_value);
CREATE INDEX idx_customer_identifiers_customer ON customer_identifiers(customer_id);

-- Migrate existing customer emails into customer_identifiers
INSERT INTO customer_identifiers (customer_id, identifier_type, identifier_value)
SELECT id, 'email', email
FROM customers
ON CONFLICT (identifier_type, identifier_value) DO NOTHING;

-- Update schema.sql reference by adding a comment
COMMENT ON TABLE customer_identifiers IS 'Maps multiple identifiers (email, phone, web session) to a single customer record';
COMMENT ON COLUMN customer_identifiers.identifier_type IS 'Type of identifier: email, phone, or web_session';
COMMENT ON COLUMN customer_identifiers.identifier_value IS 'The actual identifier value (e.g. email address, phone number, session token)';
