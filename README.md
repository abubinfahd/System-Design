# 🔗 Mini URL Shortener with Analytics

A **production-grade URL shortener** built incrementally across six milestones — from a simple CRUD prototype to a horizontally scalable, cache-backed service with click analytics.

## Architecture Overview

```text
                  ┌──────────────────────────────────────────────────────────┐
                  │                    Docker Compose                        │
                  │                                                          │
  Client ──────► │  ┌───────────┐    ┌───────────┐    ┌──────────────────┐  │
  HTTP req       │  │  Gunicorn  │    │   Redis    │    │   PostgreSQL     │  │
                  │  │  4 workers │◄──►│  (cache +  │    │   (persistent    │  │
                  │  │  Uvicorn   │    │  rate limit│    │    storage)      │  │
                  │  └─────┬─────┘    │  + locks)  │    └────────┬─────────┘  │
                  │        │          └───────────┘              │            │
                  │        │                                     │            │
                  │        └─────────────────────────────────────┘            │
                  │           Background aggregation job (60s)               │
                  └──────────────────────────────────────────────────────────┘
```

## Milestone Progression

| Milestone | Focus | Key Additions |
|-----------|-------|---------------|
| **M1** | Core CRUD | URL creation, redirect, click tracking |
| **M2** | Persistence | PostgreSQL integration, SQLAlchemy ORM |
| **M3** | Production API | Versioned endpoints, Base62 encoding, custom aliases, expiration, idempotency keys, rate limiting, structured errors |
| **M4** | Write-Optimized DB | Separate `click_events` table, background aggregation job, pre-computed `click_count` |
| **M5** | Redis Caching | L1 in-process cache → L2 Redis cache-aside, stampede protection, distributed rate limiting, graceful degradation |
| **M6** | Horizontal Scaling | Multi-stage Dockerfile, Gunicorn + Uvicorn workers, stateless design, health checks, `docker-compose --scale` |

---

## Features

- **Deterministic Short Codes** — Auto-incrementing DB ID encoded as Base62 (no collision checking)
- **Custom Aliases** — User-supplied short codes matching `[a-zA-Z0-9_-]`
- **URL Expiration** — Optional `expires_at` timestamp; expired URLs return `410 Gone`
- **Idempotency Keys** — `Idempotency-Key` header prevents duplicate creates on retries
- **Rate Limiting** — Redis-based sliding window (10 req/min per IP), with in-memory fallback
- **Multi-Layer Caching** — L1 in-process dict (60s TTL) → L2 Redis (24h TTL) → PostgreSQL
- **Cache Stampede Protection** — Distributed Redis lock prevents thundering herd on cold keys
- **Background Click Aggregation** — Async job batches `click_events` into `urls.click_count` every 60s
- **Structured Error Responses** — Unified `{ "error": { "code": "...", "message": "..." } }` format
- **Health Check Endpoint** — `GET /health` reports DB + Redis status for load balancer probes
- **Graceful Degradation** — Cache and rate limiting continue to function when Redis is down
- **Input Validation** — Dangerous URL schemes (`javascript:`, `data:`, `file:`) blocked at the schema layer
- **Suspicious Domain Logging** — Warns on known URL shortener re-shortening (e.g. `bit.ly`)

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Framework** | FastAPI (Python 3.11) |
| **Database** | PostgreSQL 15 |
| **Cache / Rate Limiter** | Redis 7 |
| **ORM** | SQLAlchemy 2 |
| **Validation** | Pydantic v2 |
| **WSGI Server** | Gunicorn + Uvicorn workers |
| **Containerization** | Docker (multi-stage) + Docker Compose |

---

## Project Structure

```text
url_shortner/
├── app/
│   ├── main.py                 # Entry point — lifespan, Redis init, aggregation job
│   ├── api/
│   │   ├── routes.py           # Versioned endpoints + legacy aliases
│   │   └── health.py           # GET /health — DB + Redis status
│   ├── core/
│   │   ├── config.py           # Pydantic settings (DATABASE_URL, REDIS_URL, BASE_URL)
│   │   ├── errors.py           # APIException + global exception handlers
│   │   ├── rate_limiter.py     # Redis sliding window + in-memory fallback
│   │   └── cache.py            # L1/L2 cache-aside, stampede lock, cache invalidation
│   ├── db/
│   │   ├── database.py         # SQLAlchemy engine + session factory
│   │   └── models.py           # Tables: urls, click_events, idempotency_keys
│   ├── schemas/
│   │   └── schemas.py          # Pydantic request/response models + URL validation
│   └── services/
│       ├── url_service.py      # Core business logic (create, redirect, analytics)
│       └── aggregation.py      # Background click aggregation (60s loop)
├── Dockerfile                  # Multi-stage build (builder → slim production)
├── docker-compose.yml          # web + postgres + redis orchestration
├── requirements.txt            # Python dependencies
├── load_test.py                # Concurrent redirect load tester
├── view_db.py                  # DB inspection utility
├── .env.example                # Environment variable template
└── LICENSE                     # License file
```

---

## How to Run

### Option 1 — Docker Compose (Recommended)

```bash
docker-compose up --build
```

This starts **PostgreSQL**, **Redis**, and the **API** (4 Gunicorn workers). The service is available at `http://localhost:8000`.

To scale horizontally:

```bash
docker-compose up --build --scale web=4
```

### Option 2 — Local Development

```bash
# 1. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS / Linux

# 2. Configure environment
copy .env.example .env
# Edit .env with your DATABASE_URL, REDIS_URL, and BASE_URL

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start development server
uvicorn app.main:app --reload
```

---

## Interactive API Documentation

FastAPI auto-generates Swagger UI at:

👉 **[http://localhost:8000/docs](http://localhost:8000/docs)**

---

## API Reference

### 1. Create Short URL

| | |
|---|---|
| **Method** | `POST` |
| **Path** | `/v1/urls` (legacy: `/shorten`) |
| **Rate Limit** | 10 req/min per IP |

**Headers:**

| Header | Required | Description |
|--------|----------|-------------|
| `Idempotency-Key` | No | UUID to prevent duplicate creates on retries |

**Request Body:**

```json
{
  "long_url": "https://www.google.com",
  "custom_alias": "my-google",
  "expires_at": "2026-12-31T23:59:59Z"
}
```

> `custom_alias` and `expires_at` are optional.

**Response (201 Created):**

```json
{
  "short_code": "my-google",
  "short_url": "http://localhost:8000/my-google",
  "created_at": "2026-06-19T11:06:21.257330Z"
}
```

### 2. Redirect (Hot Path)

| | |
|---|---|
| **Method** | `GET` |
| **Path** | `/{short_code}` |

Performs **302 Found** redirect to the original URL and records a click event.

| Status | Meaning |
|--------|---------|
| `302` | Redirect to original URL |
| `404` | Short code not found |
| `410` | URL has expired |

### 3. View Analytics

| | |
|---|---|
| **Method** | `GET` |
| **Path** | `/v1/urls/{short_code}/analytics` (legacy: `/analytics/{short_code}`) |

**Response (200 OK):**

```json
{
  "short_code": "1",
  "total_clicks": 1000,
  "created_at": "2026-06-19T11:06:21.257330Z"
}
```

> `total_clicks` = pre-computed aggregated count + unprocessed recent events.

### 4. Health Check

| | |
|---|---|
| **Method** | `GET` |
| **Path** | `/health` |

**Response (200 / 503):**

```json
{
  "status": "healthy",
  "db": "ok",
  "redis": "ok"
}
```

| Status | `status` value | Meaning |
|--------|---------------|---------|
| `200` | `healthy` | All systems operational |
| `200` | `degraded` | DB up, Redis down (cache disabled) |
| `503` | `unhealthy` | DB down — do not route traffic |

### Error Response Format

All errors follow a unified schema:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "URL not found"
  }
}
```

| Error Code | HTTP Status | Description |
|-----------|-------------|-------------|
| `INVALID_URL` | 400 | Malformed or blocked URL scheme |
| `INVALID_ALIAS` | 400 | Custom alias contains invalid characters |
| `INVALID_INPUT` | 400 | General validation failure |
| `NOT_FOUND` | 404 | Short code does not exist |
| `ALIAS_TAKEN` | 409 | Custom alias already in use |
| `EXPIRED` | 410 | URL past its `expires_at` time |
| `TOO_MANY_REQUESTS` | 429 | Rate limit exceeded |
| `INTERNAL_SERVER_ERROR` | 500 | Unexpected server error |

---

## Database Schema

```text
┌─────────────────────────┐     ┌─────────────────────────┐
│         urls            │     │     click_events        │
├─────────────────────────┤     ├─────────────────────────┤
│ id (PK, BigInt, auto)   │     │ id (PK, BigInt, auto)   │
│ short_code (unique, idx)│     │ short_code (idx)        │
│ long_url                │     │ created_at (idx)        │
│ custom_alias (unique)   │     │ processed (idx, bool)   │
│ click_count (BigInt)    │     └─────────────────────────┘
│ expires_at              │         No FK — zero lock
│ created_at              │         contention on writes
└─────────────────────────┘

┌─────────────────────────┐
│   idempotency_keys      │
├─────────────────────────┤
│ key (PK, String)        │
│ response_body (JSON)    │
│ created_at              │
└─────────────────────────┘
```

---

## Load Testing

```bash
python load_test.py
```

Runs concurrent redirect benchmarks and writes results to `load_test_result.txt`.

---

## License

This project is licensed under the terms in the [LICENSE](LICENSE) file.
