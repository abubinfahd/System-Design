# Milestone 5: Caching Strategy (Deep Dive)

Up until now, every redirect went straight to Postgres. That works fine at small scale. But as traffic grows, you will hit a wall — not because your code is wrong, but because you are asking your database to answer the same question millions of times a day.

This milestone is about understanding Redis not as a buzzword, but as a distributed systems component with specific guarantees, failure modes, and design tradeoffs.

Cache bugs are subtle. Bad caching can silently serve wrong data to users. By the end of this milestone, you will understand not just how to use Redis, but when it lies to you and what to do about it.

---

## Table of Contents

- [Step 1: Why Cache Exists](#step-1-why-cache-exists)
- [Step 2: The Redirect Path with Redis](#step-2-the-redirect-path-with-redis)
- [Step 3: Cache-Aside Pattern](#step-3-cache-aside-pattern)
- [Step 4: TTL Strategy](#step-4-ttl-strategy)
- [Step 5: Cache Invalidation](#step-5-cache-invalidation)
- [Step 6: Cache Stampede](#step-6-cache-stampede)
- [Step 7: When the Cache Lies to You](#step-7-when-the-cache-lies-to-you)
- [Step 8: Redis Failure Handling](#step-8-redis-failure-handling)
- [Step 9: Multi-Layer Caching](#step-9-multi-layer-caching)
- [Common Mistakes](#common-mistakes)
- [Final Mental Model](#final-mental-model)

---

## Step 1: Why Cache Exists

Let's start with the honest problem.

### Without Redis

Every redirect runs this query:

```sql
SELECT long_url FROM urls WHERE short_code = 'abc123';
```

That is fine when you have 100 users. But at 1 million redirects per day, you are running this same query over and over — often for the same short codes. Popular links get clicked hundreds of times an hour. Each click goes to Postgres. Each query opens a connection, hits disk (or buffer pool), and waits.

The problems compound:

- DB connection pool fills up — new requests wait in queue
- Query latency increases under load
- A single viral link creates a hotspot, hammering the same rows repeatedly
- Your DB, which is also handling writes and analytics, starts slowing down for everyone

None of these problems are about your query being slow. A properly indexed query on `short_code` is fast. The problem is volume — asking Postgres to do the same fast thing a million times.

### With Redis

Redis stores the mapping in memory:

```
"abc123" → "https://example.com/very-long-path"
```

Reading from memory is roughly 100x faster than reading from a database. More importantly, it removes the database from the redirect path entirely on cache hits.

```
Without Redis:  Client → FastAPI → Postgres   (~10–20ms)
With Redis:     Client → FastAPI → Redis       (~1ms)
                                  ↓ (miss only)
                                  Postgres
```

> Cache exists to protect the database. Not primarily to make things faster — though that is a welcome side effect. The real goal is to absorb traffic so your DB never sees more load than it can handle.

---

## Step 2: The Redirect Path with Redis

There are two cases on every redirect: cache hit and cache miss.

### Cache Hit (the common case)

```
GET /abc123
  ↓
FastAPI checks Redis
  ↓
Redis returns "https://example.com/very-long-path"
  ↓
FastAPI returns 302 redirect
```

Latency: ~1ms. Postgres is never touched.

### Cache Miss (first time or after expiry)

```
GET /abc123
  ↓
FastAPI checks Redis
  ↓
Redis: key not found
  ↓
FastAPI queries Postgres
  ↓
FastAPI stores result in Redis
  ↓
FastAPI returns 302 redirect
```

The first request for a given short code pays the Postgres cost. Every subsequent request is served from Redis until the cache entry expires or is invalidated.

This pattern has a name.

---

## Step 3: Cache-Aside Pattern

Cache-aside is the most common caching pattern in production systems. The application — not the database, not the cache — is responsible for keeping them in sync.

### The pattern in plain English

1. Check cache first
2. If found, return it (cache hit)
3. If not found, go to the database
4. Store the result in cache
5. Return the result

### Why this pattern works well

- **Lazy loading** — you only cache data that is actually requested. No wasted memory on URLs that nobody clicks.
- **Simple** — no background sync jobs, no complex write-through logic. Just check, miss, fill, return.
- **Resilient** — if Redis goes down, you fall back to Postgres. The system degrades gracefully instead of crashing.

### Implementation

```python
def get_long_url(short_code: str) -> str | None:
    # Step 1: check Redis first
    cached = redis.get(short_code)
    if cached:
        return cached.decode("utf-8")   # cache hit — return immediately

    # Step 2: cache miss — go to Postgres
    url = db.execute(
        "SELECT long_url FROM urls WHERE short_code = :code",
        {"code": short_code}
    ).scalar()

    if not url:
        return None   # short code does not exist

    # Step 3: store in Redis for next time
    redis.set(short_code, url)

    # Step 4: return the result
    return url
```

Clean, readable, and follows a single responsibility: check cache, populate if missing, return result.

---

## Step 4: TTL Strategy

TTL (Time To Live) controls how long a cached value stays in Redis before it expires automatically. When it expires, the next request gets a cache miss and re-fetches from Postgres.

### Should URL redirects have a TTL?

For most URL shorteners: **no TTL, or a very long TTL.**

Here is why. URLs in this system are essentially immutable. Once `abc123` points to `https://example.com`, it almost never changes. There is no reason to expire the cache entry and force a Postgres lookup — the data is stable.

```python
redis.set(short_code, long_url)           # no TTL — lives forever
redis.set(short_code, long_url, ex=86400) # or 24h TTL if you prefer a safety net
```

### When TTL matters

TTL becomes important when data changes frequently. A few examples:

| Data type | Good TTL |
|-----------|----------|
| URL redirect (stable) | No TTL or 24h+ |
| User profile | 5–15 minutes |
| Stock price | 10–30 seconds |
| Session token | Match session lifetime |

The general rule: TTL should match how often your source data actually changes. Setting a 60-second TTL on stable URL data just causes 60-second cache misses for no reason.

### The TTL trap

A common mistake is using a short TTL "just to be safe." The thinking goes: "If TTL is short, stale data disappears quickly." True, but the cost is constant cache misses — which sends traffic back to Postgres. You just rebuilt the problem you were trying to solve.

Use long TTLs for stable data, and use explicit invalidation (next step) when data actually changes.

---

## Step 5: Cache Invalidation

There is a famous saying in computer science:

> "There are only two hard things in Computer Science: cache invalidation and naming things."

It is famous because it is true. Let us understand why.

### The stale cache problem

Suppose a user updates their short URL to point to a new destination:

```
Before:  abc123 → https://old-site.com
After:   abc123 → https://new-site.com
```

The database is updated immediately. But Redis still has the old value. Every user who clicks the link gets redirected to the old site until the cache entry expires — which, with no TTL, is never.

```
DB:    abc123 → https://new-site.com    (correct)
Redis: abc123 → https://old-site.com   (stale, wrong)
```

This is a real user-facing bug. Users get sent to the wrong place.

### Solution 1: Delete the cache entry on update (recommended)

When a URL is updated in the database, immediately delete the corresponding Redis key:

```python
def update_url(short_code: str, new_long_url: str):
    # Step 1: update the database (source of truth)
    db.execute(
        "UPDATE urls SET long_url = :url WHERE short_code = :code",
        {"url": new_long_url, "code": short_code}
    )

    # Step 2: delete the cache entry
    redis.delete(short_code)

    # Next request will get a cache miss and fetch fresh data from DB
```

The next user who clicks the link gets a cache miss, fetches the new URL from Postgres, and repopulates the cache with correct data. Clean and reliable.

### Solution 2: Update the cache entry directly

```python
def update_url(short_code: str, new_long_url: str):
    db.execute(...)          # update DB
    redis.set(short_code, new_long_url)   # overwrite cache immediately
```

This is faster — there is no cache miss after the update. But it is riskier. If the DB update fails after you already updated Redis, your cache has data that does not exist in the DB. Now they are out of sync.

### Which to use?

| Strategy | Simpler | Safer | Slower (one miss) |
|----------|---------|-------|-------------------|
| Delete on update | Yes | Yes | Yes (one miss after update) |
| Update immediately | No | No | No |

For a URL shortener where updates are rare, delete-on-update is the right choice. Simplicity and correctness over saving one cache miss.

---

## Step 6: Cache Stampede

You have solved the stale cache problem. Now here is a different problem that happens when things go right — but at scale.

### The scenario

A link goes viral. 10,000 people click it within the same second. The cache entry for that short code just expired (or never existed). Every single one of those 10,000 requests checks Redis, gets a miss, and then goes to Postgres.

```
10,000 requests
  ↓ all get cache miss
10,000 DB queries
  ↓ simultaneously
Postgres melts
```

This is called a cache stampede (also known as thundering herd). The cache, which was supposed to protect the DB, just dropped all protection at once.

### Why this happens more than you think

- A popular short code's cache entry expires
- Redis restarts and loses all data
- A new viral link gets shared before anyone has cached it
- A cache invalidation clears a key that 5,000 people are about to click

### Solution: Request coalescing with a distributed lock

Only one request fetches from Postgres. The rest wait.

```python
import time

def get_long_url_safe(short_code: str) -> str | None:
    # Step 1: check cache normally
    cached = redis.get(short_code)
    if cached:
        return cached.decode("utf-8")

    # Step 2: cache miss — try to acquire a lock
    lock_key = f"lock:{short_code}"
    lock_acquired = redis.set(lock_key, "1", nx=True, ex=5)  # 5 second lock

    if lock_acquired:
        # This request won the lock — go fetch from DB
        try:
            url = db_lookup(short_code)
            if url:
                redis.set(short_code, url)
            return url
        finally:
            redis.delete(lock_key)   # always release the lock
    else:
        # Another request is already fetching — wait briefly and retry
        time.sleep(0.05)
        cached = redis.get(short_code)
        return cached.decode("utf-8") if cached else db_lookup(short_code)
```

**Result:**

```
10,000 requests hit Redis → cache miss
  ↓
1 request acquires lock → goes to Postgres
9,999 requests wait (50ms) → read from cache
  ↓
1 DB query serves 10,000 users
```

Your database sees one query instead of ten thousand. This is the difference between a system that handles viral traffic and one that crashes under it.

---

## Step 7: When the Cache Lies to You

This is the most important section in this milestone. Most engineers learn this the hard way in production.

### The scenario

Redis says:

```
abc123 → https://old-site.com
```

Postgres says:

```
abc123 → https://new-site.com
```

Users are being redirected to the wrong URL. The system is confidently returning wrong data.

### How does this happen?

**Scenario A: Failed invalidation**

```
1. URL updated in DB successfully
2. redis.delete(short_code) → fails (network error, Redis timeout)
3. DB has new URL, Redis has old URL
4. Users get wrong redirect until cache expires or server restarts
```

**Scenario B: Race condition**

```
Time 0ms:  Request A reads URL from DB (old value)
Time 1ms:  Someone updates URL in DB + deletes cache
Time 2ms:  Request A writes OLD value back into Redis
Time 3ms:  Cache now has stale data again, despite the invalidation
```

This is particularly subtle. The invalidation happened correctly, but a concurrent read-then-write raced ahead and restored the old value.

**Scenario C: Redis restart without persistence**

```
Redis restarts with empty cache
First request populates cache from DB — fine
But if DB was mid-update when Redis restarted, data may be inconsistent
```

### The key insight

> Redis is not the source of truth. Postgres is.

Redis is an optimization layer. It serves data faster, but it can be wrong. Any time you read from Redis, you are accepting a tradeoff: speed in exchange for the possibility of staleness.

For a URL shortener where URLs rarely change, this tradeoff is acceptable. A user might get the old redirect for a few seconds after an update. That is usually fine.

But for financial transactions, inventory counts, or anything where correctness is non-negotiable, you would either skip the cache for writes or use stronger consistency guarantees (like read-your-writes consistency patterns).

### How to minimize the risk

1. Always update the database first, then invalidate the cache — never the reverse
2. Add a reasonable TTL even on "stable" data as a safety net (e.g., 24 hours)
3. Log cache invalidation failures and retry them
4. Accept that caches can be stale, and design your system's tolerance for staleness explicitly

---

## Step 8: Redis Failure Handling

Redis goes down. It will happen — during deployments, network partitions, OOM kills, or hardware failures. Your system should not crash when it does.

### The wrong response

```python
def get_long_url(short_code: str):
    return redis.get(short_code)   # if Redis is down, this raises an exception
                                   # and your entire redirect API returns 500
```

Users cannot access any short links because your cache layer is down. This is worse than not having a cache at all.

### The correct response: graceful degradation

```python
def get_long_url(short_code: str) -> str | None:
    try:
        cached = redis.get(short_code)
        if cached:
            return cached.decode("utf-8")
    except Exception as e:
        # Redis is down — log the error, continue to DB
        logger.warning(f"Redis unavailable: {e}")

    # Fall through to Postgres
    return db_lookup(short_code)
```

When Redis is down, every request goes to Postgres. That is slower, but the system stays functional. Users notice slightly higher latency. They do not see errors.

### But protect Postgres too

If Redis has been protecting Postgres from 10,000 requests per second, and Redis goes down, Postgres suddenly sees all 10,000 directly. This is called a cache stampede at the infrastructure level.

Add a circuit breaker: if Redis has been down for more than a few seconds, start rate limiting incoming requests to Postgres stays within safe limits.

```python
# Pseudocode for circuit breaker pattern
if redis_failure_count > THRESHOLD:
    # Redis is likely down — protect DB
    if request_rate > DB_SAFE_LIMIT:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
```

This is the difference between a degraded-but-functional system and a complete outage.

---

## Step 9: Multi-Layer Caching

For systems at very high scale, even Redis can become a bottleneck. The solution is adding a layer of in-process memory cache in front of Redis.

### The architecture

```
Request
  ↓
L1 Cache (in-process Python dict)   ← microseconds
  ↓ (miss)
L2 Cache (Redis)                    ← ~1ms
  ↓ (miss)
Postgres                            ← 5–20ms
```

### Why add an L1 cache?

| Layer | Latency | Network hop | Shared across servers |
|-------|---------|-------------|----------------------|
| Local dict (L1) | ~0.01ms | None | No |
| Redis (L2) | ~1ms | Yes | Yes |
| Postgres | ~10ms | Yes | Yes |

For the most popular short codes — the top 1% that receive 80% of traffic — even 1ms Redis latency multiplied by millions of requests per day adds up. An in-process cache eliminates the network hop entirely.

### Simple L1 implementation

```python
from functools import lru_cache
import time

# LRU cache with max 1000 entries
local_cache = {}
CACHE_TTL = 60   # 60 seconds local TTL

def get_long_url(short_code: str) -> str | None:
    # L1: check local memory first
    entry = local_cache.get(short_code)
    if entry and time.time() < entry["expires"]:
        return entry["url"]

    # L2: check Redis
    try:
        cached = redis.get(short_code)
        if cached:
            url = cached.decode("utf-8")
            local_cache[short_code] = {"url": url, "expires": time.time() + CACHE_TTL}
            return url
    except Exception:
        pass

    # L3: Postgres
    url = db_lookup(short_code)
    if url:
        redis.set(short_code, url)
        local_cache[short_code] = {"url": url, "expires": time.time() + CACHE_TTL}

    return url
```

### Important tradeoff

L1 cache is per-server. If you have 10 FastAPI instances, each has its own L1 cache. A cache invalidation on Redis does not automatically clear all L1 caches. You need either a short TTL on L1 entries, or a pub/sub mechanism to broadcast invalidations to all servers.

For most URL shorteners, a 60-second L1 TTL is perfectly acceptable. A URL update takes at most 60 seconds to propagate everywhere.

---

## Common Mistakes

| Mistake | What goes wrong |
|---------|-----------------|
| Caching everything blindly | Memory fills up with data nobody requests |
| No cache invalidation | Users get stale data indefinitely |
| Very short TTL on stable data | Constant cache misses — DB load never reduces |
| No fallback when Redis is down | Entire API returns 500 errors |
| Updating cache before DB | Cache has data that DB does not — inconsistency |
| Ignoring cache stampede | First request after TTL expiry hammers DB |

---

## Final Mental Model

```
Postgres  =  durability + truth + consistency
Redis     =  speed + protection + convenience
```

Postgres is where your data actually lives. Redis is a fast, temporary copy of the most frequently accessed parts of it. Redis can be empty, wrong, or missing — your system should continue to work. Postgres going down is an outage. Redis going down is a slowdown.

Design accordingly.

---

## Final Deep Question

Before moving on, sit with this:

> Redis contains: `abc123 → https://old-site.com`
> Postgres contains: `abc123 → https://new-site.com`

How can this inconsistency exist even if your invalidation code is correct?

Think through:

- What if two requests are processed concurrently?
- What if the invalidation call succeeded but a background thread was mid-read?
- What if Redis acknowledged the delete but had not actually flushed it yet?
- What ordering guarantees does Redis give you, and what does it not?

This is distributed systems thinking. There is no single right answer — just tradeoffs to reason through explicitly.

---

## Next

Milestone 6: Scaling Strategy — horizontal scaling, load balancing, read replicas, and the decisions you make when a single server is no longer enough.
