"""Prometheus metrics for system monitoring."""

from prometheus_client import Counter, Histogram, Gauge
import time

# Counter metrics
tickets_created = Counter(
    "tickets_created_total",
    "Total tickets created",
    ["channel", "priority"],
)

messages_processed = Counter(
    "messages_processed_total",
    "Total messages processed",
    ["channel", "status"],
)

agent_tool_calls = Counter(
    "agent_tool_calls_total",
    "Total agent tool calls",
    ["tool_name", "status"],
)

tickets_escalated = Counter(
    "tickets_escalated_total",
    "Total tickets escalated to human",
    ["reason", "priority"],
)

kb_searches = Counter(
    "kb_searches_total",
    "Total knowledge base searches",
    ["category", "results_count"],
)

# Histogram metrics (latency/duration)
message_processing_time = Histogram(
    "message_processing_seconds",
    "Message processing duration",
    ["channel"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0),
)

agent_response_time = Histogram(
    "agent_response_seconds",
    "Agent response generation time",
    buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

kb_search_time = Histogram(
    "kb_search_seconds",
    "Knowledge base search duration",
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0),
)

ticket_resolution_time = Histogram(
    "ticket_resolution_hours",
    "Ticket resolution time in hours",
    buckets=(1, 2, 4, 8, 24, 48, 72),
)

# Gauge metrics (current state)
active_tickets = Gauge(
    "active_tickets",
    "Number of active tickets",
    ["status"],
)

kafka_lag = Gauge(
    "kafka_consumer_lag",
    "Kafka consumer lag",
    ["topic"],
)

tokens_used = Counter(
    "tokens_used_total",
    "Total tokens used in API calls",
    ["model", "endpoint"],
)

api_request_duration = Histogram(
    "api_request_seconds",
    "API request duration",
    ["method", "endpoint"],
    buckets=(0.01, 0.1, 0.5, 1.0, 5.0),
)

# Error tracking
errors_total = Counter(
    "errors_total",
    "Total errors",
    ["error_type", "component"],
)

# Business metrics
customer_satisfaction = Gauge(
    "customer_satisfaction_score",
    "Customer satisfaction score",
    ["channel"],
)

agent_success_rate = Gauge(
    "agent_success_rate",
    "Agent success rate (0-1)",
)

escalation_rate = Gauge(
    "escalation_rate",
    "Escalation rate (0-1)",
)

# Sentiment & emotion metrics
sentiment_score = Gauge(
    "sentiment_score",
    "Customer sentiment score (0-1)",
    ["channel", "tier"],
)

sentiment_emotion = Counter(
    "sentiment_emotion_total",
    "Messages by detected emotion",
    ["emotion", "channel"],
)

sentiment_urgency = Gauge(
    "sentiment_urgency_score",
    "Customer message urgency score (0-1)",
    ["channel"],
)

sentiment_below_threshold = Counter(
    "sentiment_below_threshold_total",
    "Messages with sentiment below escalation threshold",
    ["channel"],
)

sentiment_high_urgency = Counter(
    "sentiment_high_urgency_total",
    "Messages with high urgency score",
    ["channel"],
)


class MetricsContext:
    """Context manager for timing operations."""

    def __init__(self, histogram):
        self.histogram = histogram
        self.start_time = None

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, *args):
        if self.start_time:
            duration = time.time() - self.start_time
            self.histogram.observe(duration)
