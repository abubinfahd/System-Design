# 🔗 Milestone 2 — Back-of-the-Envelope Estimation (Done Properly)

---

## Why Back-of-the-Envelope Estimation?

In system design, estimation is not about perfect accuracy. It is about understanding system boundaries, discovering potential bottlenecks before writing code, and justifying architecture decisions with data rather than gut feelings.

---

## Step 1: Define Assumptions

We start by defining baseline traffic expectations. These assumptions are rough orders of magnitude:

- **10,000** new short URLs created per day (Writes)
- **100,000** redirects per day (Reads)

This represents a **10:1 read-to-write ratio**. 

> [!TIP]
> In real production environments, URL shortener traffic is typically much more skewed (often 100:1 or higher), as a single short link shared on social media can get thousands of hits. Always overestimate reads in consumer-facing systems to create a safer buffer.

---

## Step 2: Convert to Requests Per Second (RPS)

To design our servers, we must convert daily volumes into requests per second:
```text
1 day = 86,400 seconds
```

### Writes Per Second (WPS)
```text
10,000 writes / 86,400 seconds ≈ 0.12 WPS (1 write every ~8 seconds)
```

### Reads Per Second (RPS)
```text
100,000 reads / 86,400 seconds ≈ 1.15 RPS
```

At first glance, these numbers seem trivial. However, average traffic is a dangerous metric to design for.

---

## Step 3: Peak vs Average Traffic

Traffic is never distributed evenly throughout the day. It peaks due to time zones, viral posts, and news cycles.

> [!WARNING]
> Systems fail during peak traffic, not average traffic. Always design your infrastructure to survive peak loads.

A standard industry rule of thumb is:
```text
Peak RPS = 10x Average RPS
```

Applying this to our service:
- **Peak Reads (RPS):** ~10 to 20 RPS
- **Peak Writes (WPS):** ~1 WPS

---

## Step 4: Architecture Decision at Current Scale

At a peak redirect rate of 20 RPS, we look at the typical throughput capacity of backend database technologies:

| Component | Throughput Capacity |
| :--- | :--- |
| **PostgreSQL** | 1,000+ simple queries/sec |
| **Redis** | 100,000+ operations/sec |
| **SQLite (Wal Mode)** | 500+ read operations/sec |

### Decision:
At 20 RPS peak, a single relational database (like Postgres or SQLite) is more than sufficient. Adding a Redis cache layer at this stage is **overengineering**. It adds deployment complexity, caching invalidation bugs, and cost without any real benefit.

> Do not add complexity before it is justified by measurable load.

---

## Scaling Analysis: 1M Redirects/Day

When traffic grows to **1,000,000 redirects/day**, the estimations scale up:

```text
Average RPS: 1,000,000 / 86,400 ≈ 11.6 RPS
Peak RPS (10x): ~100 to 120 RPS
```

At 120 RPS peak, a relational database can still handle the raw query load. However, introducing a cache layer (like Redis) becomes justified for engineering reasons beyond just raw capacity.

### Why Introduce Redis at 1M Redirects/Day?

1. **Latency Reduction (User Critical Path)**
   
   | Layer | Typical Latency |
   | :--- | :--- |
   | **Redis (RAM)** | ~1 ms |
   | **PostgreSQL (Disk/Network)** | 5 ms to 20 ms |
   
   Redirect speed directly impacts user experience and SEO. Reducing lookup latency from 15ms to 1ms is a massive win.

2. **Protection Against Burst Spikes**
   A viral social media post can instantly surge traffic from 100 RPS to 2,000+ RPS. While a relational database might throttle or hit connection limits, Redis handles surges effortlessly.

3. **Hot Key Efficiency (The 80/20 Rule)**
   Typically, 80% of click traffic goes to 10% of URLs (e.g., a few popular links). Caching these "hot keys" in Redis prevents the database from reading the exact same disk pages repeatedly.

4. **Connection Pool Preservation**
   Each request to a relational database consumes a connection from the pool (usually capped at 100-500). High concurrency exhausts these connections quickly. Redis prevents lookups from ever reaching the database, freeing connections for writes.

---

## Architecture Evolution

### Stage 1 — Initial Build (Current Scale)
```text
Client ──► FastAPI ──► SQLite / Postgres
```

### Stage 2 — Mid Scale (1M+ Redirects/Day)
```text
Client ──► FastAPI ──► Redis (Cache) ──► Postgres (DB)
```

### Stage 3 — Future Scale (10M+ Redirects/Day)
```text
Client ──► FastAPI ──► L1 In-Process Cache ──► L2 Redis ──► Read Replicas (Reads) / Primary DB (Writes)
```

---

## Key Summary

- **Estimation Drives Design:** Always calculate constraints before writing code. Guessing architecture leads to fragile or overengineered systems.
- **Order of Magnitude over Precision:** The difference between 100K and 150K requests does not change your architecture. The difference between 10K (Postgres) and 1M+ (Redis) does.
- **Progressive Scaling:** Move to the next architectural tier only when metrics show the current system is under strain.

| Scale | Daily Redirects | Target Architecture |
| :--- | :--- | :--- |
| **Small** | < 100,000 | Postgres / Database only |
| **Medium** | 100,000 - 1,000,000 | Add Redis Cache |
| **Large** | 1,000,000+ | Add Local L1 Cache + Read Replicas |

---

## Q&A — Deep Dives

### Q1: Why do we estimate peak traffic instead of designing for average traffic?
**Answer:** Traffic is bursty. Viral events, news cycles, or time-based user activity cause short-duration spikes. If a system is designed for an average of 10 RPS but experiences a peak spike of 100 RPS, the server or database connection pool will exhaust, leading to request timeouts, 503 errors, and service downtime. Always design for peak loads to ensure system availability.

### Q2: Why is the read-to-write ratio so critical for database selection?
**Answer:** Read operations and write operations have completely different scaling properties. In a read-heavy system (like a URL shortener), we can scale reads almost infinitely by adding cache layers (Redis, Memcached, CDN) and read replicas. In write-heavy systems, we must deal with data durability, transaction locks, and consistency, which require more complex solutions like database sharding or message queues. Knowing that our system is 90%+ reads allows us to optimize the read path aggressively.

### Q3: What is the impact of synchronous write operations on read performance?
**Answer:** When click tracking is done synchronously in the redirect path, every redirect request must write a log to the database before responding to the user. In our Milestone 1 load tests (100 concurrent users, 1000 requests), this synchronous write pattern under SQLite caused average latency to soar to **2,315 ms** due to SQLite's database-level write locks. If click tracking were decoupled asynchronously (e.g., via background tasks or message queues), the database write contention would be removed, bringing the average redirect latency back down to `< 50 ms`.

### Q4: How do we calculate memory requirements for Redis to cache URLs?
**Answer:** 
Let's calculate the memory required to cache 1 million URLs:
- **Key (6-character short code):** 6 bytes
- **Value (Target URL, e.g., 100 characters):** ~100 bytes
- **Metadata/Redis Overhead per key:** ~250 bytes
- **Total per URL:** ~356 bytes
- **For 1 million URLs:** `1,000,000 * 356 bytes ≈ 356 MB`
Even with millions of URLs, the memory footprint fits comfortably within a tiny, inexpensive Redis instance.

### Q5: How do database connection limits affect performance at scale?
**Answer:** Every relational database has a maximum number of concurrent open connections (typically 100 to 500). If our FastAPI application receives 1,000 concurrent redirect requests, and each request tries to open a database connection to query the URL, the connection pool will exhaust. Requests will queue up, waiting for a connection to release, leading to massive latency spikes or timeouts. A cache layer prevents these read requests from ever requesting a database connection, preserving them for write operations.

### Q6: What consistency trade-offs do we introduce when scaling to read replicas?
**Answer:** When scaling a database using read replicas, writes go to the primary node and are asynchronously replicated to read replica nodes. This replication takes time (typically milliseconds to seconds). During this replication lag window, the system is **eventually consistent**. If a user creates a short URL (written to the primary DB) and immediately tries to access it (read from a replica before it replicates), they will receive a `404 Not Found`. We must decide if this temporary inconsistency is acceptable for the trade-off of massive read scalability.
