-- Migration 004: Multi-channel identifier seed data
-- Adds identifiers linking the same customer across email + phone + web_session
-- for testing identity resolution.

-- Link existing customer alice@acmecorp.com with a WhatsApp phone number
INSERT INTO customer_identifiers (customer_id, identifier_type, identifier_value)
SELECT id, 'phone', '+14155551234'
FROM customers
WHERE email = 'alice@acmecorp.com'
ON CONFLICT (identifier_type, identifier_value) DO NOTHING;

-- Link same customer with a web session token
INSERT INTO customer_identifiers (customer_id, identifier_type, identifier_value)
SELECT id, 'web_session', 'session-abc-123-alice'
FROM customers
WHERE email = 'alice@acmecorp.com'
ON CONFLICT (identifier_type, identifier_value) DO NOTHING;

-- Link bob@startupinc.com with a phone number
INSERT INTO customer_identifiers (customer_id, identifier_type, identifier_value)
SELECT id, 'phone', '+14155559876'
FROM customers
WHERE email = 'bob@startupinc.com'
ON CONFLICT (identifier_type, identifier_value) DO NOTHING;

-- Link bob@startupinc.com with a web session
INSERT INTO customer_identifiers (customer_id, identifier_type, identifier_value)
SELECT id, 'web_session', 'session-xyz-789-bob'
FROM customers
WHERE email = 'bob@startupinc.com'
ON CONFLICT (identifier_type, identifier_value) DO NOTHING;

-- Link carol@smallbiz.com with a phone number
INSERT INTO customer_identifiers (customer_id, identifier_type, identifier_value)
SELECT id, 'phone', '+14155550987'
FROM customers
WHERE email = 'carol@smallbiz.com'
ON CONFLICT (identifier_type, identifier_value) DO NOTHING;

-- Create a ticket via WhatsApp for Alice to simulate cross-channel history
INSERT INTO tickets (ticket_number, customer_id, subject, category, priority, status, channel, assigned_to)
SELECT
    'TKT-' || to_char(CURRENT_DATE, 'YYYYMMDD') || '-XC01',
    c.id,
    'WhatsApp: Order status inquiry (cross-channel)',
    'general',
    'medium',
    'open',
    'whatsapp',
    'support-agent-1'
FROM customers c
WHERE c.email = 'alice@acmecorp.com'
AND NOT EXISTS (
    SELECT 1 FROM tickets t
    WHERE t.customer_id = c.id AND t.subject LIKE '%WhatsApp: Order status%'
);

-- Add a message to the WhatsApp ticket
INSERT INTO messages (ticket_id, customer_id, direction, content, channel)
SELECT
    t.id,
    t.customer_id,
    'inbound',
    'Hi, can you check the status of my order? I sent an email earlier but also following up here.',
    'whatsapp'
FROM tickets t
WHERE t.subject = 'WhatsApp: Order status inquiry (cross-channel)'
AND NOT EXISTS (
    SELECT 1 FROM messages m
    WHERE m.ticket_id = t.id AND m.content LIKE '%can you check the status of my order%'
);

-- Report on identifiers created (visible in migration logs)
DO $$
DECLARE
    identifier_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO identifier_count FROM customer_identifiers;
    RAISE NOTICE 'Migration 004 complete: % customer_identifiers in system', identifier_count;
END $$;
