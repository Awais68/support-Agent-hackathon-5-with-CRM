-- CRM Digital FTE Factory Database Schema
-- TechFlow Analytics Support System

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;

-- Customers table
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    company VARCHAR(255),
    tier VARCHAR(50) NOT NULL CHECK (tier IN ('starter', 'growth', 'enterprise')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX idx_customers_email_tier ON customers(email, tier);

-- Tickets table
CREATE TABLE tickets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_number VARCHAR(20) NOT NULL UNIQUE,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    subject VARCHAR(500) NOT NULL,
    category VARCHAR(50),
    priority VARCHAR(20) CHECK (priority IN ('low', 'medium', 'high', 'critical')),
    status VARCHAR(50) DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'resolved', 'escalated', 'closed')),
    channel VARCHAR(50) NOT NULL CHECK (channel IN ('email', 'whatsapp', 'webform', 'voice', 'api')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    assigned_to VARCHAR(255)
);
CREATE INDEX idx_tickets_customer_status ON tickets(customer_id, status);
CREATE INDEX idx_tickets_ticket_number ON tickets(ticket_number);
CREATE INDEX idx_tickets_created_at ON tickets(created_at DESC);

-- Messages table
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_id UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    direction VARCHAR(20) NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    content TEXT NOT NULL,
    channel VARCHAR(50) NOT NULL CHECK (channel IN ('email', 'whatsapp', 'webform', 'voice', 'api')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'::jsonb,
    sentiment_score FLOAT
);
CREATE INDEX idx_messages_ticket_created ON messages(ticket_id, created_at DESC);
CREATE INDEX idx_messages_customer_created ON messages(customer_id, created_at DESC);

-- Knowledge base table with pgvector
CREATE TABLE knowledge_base (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title VARCHAR(500) NOT NULL,
    content TEXT NOT NULL,
    category VARCHAR(100),
    tags TEXT[] DEFAULT ARRAY[]::TEXT[],
    embedding vector(1536),
    source VARCHAR(255),
    tier VARCHAR(50) DEFAULT 'all' CHECK (tier IN ('all', 'starter', 'growth', 'enterprise')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_knowledge_base_category ON knowledge_base(category);
CREATE INDEX idx_knowledge_base_tier ON knowledge_base(tier);

-- HNSW index for vector similarity search
CREATE INDEX idx_knowledge_base_embedding
    ON knowledge_base USING hnsw (embedding vector_cosine_ops);

-- Agent runs table
CREATE TABLE agent_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticket_id UUID NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    agent_name VARCHAR(255) DEFAULT 'customer-success-agent',
    input_message TEXT NOT NULL,
    output_message TEXT,
    model VARCHAR(50) DEFAULT 'gpt-4o',
    tokens_used INT,
    tool_calls TEXT[] DEFAULT ARRAY[]::TEXT[],
    status VARCHAR(50) DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    result JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    duration_ms INT
);
CREATE INDEX idx_agent_runs_ticket_created ON agent_runs(ticket_id, created_at DESC);
CREATE INDEX idx_agent_runs_customer_created ON agent_runs(customer_id, created_at DESC);

-- Metrics table for analytics
CREATE TABLE metrics (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metric_name VARCHAR(100) NOT NULL,
    metric_value FLOAT NOT NULL,
    metric_type VARCHAR(50) DEFAULT 'gauge',
    labels JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX idx_metrics_name_timestamp ON metrics(metric_name, timestamp DESC);
CREATE INDEX idx_metrics_timestamp ON metrics(timestamp DESC);

-- Customer identifiers table (multi-identifier resolution)
CREATE TABLE customer_identifiers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    identifier_type VARCHAR(50) NOT NULL CHECK (identifier_type IN ('email', 'phone', 'web_session')),
    identifier_value VARCHAR(500) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (identifier_type, identifier_value)
);

CREATE INDEX idx_customer_identifiers_lookup ON customer_identifiers(identifier_type, identifier_value);
CREATE INDEX idx_customer_identifiers_customer ON customer_identifiers(customer_id);

-- Create indexes for common queries
CREATE INDEX idx_tickets_status_created ON tickets(status, created_at DESC);
CREATE INDEX idx_messages_ticket_customer ON messages(ticket_id, customer_id);
CREATE INDEX idx_agent_runs_status ON agent_runs(status, created_at DESC);

-- Grant appropriate permissions (adjust as needed)
-- GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO application_user;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO application_user;
