# Milestone 7: Observability

Your system is deployed. Traffic is flowing. And then a user sends you a message:

> "Short links are slow."

That is it. No stack trace. No error code. Just a complaint and a blinking cursor.

Now what? Is FastAPI slow? Is Redis down? Is Postgres overwhelmed? Is one API server having a bad time while the others are fine? Is it slow for everyone or just this one user?

Without observability, you are guessing. You SSH into servers, run random queries, check logs manually, and hope you find the problem before the user gives up. This is how most engineers spend their on-call shifts — reactive, stressed, and operating in the dark.

With observability, you open a dashboard and within 60 seconds you know exactly which component is slow, when it started, and what changed. That is the goal.

---

## Table of Contents

- [The Three Pillars](#the-three-pillars)
- [Pillar 1: Logging](#pillar-1-logging)
- [Pillar 2: Metrics](#pillar-2-metrics)
- [Pillar 3: Distributed Tracing](#pillar-3-distributed-tracing)
- [Correlation IDs](#correlation-ids)
- [Health Checks](#health-checks)
- [Alerting](#alerting)
- [Putting It All Together: A Real Scenario](#putting-it-all-together-a-real-scenario)
- [Production Stack](#production-stack)
- [URL Shortener Metrics Reference](#url-shortener-metrics-reference)
- [Mental Model](#mental-model)

---

## The Three Pillars

Observability is built on three complementary tools. Each one answers a different question.

```
Logs    →  What happened?
Metrics →  How often is it happening?
Traces  →  Where in the system is it happening?
```

You need all three. Logs alone tell you individual events but not trends. Metrics alone tell you something is wrong but not why. Traces alone show you the path of a request but not the broader pattern. Together, they give you a complete picture.

---

## Pillar 1: Logging

Logs are the most familiar observability tool. Every time something meaningful happens in your system, you write a line describing it. Over time, those lines tell the story of what your system has been doing.

### What good logs look like

Bad log:

```
Something went wrong
```

This tells you nothing actionable. What went wrong? For which request? At what time? How long did it take?

Good log:

```
2026-06-19 10:01:05 ERROR
  event=redirect_failed
  short_code=abc123
  error=postgres_timeout
  duration_ms=2300
  request_id=xyz789
  server=api-server-2
```

Now you know: a redirect for `abc123` failed because Postgres timed out, it took 2.3 seconds, and it happened on `api-server-2`. You can go fix it.

### Logging in FastAPI

```python
import structlog
import time

logger = structlog.get_logger()

@app.get("/{short_code}")
def redirect(short_code: str, request: Request):
    start = time.time()
    request_id = request.headers.get("X-Request-ID", generate_id())

    log = logger.bind(short_code=short_code, request_id=request_id)
    log.info("redirect_requested")

    # Check Redis
    cached = redis.get(short_code)
    if cached:
        log.info("cache_hit", duration_ms=(time.time() - start) * 1000)
        return RedirectResponse(cached)

    log.info("cache_miss")

    # Fall through to Postgres
    try:
        url = db_lookup(short_code)
    except TimeoutError as e:
        log.error("db_timeout", error=str(e), duration_ms=(time.time() - start) * 1000)
        raise HTTPException(status_code=503)

    if not url:
        log.warning("short_code_not_found")
        raise HTTPException(status_code=404)

    redis.set(short_code, url)
    log.info("redirect_served", source="db", duration_ms=(time.time() - start) * 1000)
    return RedirectResponse(url)
```

Every log line carries the `short_code` and `request_id` automatically. You can search your log system for any `request_id` and see the complete story of that request.

### Log levels

| Level | When to use |
|-------|-------------|
| `DEBUG` | Detailed internal state during development. Not used in production. |
| `INFO` | Normal events worth recording — request received, cache hit, redirect served. |
| `WARNING` | Something unexpected happened but the system handled it — short code not found, high latency. |
| `ERROR` | Something failed and needs attention — DB timeout, Redis down, unhandled exception. |

Do not log everything at `ERROR`. Engineers learn to ignore noisy error logs. Reserve `ERROR` for things that actually need someone to wake up and fix something.

---

## Pillar 2: Metrics

Logs tell individual stories. Metrics tell trends.

A log says: "This one request to `abc123` took 2300ms."

A metric says: "Over the last 5 minutes, P99 redirect latency has been 2400ms, up from 12ms an hour ago."

That second statement tells you there is a systemic problem, not a one-off event. That is the kind of signal that triggers an alert and gets someone investigating before users start complaining en masse.

### Why percentiles matter more than averages

Imagine your system handles 100 redirect requests per second. 99 of them complete in 10ms. One of them takes 5000ms.

```
Average latency = (99 × 10 + 1 × 5000) / 100 = ~60ms
```

An average of 60ms looks perfectly fine on a dashboard. But 1% of your users are waiting 5 full seconds for a redirect. That is a real problem that the average completely hides.

Percentiles expose the truth:

```
P50 (median):  10ms    ← typical user experience
P95:           12ms    ← 95% of users see this or better
P99:           5000ms  ← the slowest 1% — these are your unhappy users
```

Always monitor P95 and P99. The average is a metric that lies.

### Metrics for your URL shortener

**API layer**

```
redirect_requests_total        ← how many redirects per second/minute/day
redirect_latency_ms (P50/P95/P99) ← how fast are they completing
5xx_error_rate                 ← what fraction of requests are failing
```

**Cache layer**

```
cache_hit_rate                 ← percentage of requests served by Redis
cache_miss_rate                ← percentage falling through to Postgres
redis_memory_usage_bytes       ← how full is Redis getting
```

**Database layer**

```
db_query_latency_ms            ← how long are queries taking
db_connections_active          ← how close to connection pool limit
db_slow_queries_total          ← count of queries over a threshold (e.g. 100ms)
```

**Business layer**

```
urls_created_total             ← new short URLs per day
redirects_per_day              ← total traffic volume
click_events_written_total     ← analytics pipeline health
```

### Implementing metrics with Prometheus

```python
from prometheus_client import Counter, Histogram, Gauge

# Define metrics
redirect_total = Counter(
    "redirect_requests_total",
    "Total redirect requests",
    ["status"]   # label: hit, miss, not_found, error
)

redirect_latency = Histogram(
    "redirect_latency_ms",
    "Redirect request latency in milliseconds",
    buckets=[1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000]
)

cache_hit_rate = Gauge(
    "cache_hit_rate",
    "Current cache hit rate (0.0 to 1.0)"
)

# Use them in your endpoint
@app.get("/{short_code}")
def redirect(short_code: str):
    with redirect_latency.time():
        cached = redis.get(short_code)

        if cached:
            redirect_total.labels(status="hit").inc()
            return RedirectResponse(cached)

        url = db_lookup(short_code)
        if not url:
            redirect_total.labels(status="not_found").inc()
            raise HTTPException(status_code=404)

        redirect_total.labels(status="miss").inc()
        return RedirectResponse(url)
```

Prometheus scrapes these metrics every 15 seconds. Grafana reads from Prometheus and renders dashboards. You get a real-time view of your system's health without writing any dashboard code from scratch.

---

## Pillar 3: Distributed Tracing

Logs and metrics tell you *what* and *how much*. Traces tell you *where*.

This becomes critical once your request passes through multiple services. A redirect request touches FastAPI, then Redis, then possibly Postgres. If the total request takes 3 seconds, which of those three components is responsible?

### What a trace looks like

```
Request: GET /abc123  (total: 2912ms)

├── FastAPI handler        10ms
│     ├── Auth middleware   2ms
│     └── Request parsing   8ms
├── Redis lookup            2ms   ← cache miss
└── Postgres query       2900ms   ← HERE is your problem
```

Without tracing, you would look at the total 3-second response time and guess at the culprit. With tracing, you see the exact breakdown. Postgres took 2900ms out of a 2912ms total request. That is where you investigate.

### How tracing works

When a request arrives, you assign it a unique `trace_id`. As the request flows through each component, you create a `span` — a timed record of work done by that component. Each span carries the `trace_id`, so they can all be stitched together into one timeline.

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

tracer = trace.get_tracer("url-shortener")

@app.get("/{short_code}")
def redirect(short_code: str):
    with tracer.start_as_current_span("redirect") as span:
        span.set_attribute("short_code", short_code)

        with tracer.start_as_current_span("redis_lookup"):
            cached = redis.get(short_code)

        if cached:
            span.set_attribute("cache", "hit")
            return RedirectResponse(cached)

        with tracer.start_as_current_span("db_lookup"):
            url = db_lookup(short_code)

        return RedirectResponse(url)
```

OpenTelemetry collects the spans. Jaeger (or Grafana Tempo) stores and visualizes them. You search by `trace_id` or by "slowest requests in the last hour" and immediately see the breakdown.

---

## Correlation IDs

Here is a small addition that makes your logs dramatically more useful.

Every request that enters your system gets a unique `request_id` (also called a correlation ID). Every log line written during that request includes this ID. When a user reports a problem, they give you their request ID (or you find it in the error response), and you search your log system for it. You see the complete story of that single request across every service it touched.

```
2026-06-19 10:01:05 INFO  request_id=xyz789 event=redirect_started short_code=abc123
2026-06-19 10:01:05 INFO  request_id=xyz789 event=cache_miss
2026-06-19 10:01:07 ERROR request_id=xyz789 event=db_timeout duration_ms=2300
```

Three log lines, three seconds apart, all tied together by `xyz789`. Without the correlation ID, finding these three lines in a sea of logs from multiple servers is nearly impossible.

```python
import uuid
from fastapi import Request

@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

    # Attach to all logs for this request
    with structlog.contextvars.bound_contextvars(request_id=request_id):
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
```

The `X-Request-ID` header is also returned to the client. If a user reports a problem, ask them to include this header from their browser's network tab. Now you can find their exact request in your logs instantly.

---

## Health Checks

A health check is a simple endpoint that tells your infrastructure whether a server is ready to receive traffic.

```python
@app.get("/health")
def health():
    # Optionally check dependencies too
    try:
        db.execute("SELECT 1")
        redis.ping()
        return {"status": "healthy", "db": "ok", "redis": "ok"}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)}
        )
```

Your load balancer polls this endpoint every 5–10 seconds. If a server returns anything other than 200, the load balancer stops routing traffic to it and alerts. The server is quarantined until it recovers.

Kubernetes uses two types:

- **Liveness probe** — is the process alive? If not, restart the container.
- **Readiness probe** — is the server ready to handle traffic? If not, remove it from the load balancer pool but do not restart it.

The distinction matters. A server might be alive (process is running) but not ready (still warming up the cache, or temporarily overwhelmed). The readiness probe lets it recover gracefully without being restarted unnecessarily.

---

## Alerting

Metrics are only useful if someone sees them when something goes wrong. Dashboards require someone to be watching. Alerts fire automatically when a threshold is crossed.

### Alert rules for your URL shortener

**Redis degraded**

```
IF cache_hit_rate < 80% FOR 5 minutes
THEN alert: "Redis performance degraded — cache hit rate dropping"
```

A sustained drop below 80% means either Redis is struggling or a lot of cache misses are happening — which means Postgres is taking on load it was not designed to handle alone.

**API error rate elevated**

```
IF 5xx_error_rate > 1% FOR 2 minutes
THEN alert: "API error rate elevated — something is broken"
```

1% is a reasonable threshold for a redirect service. Higher than that and real users are hitting errors.

**Database overload risk**

```
IF db_cpu_usage > 85% FOR 3 minutes
THEN alert: "Database CPU high — risk of overload"
```

Give yourself warning before the DB actually falls over.

**Redirect latency degraded**

```
IF redirect_latency_p99 > 500ms FOR 5 minutes
THEN alert: "Redirect P99 latency elevated — users experiencing slowness"
```

This is the metric that would have caught the problem your user reported at the start of this milestone.

### Alert fatigue is real

Do not add alerts for everything. Every false alarm trains engineers to ignore alerts. Alert only on conditions that require human action. A brief Redis latency spike that self-resolves in 30 seconds is not worth waking someone up at 2am. A sustained error rate above 5% absolutely is.

---

## Putting It All Together: A Real Scenario

A user clicks `short.ly/abc123`. The redirect takes 4 seconds. They report it.

**Step 1: Check metrics (what is the scale?)**

You open Grafana. Redirect P99 latency has been elevated for the past 20 minutes — not just this one user. Cache hit rate has dropped from 95% to 40%. DB query latency is at 800ms average.

This is not a one-off event. Something systemic changed 20 minutes ago.

**Step 2: Check traces (where is the time going?)**

You open Jaeger and filter for slow requests in the last 20 minutes. Every slow trace shows the same pattern:

```
FastAPI handler:   8ms
Redis lookup:      2ms   ← cache miss
Postgres query:   3800ms  ← this is the problem
```

Almost every request is missing the Redis cache and hammering Postgres.

**Step 3: Check logs (what happened 20 minutes ago?)**

```
2026-06-19 09:41:03 WARNING redis_memory_full evicted=true
2026-06-19 09:41:03 WARNING cache_eviction short_code=abc123
2026-06-19 09:41:03 WARNING cache_eviction short_code=def456
...
```

Redis ran out of memory 20 minutes ago and started evicting cached URLs. The cache is now effectively empty. Every redirect misses Redis and goes to Postgres. Postgres is overwhelmed.

**Conclusion:** Redis memory limit was hit — probably due to a traffic spike or a memory configuration that was too low. Fix: increase Redis memory limit and flush the eviction policy. Optionally add a memory usage alert so this is caught before users feel it next time.

Without logs, metrics, and traces, you would have spent an hour guessing. With them, you had the answer in under 5 minutes.

---

## Production Stack

For a FastAPI-based URL shortener, a practical observability stack looks like this:

| Concern | Tool |
|---------|------|
| Structured logging | `structlog` (Python) |
| Metrics collection | `prometheus_client` (Python) |
| Metrics storage + querying | Prometheus |
| Dashboards | Grafana |
| Distributed tracing | OpenTelemetry SDK |
| Trace storage + UI | Jaeger or Grafana Tempo |
| Alerting | Grafana Alerts or PagerDuty |

All of these are open source. On managed cloud platforms (AWS, GCP), you can substitute with CloudWatch, Cloud Monitoring, or Datadog — which bundle logging, metrics, and tracing into a single service at the cost of vendor lock-in.

---

## URL Shortener Metrics Reference

These are the specific metrics worth tracking from day one:

```
redirect_requests_total          ← volume of redirect traffic
redirect_latency_ms              ← P50, P95, P99 — user experience signal
5xx_error_rate                   ← fraction of requests failing with server errors
cache_hit_rate                   ← health of Redis layer
cache_miss_rate                  ← how much traffic is hitting Postgres
redis_memory_usage_bytes         ← risk of eviction
db_query_latency_ms              ← Postgres performance
db_connections_active            ← proximity to connection pool limit
db_slow_queries_total            ← queries over threshold (e.g. 100ms)
urls_created_total               ← business growth signal
click_events_written_total       ← analytics pipeline health
```

Start with these. Add more only when you have a specific question that existing metrics cannot answer.

---

## Mental Model

```
Logs    →  What happened to this specific request?
Metrics →  How is the system behaving overall, over time?
Traces  →  Which component is responsible for this latency?
```

They work together. Metrics tell you something is wrong. Traces tell you where. Logs tell you why.

---

## The Most Important Lesson

A system is not production-ready because it works under normal conditions.

A system is production-ready when it breaks — and you can understand exactly why it broke, how long it has been broken, how many users were affected, and what to do to fix it.

That is what observability gives you. Not perfection. Clarity.

---

## Next

Milestone 8: Security — authentication, rate limiting, input validation, and protecting your system from the people who will inevitably try to break it.
