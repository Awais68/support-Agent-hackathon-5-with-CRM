# Load Test Script

Simulates the grading scenario for TechFlow CRM Digital FTE.

## Requirements

```bash
pip install aiohttp structlog
```

## Usage

```bash
# Quick smoke test (10 requests)
python scripts/load_test.py --base-url http://localhost:8000 --requests 10 --concurrency 5

# Full grading simulation (200+ requests)
python scripts/load_test.py --base-url http://localhost:8000 --requests 200 --concurrency 10

# Heavy load test
python scripts/load_test.py --base-url http://localhost:8000 --requests 500 --concurrency 25
```

## Channel Distribution

- Web form: 50% (100+ at 200 total)
- Gmail: 25% (50+ at 200 total)
- WhatsApp: 25% (50+ at 200 total)

## Success Criteria (per spec)

- 99.9% uptime during test
- P95 latency < 3s
- < 25% escalation rate
- Pricing/refund/legal messages must escalate
- No internal system details revealed
