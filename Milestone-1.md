# 🔗 Project: Mini URL Shortener with Analytics

---

## Why This Project?

This single system touches **almost everything I've learned** — in one place, for real.

| Concept | Where It Appears |
|---|---|
| Client-Server | API + browser redirect |
| Stateless | Redirect service |
| Scalability | Read-heavy system |
| DNS / HTTP | Redirect flow |
| REST API | Create short URL |
| DB Indexing | Lookup by short code |
| Transactions | URL creation consistency |
| Proxy / CDN thinking | Caching hot URLs |

---

## Core Idea

You build a system where:

- User submits a long URL
- System returns a short URL
- Visiting the short URL → redirects to original
- Tracks click analytics

**High-Level Architecture**

```
Client → FastAPI → DB
              ↓
       Redirect Service
              ↓
         Cache (later)
```

This focused system naturally forces you to make decisions around:

- Scalability
- Indexing strategy
- State vs stateless tradeoffs
- API + DB coupling
- Real bottlenecks

---

---

## Milestones Overview

---

### Milestone 1 — Requirements

- **Functional:** Create short URL, Redirect, Get analytics
- **Non-functional:** Low latency redirect (<100ms), High read traffic, Eventually scalable

---

### Milestone 2 — Back-of-the-Envelope Estimation

**Assume:**

- 10K URLs/day
- 100K redirects/day

**Think:** Read >> Write → read-heavy system

> **Key Insight:** You must optimize the read path, not write.

---

### Milestone 3 — API Design

**Create URL**

```
POST /shorten
{
  "long_url": "https://example.com"
}
```

**Redirect**

```
GET /{short_code}
```

**Analytics**

```
GET /analytics/{short_code}
```

---

### Milestone 4 — Database Design Critical Part

Indexing strategy — this is where engineers fail.

- With index → O(log N) B+ tree lookup

---

### Milestone 5 — Short Code Generation

- Bad: random string → collision risk
- Better: Base62 encode ID

```
id = 125 → "cb"
```

---

### Milestone 6 — FastAPI Implementation

Build the API endpoints based on the design above.

---

### Milestone 7 — Redirect Optimization Important

DB hit for every request → bad at scale.

**Better (Day 4 upgrade):** Add in-memory cache

```
Cache hit  → return immediately
Cache miss → DB → store in cache → return
```

Options: `dict` or `Redis`

---

### Milestone 8 — Scaling Thinking

| Question | Expected Answer |
|---|---|
| DB becomes slow? | Add caching layer + Read replicas |
| Clicks table grows huge? | Batch writes, Kafka-style ingestion, Partitioning |
| Hot URLs? | Cache heavily + CDN layer |
| Single DB bottleneck? | Sharding (by short_code hash) |

---

## Where Most People Fail

| Mistake | What Goes Wrong |
|---|---|
| "Just build API" | No system thinking |
| No indexing strategy | You fail DB design |
| Ignoring read-heavy nature | Wrong scaling decisions |

---

## What You Should Feel After This

If done properly, you'll understand:

- Why caching is **mandatory** (not optional)
- Why **indexes matter more** than schema
- Why read vs write pattern **changes architecture**
- How to think like a backend / system engineer

---

---

# Milestone 1 — Requirements (Done Properly)

> We're not just listing features — we're defining **system boundaries + constraints**.

---

## Step 1: Clarify the Problem (Critical Thinking)

Let's define exactly what system we're building:

> *A service that converts long URLs into short URLs and redirects users while tracking usage.*

But this is still vague. So refine it:

- Is it public? → **Yes**
- Is it read-heavy? → **Yes**
- Is latency critical? → **Yes** (redirect must be fast)

This already tells you:

> You're building a **low-latency, high-read system**.

---

## Step 2: Functional Requirements

These are **what the system must do** (APIs / behavior).

**1. Create Short URL**

- Input: long URL
- Output: short URL

Edge cases:

- Same URL submitted multiple times → same short or new one?
- Decision: **new one** (simpler, avoids dedup complexity)

**2. Redirect to Original URL**

- User hits short URL
- System returns **HTTP 302** → original URL

**3. Track Clicks**

- Every redirect should be recorded

**4. Get Analytics**

- Total clicks per short URL

**We are NOT building:**

- User accounts
- Dashboards
- Real-time analytics
- Geo tracking

---

## Step 3: Non-Functional Requirements

These define **how well** the system performs.

**1. Latency**

- Redirect should be **< 100ms**
- Why? User experience + browser blocking behavior

**2. Availability**

- System should always work — especially redirect
- Even if analytics fails → redirect must succeed

**3. Scalability**

- Should handle: 10K → 1M redirects/day
- Implies: horizontal scalability later

**4. Consistency**

- URL creation → must be **strongly consistent**
- Click tracking → can be **eventually consistent**
- This is a key design tradeoff

**5. Durability**

- URLs must never be lost
- Persistent storage required → **Postgres**

---

## Step 4: Traffic Pattern (Very Important)

| Operation | Frequency |
|---|---|
| Create URL | LOW |
| Redirect | VERY HIGH |
| Analytics | LOW |

**Insight:** This is a **Read-heavy system.**

This single insight will drive:

- Caching decisions
- Indexing strategy
- Scaling approach

---

## Step 5: Constraints (Real-World Thinking)

You must assume:

- Limited budget (no overengineering)
- Single region (for now)
- Simple deployment

---

---

# Q&A — Deep Dives

---

## Q1: Why is Redirect Latency More Critical Than URL Creation?

**Core Idea:** Redirect is on the **user's critical path**. Creation is not.

**Case 1: URL Creation (Slow)**

```
POST /shorten → takes 2 seconds
```

What happens?

- User waits a bit… then gets the link
- Slight annoyance, but acceptable

---

**Case 2: Redirect (Slow)**

```
https://short.ly/abc123 → takes 2 seconds
```

What happens?

- Browser hangs
- User thinks the link is broken
- User might close the tab

---

**Real Insight:**

Redirect is part of *"what happens when you click a link"* — the browser flow.

It blocks:

- Page load
- SEO ranking
- User trust

---

## Q2: Why is Eventual Consistency OK for Clicks but Not URL Creation?

**Core Principle:** Ask — *"What breaks if data is slightly delayed?"*

---

**Example 1: URL Creation (MUST be strong)**

User creates `short.ly/xyz` and immediately tries to open it.

If NOT consistent:

- DB write not committed yet
- Redirect lookup fails
- User sees: `404 Not Found`

**Result:** System is broken.

---

**Example 2: Click Tracking (Eventual is OK)**

User clicks a link. We:

- Redirect immediately
- Log the click later (async)

If delayed:

- Analytics shows 99 instead of 100 clicks
- Does the user notice? **No.**
- Does the system break? **No.**

---

**Engineering Insight:**

| Feature | Consistency |
|---|---|
| URL creation | Strong |
| Click tracking | Eventual |

**Real-world analogy:**

Think of YouTube:

- Views count updates slowly
- But the video must play instantly

---

## Q3: What's the First Bottleneck in a Read-Heavy System?

You hinted correctly: **DB**. Let's go deeper.

**Scenario:**

You have 1M redirects/day. Each redirect does:

```sql
SELECT long_url FROM urls WHERE short_code = ?
```

Every request hits the DB.

**Problem Breakdown:**

1. **DB CPU** — query parsing + execution
2. **Disk I/O** — index lookup (B+ tree)
3. **Connection limits** — too many concurrent requests

**Result:** DB becomes the primary bottleneck.

Not FastAPI. Not network. Not CPU. **It's the Database.**

| Component | Capacity |
|---|---|
| FastAPI | 10K req/sec |
| DB | 1K–2K req/sec |

DB dies first.

**Solution Direction** *(don't implement yet)*

Think about:

- Cache (Redis / in-memory)
- Reduce DB hits
- Read replicas

**Final Insight:**

> In read-heavy systems: **your job is to protect the database.**

---

### Quick Summary So Far

| Question | Key Learning |
|---|---|
| Q1 | Optimize the user-critical path |
| Q2 | Not all data needs strong consistency |
| Q3 | DB is the first bottleneck in read-heavy systems |

---

## Q4: If 80% of Requests Are for the Same 10 URLs — How Do You Redesign?

A junior says *"use Redis"*.  
A strong engineer explains **how, where, and why**.

**Problem:** This is called the **hot key / hotspot problem.**

---

**Naive System (Current):**

```
Client → FastAPI → Postgres

Every redirect:
SELECT long_url FROM urls WHERE short_code = ?
```

Even with an index → DB still gets hammered.

---

**Correct Design (With Cache):**

```
Client → FastAPI → Cache (Redis) → DB (fallback)
```

**Read Path — Step by Step:**

```
1. Request comes in → /abc123
2. Check Redis
   ├── HIT  → return long_url (FAST)
   └── MISS → query DB
                  ↓
              store in Redis
                  ↓
              return response
```

**Why this works:**

| Layer | Latency |
|---|---|
| Redis | ~1ms |
| Postgres | 5–20ms |

You just reduced 99% of DB reads → near zero.

---

**But here's where most people fail** — just "adding Redis" is not enough. You must decide:

**1. Cache Strategy**

Use: **Cache-Aside (Lazy Loading)**

Why?

- Simple
- Works well for read-heavy systems
- No unnecessary writes

**2. What to Cache?**

```
key   = short_code
value = long_url
```

Keep it minimal. No overengineering.

**3. TTL (Time To Live)**

- **Option A: No TTL** — URLs rarely change → safe, best performance
- **Option B: TTL (e.g., 1 hour)** — if you want staleness protection

**4. Hot Key Optimization (Advanced Thinking)**

Problem: same key hit thousands of times/sec.

Redis can handle it — but a better approach is:

```
In-process cache (L1) + Redis (L2)
```

---

**Final Architecture (Strong Answer):**

```
Client
  ↓
FastAPI
  ↓
In-Memory Cache (dict, LRU)   ← L1 cache
  ↓
Redis                         ← L2 cache
  ↓
Postgres                      ← Source of truth
```

| Layer | Purpose |
|---|---|
| L1 cache | Ultra-fast (~microseconds) |
| Redis | Distributed cache |
| DB | Durability |

---

**Bottleneck Analysis (Mentor Level):**

- Without cache → DB dies
- With Redis → Redis becomes hotspot (but manageable)
- With L1 + Redis → System becomes very stable

**Strong answer:**

> *"Use cache-aside strategy with Redis, optionally add L1 in-memory cache for hot keys, eliminate most DB reads, and protect Postgres from read-heavy load."*

---

## Q5: What Happens if Redis Goes Down?

**Baseline Answer:** Redis down → fallback to DB

Correct  but incomplete

---

**What Actually Happens (Failure Scenario):**

- Redis crashes
- Traffic is high (read-heavy system)

Without protection:

- Normal: 1M requests/day → mostly cached
- Redis down: **ALL 1M → DB**

**Result:**

- DB connection pool exhausted
- Query latency spikes
- Timeouts
- Full system outage

This is called: **Cache stampede / thundering herd problem.**

---

**Proper Production Thinking:** You need **controlled degradation**, not blind fallback.

---

**Correct Strategy (Layered Defense):**

**1. Fallback to DB — yes, but controlled**

You do fallback — but not blindly.

**2. Add Rate Limiting / Protection**

```
If Redis down:
    limit DB requests to X req/sec
    reject or degrade the rest
```

**3. Use L1 Cache (Your Hidden Savior)**

```
FastAPI → local in-memory cache → Redis → DB
```

If Redis dies → L1 cache still serves hot URLs.

> Even if Redis is down, you still survive — because hot keys are cached locally.

**4. Graceful Degradation**

Instead of crashing:

- Return cached (even slightly stale) data
- OR return limited service

**5. Circuit Breaker Pattern (Advanced)**

If Redis is failing → stop hitting Redis temporarily.

Why?

- Avoid wasting time on failed calls
- Reduce latency spikes

---

**Final Resilient Flow:**

```
Request
  ↓
L1 Cache
  ↓
Redis (if healthy)
  ↓
DB (rate-limited fallback)
```

**Strong answer:**

> *"Fallback to DB with rate limiting, rely on L1 cache for hot keys, and use a circuit breaker to avoid cascading failure."*

**Key Insight:**

> Caching is not just about performance — it's about **protecting your database during failures**.

---

## Q6: A New URL (Never Cached) Suddenly Gets Viral Traffic — What Happens?

*"Store in Redis"* → correct but incomplete again.

The problem here is **not** whether we store it — it's what happens under **concurrency** *before* it gets stored.

---

**Problem Restated:**

```
short.ly/abc123 goes viral
10,000 requests hit at the same time
```

All requests do:

```
Redis → MISS
DB    → query
```

**Result:** 10,000 requests → **10,000 DB queries**

Your DB just got DDoS-ed by your own system.

This is called: **Cache Stampede (cold key problem).**

---

**Correct Solution: Request Coalescing**

You need **request coalescing**, not just caching.

**Solution 1: Cache Lock (Single Flight)**

Idea: Only **one** request fetches from DB. Others wait.

```
1. Request A → cache miss → acquires lock
2. Request B → sees lock  → waits
3. Request C →              waits
   ...
4. Request A → fetches from DB → stores in Redis → releases lock
5. All others → read from Redis 
```

**Result:** 10,000 requests → **1 DB query** 🎉

---

**Production Upgrade:**

Local locks only work per instance.

If you have multiple servers → use a **distributed lock (Redis SETNX)**.

---

**Alternative Strategy: Pre-warm Cache (If Predictable)**

If you know trending URLs or popular links in advance → load them into Redis beforehand.

**Even More Advanced (Awareness Only):**

- Request batching
- Async write-through
- CDN caching (edge-level)

---

**Strong answer:**

> *"Use cache-aside with request coalescing (lock) to prevent cache stampede, so only one DB query happens for a cold but hot key."*

**References:**

- <https://lahin31.github.io/system-design-bangla/>
- <https://github.com/karanpratapsingh/system-design>
- <https://github.com/donnemartin/system-design-primer>
- <https://www.geeksforgeeks.org/system-design/system-design-tutorial/>
- LLM
