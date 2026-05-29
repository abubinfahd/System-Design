# 🔗 Mini URL Shortener with Analytics (Milestone 3)

Welcome to the **Mini URL Shortener** project! This is the implementation of **Milestone 3: Production-Grade API Design**.

This version transitions the codebase to a production-grade service by introducing versioned endpoints, robust request validation, deterministic Base62 encoding, custom aliases, expiration times, thread-safe rate limiting, idempotency key checks, and a standardized global error response format.

## Features & Enhancements

1. **Deterministic Short Codes**: Switched from random string generation to auto-incrementing database ID encoded into a Base62 alphanumeric string. This avoids collision checking and extra database lookups under write load.
2. **Versioned API Endpoints**: Introduced `/v1/` prefix for all operational endpoints (`POST /v1/urls` and `GET /v1/urls/{short_code}/analytics`). Backward-compatible aliases (`POST /shorten` and `GET /analytics/{short_code}`) are retained to prevent breaking legacy client integrations.
3. **Custom Aliases**: Users can supply a custom string to use as the short code (matching `[a-zA-Z0-9_-]`). Uniqueness checks prevent duplicate aliases.
4. **URL Expiration**: Optional `expires_at` timestamp. Accessing expired URLs redirects to an HTTP `410 Gone` error page.
5. **Idempotency Keys**: Accept an optional `Idempotency-Key` header on creation requests. Concurrent retries return the cached response without corrupting data or generating duplicate database rows.
6. **Rate Limiting**: Thread-safe, sliding-window in-memory rate limiter protecting creation write paths (restricted to 10 requests per minute per IP).
7. **Globally Structured Errors**: A unified response schema for all application errors:
   ```json
   {
     "error": {
       "code": "ERROR_CODE",
       "message": "Human readable description"
     }
   }
   ```

## Tech Stack

- **Backend Framework:** FastAPI (Python)
- **Database:** PostgreSQL (with SQLite fallback for local verification)
- **ORM:** SQLAlchemy
- **Validation:** Pydantic v2
- **Containerization:** Docker & Docker Compose
- **Rate Limiter:** Custom thread-safe sliding window in-memory implementation

---

## Project Structure (Modular Design)

```text
url_shortner/
├── app/
│   ├── main.py               # Application entry point & exception handlers registration
│   ├── api/
│   │   └── routes.py         # API endpoints (versioned routes and legacy aliases)
│   ├── core/
│   │   ├── config.py         # App configuration & environment variables
│   │   ├── errors.py         # Custom exceptions & global HTTP error formatting handlers
│   │   └── rate_limiter.py   # In-memory sliding-window IP rate limiter
│   ├── db/
│   │   ├── database.py       # Database connection setup
│   │   └── models.py         # SQLAlchemy tables (URL, Click, IdempotencyKey)
│   ├── schemas/
│   │   └── schemas.py        # Request & Response Pydantic validation
│   └── services/
│       └── url_service.py    # Core business logic (Base62 encoding & transactional writes)
├── Dockerfile                # Docker container build script
├── docker-compose.yml        # Docker compose orchestrator (Web & PostgreSQL containers)
├── requirements.txt          # Python dependencies
└── load_test.py              # Performance load test runner
```

---

## How to Run the Project

### Option 1: Using Docker (Recommended for Production/Local Dev)

Start the database and application containers in the background:

```bash
docker-compose up --build
```

The API will be available at `http://localhost:8000`.

### Option 2: Running Locally without Docker

1. Create and activate a Python virtual environment:
   ```bash
   python -m venv venv
   venv\Scripts\activate  # On Windows
   # source venv/bin/activate # On Unix/macOS
   ```
2. Copy environment settings and configure database connection string:
   ```bash
   copy .env.example .env
   # Open .env and adjust configurations (DATABASE_URL & BASE_URL)
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Start the development server:
   ```bash
   uvicorn app.main:app --reload
   ```

---

## Interactive API Documentation

FastAPI automatically serves interactive Swagger UI documentation at:

👉 **[http://localhost:8000/docs](http://localhost:8000/docs)**

---

## API Reference

### 1. Create Short URL

- **Method:** `POST`
- **Path:** `/v1/urls` (Legacy alias: `/shorten`)
- **Headers**: 
  - `Idempotency-Key`: `uuid` (Optional, prevents duplicate creates on retries)
- **Body:**
  ```json
  {
    "long_url": "https://www.google.com",
    "custom_alias": "my-google-search",
    "expires_at": "2026-12-31T23:59:59Z"
  }
  ```
  *(Both `custom_alias` and `expires_at` are optional)*
- **Response (HTTP 201):**
  ```json
  {
    "short_code": "my-google-search",
    "short_url": "http://localhost:8000/my-google-search",
    "created_at": "2026-05-29T11:06:21.257330Z"
  }
  ```

### 2. Redirect to Original URL (Hot Path)

- **Method:** `GET`
- **Path:** `/{short_code}` (e.g. `http://localhost:8000/1`)
- **Behavior:** Performs an HTTP `302 Found` redirection to the original long URL and records a click tracking record.
- **Failures:**
  - **HTTP 404 Not Found**: If the short code does not exist.
  - **HTTP 410 Gone**: If the URL has passed its expiration time.

### 3. View Analytics

- **Method:** `GET`
- **Path:** `/v1/urls/{short_code}/analytics` (Legacy alias: `/analytics/{short_code}`)
- **Response (HTTP 200):**
  ```json
  {
    "short_code": "1",
    "total_clicks": 1000,
    "created_at": "2026-05-29T11:06:21.257330Z"
  }
  ```

---

## Load Testing

We include a load testing script `load_test.py` to evaluate concurrent redirects and measure average response latency:

```bash
python load_test.py
```

The script prints the benchmark metrics to the terminal and records results in `load_test_result.txt` before performing clean-up database tasks.
