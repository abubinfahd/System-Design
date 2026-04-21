# System Design: URL Shortener

A structured walkthrough of URL shortener system design, from back-of-the-envelope estimation through architecture decisions. Built as a learning progression from first principles.

---

## Table of Contents

- [Overview](#overview)
- [Milestone 2: Back-of-the-Envelope Estimation](#milestone-2-back-of-the-envelope-estimation)
  - [Step 1: Define Assumptions](#step-1-define-assumptions)
  - [Step 2: Convert to RPS](#step-2-convert-to-rps)
  - [Step 3: Peak vs Average Traffic](#step-3-peak-vs-average-traffic)
  - [Step 4: Architecture Decision at Current Scale](#step-4-architecture-decision-at-current-scale)
- [Scaling Analysis: 1M Redirects/Day](#scaling-analysis-1m-redirectsday)
- [Architecture Evolution](#architecture-evolution)
- [Key Engineering Principles](#key-engineering-principles)

---

## Overview

This document walks through the estimation and architecture reasoning behind a URL shortener service. The primary goal is not to build a perfect system on day one, but to understand system behavior through estimation, identify bottlenecks before they occur, and make architecture decisions that are justified by data, not intuition.

> Estimation is not about accuracy. It is about understanding system behavior.

---

## Milestone 2: Back-of-the-Envelope Estimation

### Step 1: Define Assumptions

Start with baseline traffic assumptions. These numbers are deliberately rough — order of magnitude matters more than precision.

```
10,000 new URLs created per day    (writes)
100,000 redirects per day          (reads)
```

The read-to-write ratio here is 10:1. In practice, a URL shortener skews even higher — a single URL may be clicked 50 to 1,000 times after being created once. A ratio of 100:1 is more realistic for production systems.

**Engineering rule:** Always overestimate reads in consumer-facing systems. This makes your design safer under real load conditions.

---

### Step 2: Convert to RPS

```
1 day = 86,400 seconds
```

**Writes per second (WPS)**

```
10,000 / 86,400 = ~0.12 WPS
```

Approximately 1 write every 8 seconds.

**Reads per second (RPS)**

```
100,000 / 86,400 = ~1.15 RPS
```

These numbers look small. That is intentional. The next step is where most engineers make mistakes.

---

### Step 3: Peak vs Average Traffic

Average RPS is not the number you design for. Traffic is not uniformly distributed.

```
Average RPS:  ~1 request/sec
Peak RPS:     10 to 50 requests/sec
```

**Why peaks occur:**
- Users click shared links at the same time
- Viral content causes sudden traffic spikes
- Time-based clustering (morning, lunch, news cycles)

**Rule of thumb:**

```
Peak RPS = 10x average
```

Applied to this system:

```
Peak reads:   10 to 20 RPS
Peak writes:  ~1 RPS
```

> Systems fail at peak, not at average. Design for peak.

---

### Step 4: Architecture Decision at Current Scale

At 20 RPS peak, consider whether a cache layer is necessary.

| Component | Throughput Capacity       |
|-----------|---------------------------|
| Postgres  | 1,000+ simple queries/sec |
| Redis     | 100,000+ ops/sec          |

At this traffic level, Postgres alone is sufficient. Adding Redis at this stage introduces complexity without a proportional benefit. This is overengineering.

> Do not add complexity before it is justified by load.

---

## Scaling Analysis: 1M Redirects/Day

When traffic grows to 1 million redirects per day, the numbers change:

```
1,000,000 / 86,400 = ~11.6 RPS (average)
Peak (x10) = ~100 to 120 RPS
```

At this scale, Postgres can still handle the query volume. The reason to introduce Redis is not that the database fails — it is that the system can be made faster, safer, and more resilient.

### Why Redis at This Stage

**1. Latency reduction (primary reason)**

| Layer    | Typical Latency |
|----------|-----------------|
| Redis    | ~1 ms           |
| Postgres | 5 to 20 ms      |

Redirect speed is latency-critical. A faster redirect directly improves user experience.

**2. Protection against burst traffic**

Average traffic of 100 RPS can spike to 2,000 RPS during a viral event. Postgres handles steady load well but is vulnerable to sudden connection surges.

**3. Hot key efficiency**

In most URL shorteners, roughly 80% of traffic hits 10% of URLs. Without caching, the database receives repeated identical queries for the same rows. Redis handles this at the cache layer, with a single database hit serving millions of subsequent requests.

**4. Connection pool preservation**

Each request to Postgres opens a database connection. Connection pools typically cap at 100 to 500 connections. At high concurrency, the pool exhausts before CPU or query time becomes the bottleneck. Redis reduces database hits and therefore reduces active connections.

**Correct reasoning for adding Redis:**

> "At 1M requests per day, Postgres can still handle the load. Redis is introduced to reduce redirect latency, absorb traffic spikes, protect the connection pool, and handle hot keys efficiently. This delays the need for database scaling and improves system reliability under burst conditions."

---

## Architecture Evolution

**Stage 1 — Initial build (current scale)**

```
Client → FastAPI → Postgres
```

**Stage 2 — After 1M redirects/day**

```
Client → FastAPI → Redis → Postgres
```

**Stage 3 — Future scale**

```
Client → FastAPI → L1 In-Process Cache → Redis → Read Replica → Primary DB
```

Each stage adds complexity only when the previous stage shows measurable strain. This is progressive scaling.

---

## Key Engineering Principles

**Estimation before architecture**

You will never have real data at the beginning of a new system. Estimates are not guesses — they are the basis for architecture decisions. Skipping estimation means guessing architecture, not designing it.

**Order of magnitude over precision**

The difference between 100K and 200K requests per day should not change your architecture. If it does, the design is fragile. What matters is whether you are at 10^3, 10^5, or 10^7 requests per day.

**Peak matters more than average**

Design your system to survive its worst moment, not its typical moment.

**Progressive scaling**

| Scale         | Architecture               |
|---------------|----------------------------|
| Small         | Postgres only              |
| Medium        | Add Redis                  |
| Large         | Redis + read replicas + sharding |

Do not build for large scale on day one. Build for the next stage, not three stages ahead.

**Measure after building**

Phase 1: Use rough estimates, design the system, identify bottlenecks on paper.  
Phase 2: Build, measure real traffic, tune based on actual data.

Estimation drives the design. Real data refines it.

---

## Next

Milestone 3: API Design — defining production-grade endpoints, request/response contracts, and error handling patterns.
