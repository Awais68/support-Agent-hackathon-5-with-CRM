# Multi-stage Dockerfile for TechFlow CRM Digital FTE
FROM python:3.13-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY pyproject.toml requirements.txt ./

# Install Python dependencies with venv + uv
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir uv && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# Runtime stage
FROM python:3.13-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 appuser

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code
COPY . .

# Set environment
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Change ownership to non-root user
RUN chown -R appuser:appuser /app

USER appuser

# Health check
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import asyncio; from api.main import app; print('healthy')" || exit 1

# Default to API mode (binds to $PORT so Render/Heroku-style hosts work)
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
