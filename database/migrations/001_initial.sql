-- Migration 001: Initial schema with seed data
-- This migration sets up the complete database schema and populates seed data

-- Import schema (in practice, this would be separate or \i schema.sql)
-- For now, assuming schema.sql has been executed

-- Seed: Sample customers
INSERT INTO customers (email, name, company, tier, metadata) VALUES
    ('alice@acmecorp.com', 'Alice Johnson', 'Acme Corp', 'enterprise', '{"account_manager":"bob@techflow.com"}'),
    ('bob@startupinc.com', 'Bob Smith', 'Startup Inc', 'growth', '{}'),
    ('carol@smallbiz.com', 'Carol Davis', 'Small Biz LLC', 'starter', '{}'),
    ('david@enterprise.io', 'David Wilson', 'Enterprise Inc', 'enterprise', '{"account_manager":"alice@techflow.com"}'),
    ('eva@mediumco.com', 'Eva Martinez', 'Medium Co', 'growth', '{}'),
    ('frank@bootstrap.com', 'Frank Chen', 'Bootstrap Labs', 'starter', '{}'),
    ('grace@bigtech.com', 'Grace Lee', 'BigTech Solutions', 'enterprise', '{"account_manager":"charlie@techflow.com"}'),
    ('henry@devshop.com', 'Henry Brown', 'Dev Shop', 'growth', '{}'),
    ('iris@analytics.io', 'Iris Patel', 'Analytics Pro', 'enterprise', '{}'),
    ('jack@cloudnative.com', 'Jack Taylor', 'CloudNative Corp', 'growth', '{}')
ON CONFLICT (email) DO NOTHING;

-- Seed: Sample tickets
INSERT INTO tickets (ticket_number, customer_id, subject, category, priority, status, channel, assigned_to)
SELECT
    'TKT-' || to_char(CURRENT_DATE, 'YYYYMMDD') || '-' || LPAD(ROW_NUMBER() OVER (ORDER BY customers.email)::TEXT, 4, '0'),
    customers.id,
    CASE (ROW_NUMBER() OVER (ORDER BY customers.email) % 5)
        WHEN 1 THEN 'How to set up data connectors?'
        WHEN 2 THEN 'Anomaly detection not working'
        WHEN 3 THEN 'Billing question about enterprise plan'
        WHEN 4 THEN 'API authentication error'
        ELSE 'Dashboard performance issues'
    END,
    CASE (ROW_NUMBER() OVER (ORDER BY customers.email) % 4)
        WHEN 1 THEN 'technical'
        WHEN 2 THEN 'billing'
        WHEN 3 THEN 'onboarding'
        ELSE 'general'
    END,
    CASE (ROW_NUMBER() OVER (ORDER BY customers.email) % 4)
        WHEN 1 THEN 'high'
        WHEN 2 THEN 'medium'
        WHEN 3 THEN 'low'
        ELSE 'critical'
    END,
    CASE (ROW_NUMBER() OVER (ORDER BY customers.email) % 5)
        WHEN 1 THEN 'open'
        WHEN 2 THEN 'in_progress'
        WHEN 3 THEN 'resolved'
        WHEN 4 THEN 'escalated'
        ELSE 'closed'
    END,
    CASE (ROW_NUMBER() OVER (ORDER BY customers.email) % 3)
        WHEN 1 THEN 'email'
        WHEN 2 THEN 'whatsapp'
        ELSE 'webform'
    END,
    'support-agent-' || (ROW_NUMBER() OVER (ORDER BY customers.email) % 3 + 1)::TEXT
FROM customers
LIMIT 50;

-- Seed: Sample knowledge base articles
INSERT INTO knowledge_base (title, content, category, tags, source, tier, embedding) VALUES
    ('Getting Started with TechFlow', 'TechFlow Analytics is a modern data platform. Start by connecting your first data source...', 'onboarding', ARRAY['setup', 'basics'], 'internal-wiki', 'all', NULL),
    ('Account Setup and Billing', 'Your account includes storage, API calls, and support. Billing is monthly. You can upgrade/downgrade anytime...', 'billing', ARRAY['billing', 'account'], 'internal-wiki', 'all', NULL),
    ('Data Connector Configuration', 'Data connectors allow TechFlow to read from your databases, APIs, and warehouses. Setup takes 5 minutes...', 'technical', ARRAY['connectors', 'setup'], 'internal-wiki', 'all', NULL),
    ('Anomaly Detection Setup', 'Anomaly detection uses AI to identify unusual patterns. Configure thresholds and notification rules...', 'technical', ARRAY['ai', 'detection'], 'internal-wiki', 'growth', NULL),
    ('API Documentation', 'TechFlow API v2 supports REST and GraphQL. Authentication uses API keys. Rate limit: 1000 req/min...', 'technical', ARRAY['api', 'reference'], 'api-docs', 'all', NULL),
    ('Enterprise Features', 'Enterprise customers get SSO, advanced RBAC, dedicated support, and custom integrations...', 'billing', ARRAY['enterprise', 'features'], 'internal-wiki', 'enterprise', NULL),
    ('Troubleshooting Connection Issues', 'If your connector fails, check firewall rules, credentials, and network connectivity...', 'technical', ARRAY['troubleshooting', 'connectors'], 'kb-article', 'all', NULL),
    ('Performance Optimization', 'Query optimization, indexing strategies, and caching can improve dashboard load times by 10x...', 'technical', ARRAY['performance', 'optimization'], 'kb-article', 'growth', NULL),
    ('Dashboard Customization', 'Create custom dashboards by dragging widgets, changing colors, and adding filters...', 'product', ARRAY['dashboards', 'ui'], 'internal-wiki', 'all', NULL),
    ('Exporting Data', 'Export data to CSV, Parquet, or directly to cloud storage via SFTP/S3 integration...', 'product', ARRAY['export', 'integration'], 'internal-wiki', 'all', NULL);

-- Note: embeddings will be populated by the application via OpenAI API
-- This seed data is minimal to allow initial testing without embeddings

-- Seed: Sample agent runs
-- These will be created as tickets are processed, so we just create a few for testing
INSERT INTO agent_runs (ticket_id, customer_id, agent_name, input_message, status, result)
SELECT
    tickets.id,
    tickets.customer_id,
    'customer-success-agent',
    'Customer message about ' || tickets.subject,
    'completed',
    jsonb_build_object(
        'response_type', 'informational',
        'escalated', false,
        'kb_used', true
    )
FROM tickets
WHERE status IN ('open', 'in_progress')
LIMIT 5;

-- Create some initial metrics
INSERT INTO metrics (metric_name, metric_value, metric_type, labels)
SELECT
    'tickets_created_today',
    50.0,
    'gauge',
    jsonb_build_object('channel', 'email');

INSERT INTO metrics (metric_name, metric_value, metric_type, labels)
SELECT
    'avg_resolution_time_hours',
    8.5,
    'gauge',
    jsonb_build_object('tier', 'enterprise');

INSERT INTO metrics (metric_name, metric_value, metric_type, labels)
SELECT
    'agent_escalation_rate',
    0.15,
    'gauge',
    jsonb_build_object('period', '24h');
