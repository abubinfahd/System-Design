# Milestone 6: Scaling Strategy (Deep Dive)

Up until now, your system has had one API server, one database, and one Redis instance. That architecture is clean, simple, and completely correct for early stage traffic. Do not let anyone tell you otherwise.

But eventually something changes. Traffic grows. Users multiply. Concurrent requests pile up. And suddenly one machine is not enough.

This is the milestone where your system stops being an "app" and becomes a distributed system. That shift comes with real complexity — but also with clear, learnable patterns. Let us walk through them properly.

---

## Table of Contents

- [Step 1: Ask What Is Actually Overloaded](#step-1-ask-what-is-actually-overloaded)
- [Step 2: Vertical vs Horizontal Scaling](#step-2-vertical-vs-horizontal-scaling)
- [Step 3: Stateless API Design](#step-3-stateless-api-design)
- [Step 4: Load Balancing](#step-4-load-balancing)
- [Step 5: Scaling the API Layer](#step-5-scaling-the-api-layer)
- [Step 6: Scaling Redis](#step-6-scaling-redis)
- [Step 7: Scaling the Database — Read Replicas](#step-7-scaling-the-database--read-replicas)
- [Step 8: Scaling Writes — Sharding](#step-8-scaling-writes--sharding)
- [Step 9: Hotspot Traffic](#step-9-hotspot-traffic)
- [Step 10: Failure Strategy](#step-10-failure-strategy)
- [Final Architecture](#final-architecture)
- [Common Junior Mistakes](#common-junior-mistakes)
- [Final Mental Model](#final-mental-model)

---

## Step 1: Ask What Is Actually Overloaded

Before you scale anything, ask this question:

> What is actually the bottleneck?

This sounds obvious. It is not. Engineers regularly throw more servers at a problem that is not caused by compute. More API servers do not help if your database is choking. More database replicas do not help if your Redis is the hotspot. More Redis instances do not help if your application code is blocking on a CPU-bound task.

The bottleneck could be any of these:

| Resource | Symptoms |
|----------|----------|
| CPU | High processor usage, slow response times under load |
| Memory | OOM kills, swap usage climbing, processes being killed |
| DB connections | "too many connections" errors, connection pool timeouts |
| Disk I/O | Slow queries despite good indexes, high disk wait time |
| Network | High latency between services, packet loss |

**Measure first. Scale second.** Adding complexity before you understand the bottleneck is how you end up with a distributed system that is slower than the single server it replaced.

---

## Step 2: Vertical vs Horizontal Scaling

Once you know what is overloaded, you have two levers.

### Vertical Scaling (Scale Up)

Make the existing machine more powerful.

```
Before:  2 CPU cores,  4GB RAM
After:   16 CPU cores, 64GB RAM
```

**Pros:**
- No architecture changes — your code does not need to change
- Simple to reason about — one machine, one process
- Fast to implement — usually just a few clicks in your cloud provider

**Cons:**
- Hardware ceiling — you cannot add infinite CPU and RAM to one machine
- Expensive at the high end — the jump from a $200/month server to a $2000/month server is not linear in performance
- Single point of failure — when this machine goes down, everything goes down

### Horizontal Scaling (Scale Out)

Add more machines running the same software.

```
Before:  1 API server
After:   10 API servers behind a load balancer
```

**Pros:**
- No hardware ceiling — keep adding machines as traffic grows
- Fault tolerant — when one machine dies, the others keep running
- Cheaper long-term — many small machines often cost less than one giant machine

**Cons:**
- Distributed system complexity — now you have to coordinate multiple machines
- Load balancing required — traffic needs to be distributed intelligently
- Stateless requirement — more on this below

### The Rule

> Scale vertically first. Scale horizontally when vertical scaling either becomes too expensive or you need fault tolerance.

A surprising amount of real-world traffic can be handled by a single well-tuned machine. Do not prematurely distribute your system. Complexity has a cost.

---

## Step 3: Stateless API Design

Horizontal scaling only works if your API servers are stateless. This is not optional — it is the prerequisite.

### What "stateful" means and why it breaks scaling

Imagine your API server stores user session data in memory:

```python
# Stateful — session lives inside server memory
sessions = {}

@app.post("/login")
def login(user_id: str):
    sessions[user_id] = {"logged_in": True}
    return {"status": "ok"}

@app.get("/dashboard")
def dashboard(user_id: str):
    if user_id not in sessions:      # only works on the same server!
        raise HTTPException(401)
    return {"data": "..."}
```

If the user logs in on Server A, their session lives in Server A's memory. The next request goes to Server B (because the load balancer round-robins). Server B has no session for this user. The user gets a 401.

To fix this you need "sticky sessions" — forcing every request from one user to go to the same server. Now your load balancer is doing extra work, and if Server A dies, all its users lose their sessions.

### What "stateless" means

Every server is identical. No server stores anything that another server does not have access to. All state lives outside the server — in the database, in Redis, or in the request itself (like a JWT token).

```python
# Stateless — session lives in Redis, not in server memory
@app.post("/login")
def login(user_id: str):
    token = generate_jwt(user_id)    # stateless token, no server memory
    return {"token": token}

@app.get("/dashboard")
def dashboard(token: str = Header()):
    user = verify_jwt(token)         # works on any server
    return {"data": db.fetch(user.id)}
```

Now any request can go to any server. Scale from 1 to 100 servers without changing a line of code.

For our URL shortener specifically: the API already does this correctly. Short code lookups go to Redis or Postgres. No per-request state lives on the API server. Every instance is interchangeable.

> Statelessness is what makes horizontal scaling possible. Build for it from day one.

---

## Step 4: Load Balancing

Once you have multiple API servers, you need something to distribute traffic between them. That is the load balancer.

### Architecture

```
Users
  ↓
Load Balancer  ← single entry point for all traffic
  ↓
┌──────────┬──────────┬──────────┐
│ Server A │ Server B │ Server C │
└──────────┴──────────┴──────────┘
```

The load balancer sits in front of all your API servers. Users talk to the load balancer. The load balancer decides which server handles each request.

### What a load balancer actually does

- Distributes incoming requests across healthy servers
- Health checks servers and stops sending traffic to ones that are down
- Can terminate TLS (HTTPS) so your API servers handle plain HTTP internally
- Can provide rate limiting, request logging, and basic DDoS protection

### Load balancing algorithms

| Algorithm | How it works | Best for |
|-----------|-------------|----------|
| Round robin | Req 1 → A, Req 2 → B, Req 3 → C, repeat | Simple, even load |
| Least connections | Send to whichever server has fewest active connections | Uneven request durations |
| IP hash | Hash client IP to determine server | Sticky sessions (avoid if possible) |
| Random | Pick a server at random | Surprisingly effective |

**Recommended: least connections.**

Round robin works well when all requests take the same time. But redirects are fast (1ms) while analytics queries might take 50ms. If Server A gets 10 analytics requests, round robin keeps sending it traffic while it is already busy. Least connections naturally routes to the server that finishes work fastest.

### In practice

Cloud providers give you a managed load balancer out of the box:

- AWS: Application Load Balancer (ALB)
- GCP: Cloud Load Balancing
- DigitalOcean: Load Balancers

You do not need to run your own. Pick the managed option, point it at your API servers, and it handles health checks and distribution automatically.

---

## Step 5: Scaling the API Layer

Your FastAPI application is stateless and sits behind a load balancer. Scaling it is straightforward.

### The problem at high concurrency

A single FastAPI process has limits:

- CPU can only process one thing at a time per core
- Each concurrent request consumes memory
- Connection handling has a ceiling

### The solution: run multiple instances

```
Load Balancer
  ↓
FastAPI instance 1   (Docker container or process)
FastAPI instance 2
FastAPI instance 3
...
FastAPI instance N
```

Each instance is identical. Together they handle N times the concurrency of a single instance.

### How to run multiple instances

**With Docker and a process manager:**

```bash
# Run 4 FastAPI workers on the same machine
gunicorn main:app \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000
```

**With Kubernetes:**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: url-shortener-api
spec:
  replicas: 10          # 10 identical API pods
  selector:
    matchLabels:
      app: url-shortener
  template:
    spec:
      containers:
      - name: api
        image: your-api-image:latest
        ports:
        - containerPort: 8000
```

Change `replicas: 10` to `replicas: 50` and Kubernetes spins up 40 more instances. Scale back down during off-peak hours to save cost.

Because the API is stateless, you can scale it up or down at any time without coordination between instances.

---

## Step 6: Scaling Redis

At high enough traffic, even Redis can become a bottleneck. A single Redis instance handles roughly 100,000 operations per second — which is a lot, but not unlimited.

### Option 1: Redis Replica (for read scaling)

```
Primary Redis (handles writes)
  ↓
Replica Redis 1 (read only)
Replica Redis 2 (read only)
```

Your API reads from replicas and writes to the primary. This roughly doubles or triples your read capacity. For a URL shortener where redirects are reads, this works well.

```python
# Write to primary
redis_primary.set(short_code, long_url)

# Read from replica (round-robin across replicas)
cached = redis_replica.get(short_code)
```

**Tradeoff:** Replication lag. Writes to the primary take a few milliseconds to propagate to replicas. In that window, a replica might return stale data. For URL redirects this is usually acceptable.

### Option 2: Redis Cluster (for massive scale)

Redis Cluster partitions your keys across multiple shards:

```
Key "abc123" → hash → shard 1
Key "xyz789" → hash → shard 2
Key "def456" → hash → shard 3
```

Each shard handles a fraction of the total keyspace. Total capacity scales linearly with the number of shards.

This adds operational complexity. For most URL shorteners, a single Redis instance with replicas is sufficient until you are handling hundreds of millions of requests per day.

### For your project right now

A single Redis instance is fine. Add replicas when you see Redis CPU or memory becoming the bottleneck. Add clustering only when replicas are not enough. This is the progressive scaling principle from Milestone 2.

---

## Step 7: Scaling the Database — Read Replicas

The database is almost always the first real bottleneck. It handles the heaviest work: durable storage, complex queries, and transaction management. Let us address reads first.

### The problem

Your URL shortener has a heavily read-skewed traffic pattern:

```
1M redirects/day  →  mostly reads
10K new URLs/day  →  mostly writes
```

One Postgres instance handles both. As read traffic grows, it competes with writes for CPU, connection pool slots, and disk I/O.

### The solution: read replicas

A read replica is a copy of the primary database that receives all writes automatically (via replication) and serves reads.

```
Primary Postgres
  ├── receives all writes (INSERT, UPDATE, DELETE)
  ├── replicates changes to replicas
  └──
Read Replica 1
  ├── receives replicated data from primary
  └── serves SELECT queries only

Read Replica 2
  └── same as above
```

### Routing queries correctly

```python
def get_long_url(short_code: str):
    # Redirects are reads — go to replica
    return replica_db.execute(
        "SELECT long_url FROM urls WHERE short_code = :code",
        {"code": short_code}
    ).scalar()

def create_url(long_url: str):
    # Writes always go to primary
    return primary_db.execute(
        "INSERT INTO urls (long_url) VALUES (:url) RETURNING id",
        {"url": long_url}
    ).scalar()
```

This simple routing rule — reads to replicas, writes to primary — can multiply your read capacity by 2x, 3x, or more just by adding replica instances.

### The replication lag problem

Here is the critical tradeoff you must understand.

Replication is asynchronous. When you write to the primary, it takes a small amount of time — usually a few milliseconds, but occasionally seconds under load — for that write to appear on the replica.

**Scenario:**

```
Time 0ms:   User updates abc123 → new URL (written to primary)
Time 2ms:   User clicks the same link
Time 2ms:   Redirect query goes to replica
Time 2ms:   Replica has not received the update yet → returns old URL
Time 50ms:  Replica receives the update
```

The user sees their own update reflected incorrectly for 50ms. This is called **eventual consistency** — the replica will eventually have the correct data, but not immediately.

**How to handle it:**

For most redirects: accept the lag. 50ms of stale data on a URL redirect is invisible to users.

For user-facing operations where the user must see their own update immediately (like updating a URL and verifying it changed): route that specific read to the primary.

```python
def get_url_for_owner(short_code: str, user_id: str):
    # User just updated this — read from primary to avoid replication lag
    return primary_db.execute(
        "SELECT long_url FROM urls WHERE short_code = :code AND user_id = :uid",
        {"code": short_code, "uid": user_id}
    ).scalar()
```

Know when eventual consistency is acceptable and when it is not. Redirect traffic: acceptable. The user reviewing their own profile after updating it: not acceptable.

---

## Step 8: Scaling Writes — Sharding

Read replicas solve the read problem. But what happens when writes become the bottleneck?

For a URL shortener, the most write-heavy table is `click_events`. At 1M+ redirects per day, that is 1M+ inserts per day — growing constantly.

A single primary Postgres can handle tens of thousands of writes per second, so you have significant headroom. But there will come a point where even that is not enough.

### The solution: sharding

Sharding splits your data horizontally across multiple database instances. Each instance (shard) holds a subset of the rows.

```
hash(short_code) % 3 = 0  →  Shard 1 (owns abc*, def*, ...)
hash(short_code) % 3 = 1  →  Shard 2 (owns ghi*, jkl*, ...)
hash(short_code) % 3 = 2  →  Shard 3 (owns xyz*, uvw*, ...)
```

### Why shard by short_code?

Because your hottest query is:

```sql
SELECT long_url FROM urls WHERE short_code = 'abc123';
```

When you shard by `short_code`, every query for a given short code goes to exactly one shard. No cross-shard coordination needed. The application computes `hash(short_code) % N` to determine which shard to query, then talks to that shard directly.

### Why NOT shard by user_id?

Because the redirect does not use `user_id`. If you sharded by `user_id`, a redirect for `abc123` might require checking which user owns it, then going to that user's shard. That is two queries and coordination logic that serves no benefit.

> Always shard by the key your hottest query filters on.

### Sharding adds serious complexity

Before sharding, consider these costs:

- Cross-shard queries become very expensive (e.g., analytics across all URLs)
- Rebalancing shards when you add more is painful
- Transactions that span multiple shards require distributed transaction protocols
- Schema changes must be applied to every shard

Add sharding only when read replicas and vertical scaling are genuinely insufficient. Most applications never need it.

---

## Step 9: Hotspot Traffic

Sharding distributes data evenly on average. But one viral URL can break that assumption entirely.

### The scenario

```
User shares: https://short.ly/abc123

Post goes viral on social media.
500,000 clicks in one hour.
All of them → same short code → same shard → same DB row.
```

Even with 10 shards, this URL's traffic all goes to Shard 1. Shard 1 is overloaded. The other 9 shards are idle.

### Solutions

**1. Redis (primary defense)**

The redirect path hits Redis first. If `abc123` is cached, Postgres is never touched regardless of traffic volume. This is why the Redis layer is not optional at scale — it is your main defense against hotspot traffic.

**2. Local in-process cache (for extreme hotspots)**

As discussed in Milestone 5, an L1 cache on each API server means even Redis is bypassed for the most popular codes. 500,000 requests per hour can be served from in-process memory on your API fleet.

**3. CDN (for static redirect targets)**

A CDN like Cloudflare can cache the redirect response itself at edge nodes worldwide. The request never reaches your infrastructure at all for cached short codes.

**4. Request buffering**

For analytics writes specifically: buffer click events in memory or a queue (like Kafka or SQS), then batch-insert them to Postgres. Instead of 10,000 individual inserts for one viral URL, you do one batch insert of 10,000 rows. Much less DB pressure.

---

## Step 10: Failure Strategy

At the scale where you have multiple servers, failures become a regular occurrence — not edge cases. Design for them explicitly.

### API server failure

```
Server B goes down
  ↓
Load balancer health check detects failure (usually within 5-30 seconds)
  ↓
Load balancer stops sending traffic to Server B
  ↓
Traffic redistributed to Server A and Server C
  ↓
Users experience no outage (maybe a few failed requests during detection window)
```

This is why you run at least 3 API instances in production, not 2. With 2, losing one means the other carries 100% of load. With 3, losing one means each remaining server takes 50% more — usually manageable.

### Redis failure

As covered in Milestone 5: catch the exception, log it, fall back to Postgres. The system degrades (slower) but does not fail. Add a circuit breaker to prevent Postgres from being overwhelmed.

### Read replica failure

Route queries to another replica or to the primary. Slightly more load on primary, but the system keeps working. Most database connection poolers (PgBouncer, for example) handle this automatically.

### Primary database failure

This is the most serious failure. Postgres has a failover mechanism:

```
Primary DB goes down
  ↓
Replication monitor detects failure
  ↓
A replica is promoted to primary
  ↓
Application connection strings update (or point to a virtual IP)
  ↓
System resumes with the promoted replica as new primary
```

Failover typically takes 30–60 seconds in a well-configured setup. Some writes during that window may be lost if they had not yet replicated. This is the durability tradeoff of asynchronous replication.

Managed database services (AWS RDS, Google Cloud SQL) handle this automatically. Running your own Postgres failover is operationally complex and generally not worth it unless you have specific requirements.

---

## Final Architecture

Here is what the system looks like with all scaling layers in place:

```
                    Users
                      ↓
              [ Load Balancer ]
                      ↓
     ┌────────────────────────────────┐
     │        FastAPI Servers         │
     │  (stateless, N instances)      │
     └────────────────┬───────────────┘
                      ↓
              [ Redis Cluster ]
              (L2 cache layer)
                      ↓
     ┌────────────────────────────────┐
     │       Primary Postgres         │
     │   (writes: INSERT, UPDATE)     │
     └────────────────┬───────────────┘
                      ↓
     ┌────────────────────────────────┐
     │       Read Replicas            │
     │  (reads: SELECT for redirects) │
     └────────────────────────────────┘
```

**Request flow for a redirect:**

```
1. Request hits load balancer
2. Routed to least-loaded FastAPI instance
3. FastAPI checks L1 in-process cache → hit: done in ~0.01ms
4. L1 miss → check Redis → hit: done in ~1ms
5. Redis miss → query read replica → store in Redis → return in ~10ms
6. Async: insert click event into click_events (non-blocking)
```

**Request flow for URL creation:**

```
1. Request hits load balancer
2. Routed to a FastAPI instance
3. FastAPI validates input
4. Writes to primary Postgres in a transaction
5. Returns new short URL
```

---

## Common Junior Mistakes

| Mistake | Why it hurts |
|---------|-------------|
| Jumping straight to sharding | Adds massive complexity before it is needed. Read replicas and vertical scaling solve most problems first. |
| Stateful API servers | Impossible to load balance correctly. Breaks horizontal scaling. |
| No load balancer | Single server becomes a traffic ceiling and a single point of failure. |
| Ignoring replication lag | Users see stale data after updates. Especially bad for user-facing operations. |
| Scaling the wrong layer | Adding API servers when the DB is the bottleneck does nothing useful. Measure first. |
| Over-engineering early | A single Postgres instance handles millions of users. Do not add distributed complexity until you feel the pain. |

---

## Final Mental Model

```
Horizontal scaling works only when:

  1. The system is stateless
     → any request can go to any server

  2. Traffic can be distributed
     → a load balancer routes intelligently

  3. Bottlenecks are isolated and addressed in order
     → API → Redis → DB reads → DB writes
```

Scale one layer at a time. Measure between each change. Add complexity only when simpler options are exhausted.

---

## Final Deep Question

Before moving to Milestone 7, think through this scenario:

> The primary Postgres is updated successfully with a new destination for `abc123`.
> A user requests that redirect 10 milliseconds later.
> The query goes to a read replica.
> The replica has not yet received the update.
> The user gets the old redirect.

What are the ways you can handle this? Think about:

- Can you route this specific read to the primary? When is that worth the cost?
- Can you use Redis to bridge the gap? What would that require?
- Is "the user gets old data for 50ms" actually a problem for a URL shortener, or is it acceptable?
- What if the URL was never meant to change? Does your system even need URL updates?

There is no single right answer. The goal is to reason through the tradeoffs explicitly rather than picking a solution by intuition.

---

## Next

Milestone 7: Observability — logging, metrics, distributed tracing, and how you know your system is actually working the way you think it is.
