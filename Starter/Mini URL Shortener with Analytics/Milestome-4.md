# Milestone 4: Database Design (Deep Dive)

This is where most backend engineers make mistakes. Getting the schema wrong early means painful rewrites later. Getting the indexing wrong means your system crawls under load. Getting the write strategy wrong means your DB locks up at the worst moment.

We will walk through this layer by layer, with the reasoning behind every decision — not just the answer, but the "why" that makes it stick.

---

## Table of Contents

- [Step 1: Query-Driven Design](#step-1-query-driven-design)
- [Step 2: Schema Design](#step-2-schema-design)
- [Step 3: Indexing Strategy](#step-3-indexing-strategy)
- [Step 4: Transaction Design](#step-4-transaction-design)
- [Step 5: Write Scaling for Clicks](#step-5-write-scaling-for-clicks)
- [Step 6: Aggregation Strategy](#step-6-aggregation-strategy)
- [Step 7: Partitioning for Growth](#step-7-partitioning-for-growth)
- [Step 8: Data Lifecycle Management](#step-8-data-lifecycle-management)
- [Step 9: Final Architecture Picture](#step-9-final-architecture-picture)
- [Key Mental Models](#key-mental-models)

---

## Step 1: Query-Driven Design

Here is a rule that will serve you throughout your career:

> Design your schema around your queries, not the other way around.

Most people design a schema first, then figure out the queries. That is backwards. Your queries are the truth — they tell you what data you need, how fast you need it, and how often it will be hit.

So before touching any table definition, write out the exact queries your system will run.

---

### The 4 Core Queries

**Q1 — Redirect lookup (the hot path)**

```sql
SELECT long_url
FROM urls
WHERE short_code = 'abc123';
```

This runs on every single click. If someone shares a link that goes viral, this query might run 10,000 times in a minute. It must be instant.

**Q2 — Create a short URL**

```sql
INSERT INTO urls (long_url) VALUES ('https://example.com/very-long-path');
UPDATE urls SET short_code = 'abc123' WHERE id = 125;
```

This runs when a user creates a new short link. It is low frequency — maybe a few times per second at most. Not your bottleneck.

**Q3 — Track a click**

```sql
INSERT INTO click_events (short_code) VALUES ('abc123');
```

Every redirect also generates this write. So this runs at the same frequency as Q1 — potentially thousands of times per minute. Write throughput matters here.

**Q4 — Analytics lookup**

```sql
SELECT click_count
FROM urls
WHERE short_code = 'abc123';
```

Notice this reads a pre-computed `click_count` column, not a `COUNT(*)` over millions of rows. That design choice is deliberate and important — we will come back to it.

---

### What These Queries Tell You

| Query | Table | Pattern |
|-------|-------|---------|
| Redirect | urls | Hot read — must be indexed |
| Create URL | urls | Low frequency write — no special treatment needed |
| Track click | click_events | High frequency write — needs separate table |
| Analytics | urls | Fast read — pre-computed value |

The `urls` table is read-heavy. The `click_events` table is write-heavy. Knowing this upfront shapes every decision that follows.

---

## Step 2: Schema Design

Now that you know your queries, design tables that serve them efficiently.

### Table 1: urls

```sql
CREATE TABLE urls (
    id          BIGSERIAL PRIMARY KEY,
    short_code  VARCHAR(10) UNIQUE NOT NULL,
    long_url    TEXT NOT NULL,
    click_count BIGINT DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at  TIMESTAMP NULL
);
```

**Why each column exists:**

| Column | Reason |
|--------|--------|
| `id` | Internal auto-increment primary key. Used to generate `short_code` via Base62 encoding. |
| `short_code` | The lookup key for every redirect. Must be unique. Indexed. |
| `long_url` | The destination. Stored as TEXT because URLs can be long. |
| `click_count` | Pre-computed counter for fast analytics. Avoids expensive COUNT queries. |
| `created_at` | Useful for analytics, debugging, and data lifecycle management. |
| `expires_at` | Optional. NULL means the URL never expires. Used to return 410 Gone responses. |

**Why BIGSERIAL for id?**

BIGINT gives you 9.2 quintillion possible values. You will never run out, even at millions of URLs per day.

---

### Table 2: click_events

```sql
CREATE TABLE click_events (
    id          BIGSERIAL PRIMARY KEY,
    short_code  VARCHAR(10) NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Why is this a separate table and not just a counter on urls?**

Great question. Here is the problem with updating `urls` directly on every click:

```sql
-- This runs on every single redirect
UPDATE urls SET click_count = click_count + 1 WHERE short_code = 'abc123';
```

Every UPDATE acquires a row lock. If 500 people click the same link at the same time, 500 requests are queuing to update the same row. This is called write contention, and it will bring your system to its knees under load.

By writing to a separate `click_events` table instead, each insert is independent. No locking, no contention, no bottleneck.

You get:

- High write throughput — inserts are fast and parallel
- No lock contention on the `urls` table
- Flexibility to add more columns later (IP, device, country) without schema changes to your core table
- The ability to replay or re-aggregate analytics from raw events

---

## Step 3: Indexing Strategy

An index is a separate data structure that lets the database find rows without scanning the entire table. Without an index, a query like `WHERE short_code = 'abc123'` scans every single row — O(N). With an index, it jumps directly to the result — O(log N).

Think of it like the index at the back of a textbook. Without it, you read every page. With it, you go straight to the right page.

---

### Index 1: Short Code Lookup (Critical)

```sql
CREATE UNIQUE INDEX idx_urls_short_code ON urls(short_code);
```

**What it does:** Enables instant lookup by `short_code` on every redirect.

**Why UNIQUE?** Because `short_code` must be unique anyway. Declaring it unique at the index level lets Postgres enforce that constraint efficiently without a separate check.

**Without this index:**

```
Table has 10 million URLs.
Query: WHERE short_code = 'abc123'
Postgres scans all 10 million rows. Sequential scan. Slow.
```

**With this index:**

```
Postgres jumps directly to 'abc123' in the B-tree index.
Returns in microseconds regardless of table size.
```

---

### Index 2: Click Events by Short Code

```sql
CREATE INDEX idx_click_events_short_code ON click_events(short_code);
```

**What it does:** Speeds up analytics queries that count clicks per URL.

**Without this index:**

```sql
-- Scans all 100 million click_events rows to find rows for 'abc123'
SELECT COUNT(*) FROM click_events WHERE short_code = 'abc123';
```

**With this index:**

```sql
-- Jumps directly to the relevant rows
-- Still slow for COUNT at 100M rows, but much better
```

**Important:** An index speeds up lookup, not aggregation. Even with an index, `COUNT(*)` over millions of rows is expensive. This is why we pre-compute `click_count` in the `urls` table instead of counting raw events at query time.

---

### Index 3: Click Events by Time (for partitioning)

```sql
CREATE INDEX idx_click_events_created_at ON click_events(created_at);
```

**What it does:** Supports time-range queries and makes partitioning work efficiently. More on this in Step 7.

---

## Step 4: Transaction Design

You already learned this in Milestone 3, but it is worth reinforcing here at the DB level.

The problem: you insert a URL row to get an ID, then encode that ID to generate a short code, then update the row. If a crash happens between insert and update, you get a row with `short_code = NULL` — broken data.

The solution: wrap both operations in a transaction.

```sql
BEGIN;

INSERT INTO urls (long_url)
VALUES ('https://example.com/very-long-path')
RETURNING id;

-- In application: short_code = encode_base62(id)

UPDATE urls
SET short_code = 'cb'
WHERE id = 125;

COMMIT;
```

If anything fails before `COMMIT`, Postgres automatically rolls back. The row disappears. No broken state.

**In Python with SQLAlchemy:**

```python
def create_url(session, long_url: str):
    try:
        new_url = URL(long_url=long_url)
        session.add(new_url)
        session.flush()                          # gets ID, no commit yet

        short_code = encode_base62(new_url.id)
        new_url.short_code = short_code

        session.commit()                         # both changes land together
        return short_code

    except Exception:
        session.rollback()                       # nothing lands if anything fails
        raise
```

`flush()` is the key here. It sends the INSERT to the DB and gets back the ID, but does not commit. The transaction is still open. Only when everything is ready does `commit()` finalize both the INSERT and the UPDATE atomically.

---

## Step 5: Write Scaling for Clicks

This is one of the most common interview questions and one of the most common production mistakes.

### The Wrong Approach

```sql
-- Runs on every redirect
UPDATE urls
SET click_count = click_count + 1
WHERE short_code = 'abc123';
```

Imagine a link goes viral. 1,000 people click it within the same second. Every single click tries to update the same row. Postgres puts a row-level lock on that row for each update. The result:

```
Request 1  → locks row → updates → releases
Request 2  → waiting...
Request 3  → waiting...
...
Request 1000 → still waiting
```

Response times spike. Your system feels frozen. This is write contention.

### The Correct Approach: Hybrid Write Pattern

```
On every redirect:
  1. Serve the redirect (fast, from Redis)
  2. Insert into click_events (fast, no lock)
  3. Background job periodically updates click_count
```

**Step 1 — Insert click event (non-blocking)**

```python
def record_click(short_code: str):
    db.execute(
        "INSERT INTO click_events (short_code) VALUES (:code)",
        {"code": short_code}
    )
```

Each insert is independent. 1,000 concurrent inserts do not block each other. Fast.

**Step 2 — Background aggregation job**

```python
# Runs every 60 seconds
def aggregate_clicks():
    result = db.execute("""
        SELECT short_code, COUNT(*) as new_clicks
        FROM click_events
        WHERE processed = FALSE
        GROUP BY short_code
    """)

    for row in result:
        db.execute("""
            UPDATE urls
            SET click_count = click_count + :new_clicks
            WHERE short_code = :code
        """, {"new_clicks": row.new_clicks, "code": row.short_code})

        db.execute("""
            UPDATE click_events
            SET processed = TRUE
            WHERE short_code = :code
        """, {"code": row.short_code})
```

The heavy aggregation happens asynchronously, off the critical path. Your redirect endpoint never touches the `urls` table for click counting.

**Result:**

| Approach | Behavior under 1000 concurrent clicks |
|----------|---------------------------------------|
| Direct UPDATE | Row lock contention, slow, may timeout |
| Insert + background job | Each insert is independent, no contention, fast |

---

## Step 6: Aggregation Strategy

When you need the click count for analytics, you have two choices.

### Option 1: Count raw events (avoid this)

```sql
SELECT COUNT(*) FROM click_events WHERE short_code = 'abc123';
```

If this URL has been clicked 5 million times, this query scans 5 million rows. Even with an index it is slow, and it gets slower every day as the table grows.

### Option 2: Read pre-computed count (do this)

```sql
SELECT click_count FROM urls WHERE short_code = 'abc123';
```

One row, one indexed lookup, returns instantly. The background job in Step 5 keeps this value up to date.

**The tradeoff:** The count may be a few minutes behind real-time. For most analytics use cases, this is completely acceptable. If a user needs real-time accuracy within seconds, you can query both:

```sql
SELECT u.click_count + COALESCE(r.recent, 0) AS total_clicks
FROM urls u
LEFT JOIN (
    SELECT short_code, COUNT(*) AS recent
    FROM click_events
    WHERE short_code = 'abc123' AND processed = FALSE
    GROUP BY short_code
) r ON u.short_code = r.short_code
WHERE u.short_code = 'abc123';
```

This gives you the pre-computed total plus any clicks not yet aggregated. Best of both worlds, used only when needed.

---

## Step 7: Partitioning for Growth

Here is the long-term problem. Your `click_events` table grows by millions of rows per day. After one year:

```
1M clicks/day × 365 days = 365 million rows
```

At this scale:
- Indexes become huge and slower to traverse
- Queries that filter by date scan far more data than needed
- Deleting old data (e.g., older than 1 year) requires a full table scan

**The solution: table partitioning by time.**

Partitioning splits one logical table into multiple physical tables behind the scenes. Each partition holds data for a specific time range.

```sql
-- Parent table (logical)
CREATE TABLE click_events (
    id          BIGSERIAL,
    short_code  VARCHAR(10) NOT NULL,
    created_at  TIMESTAMP NOT NULL
) PARTITION BY RANGE (created_at);

-- April 2026 partition
CREATE TABLE click_events_2026_04
    PARTITION OF click_events
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');

-- May 2026 partition
CREATE TABLE click_events_2026_05
    PARTITION OF click_events
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
```

**What you gain:**

| Benefit | Explanation |
|---------|-------------|
| Smaller indexes | Each partition has its own index, covering only that month's data |
| Faster queries | A date-range query only scans the relevant partition |
| Easy deletion | Drop an old partition with one command — instant, no full scan |
| Better maintenance | VACUUM and ANALYZE run on smaller chunks |

**Deleting old data is now trivial:**

```sql
-- Drop all data older than 1 year — instant operation
DROP TABLE click_events_2025_04;
```

Without partitioning, the equivalent DELETE would lock the table for minutes or hours.

---

## Step 8: Data Lifecycle Management

Even with partitioning, keeping years of raw click data in Postgres is expensive. The solution is tiered storage.

```
Hot data (last 90 days)   →  Postgres (fast, expensive)
Warm data (90d - 1 year)  →  Postgres, less frequently queried
Cold data (1 year+)       →  Archive to S3 (cheap, slow)
```

**Archival flow:**

```python
def archive_old_partitions():
    # Export old partition to S3 as compressed Parquet
    export_to_s3("click_events_2024_01", "s3://your-bucket/archive/")

    # Drop the partition from Postgres
    db.execute("DROP TABLE click_events_2024_01")
```

If you ever need to query archived data (for example, year-over-year analytics), you can use AWS Athena to query Parquet files directly from S3 without loading them back into Postgres.

This pattern is used by every large data team. Postgres for operational data, S3 + Athena for historical analysis.

---

## Step 9: Final Architecture Picture

Here is what the complete system looks like with all the pieces in place:

```
           Client (browser / app)
                   |
                   v
               FastAPI
              /       \
             /         \
          Redis       Postgres
      (redirect        /     \
        cache)      urls    click_events
                  (reads)   (writes)
                              |
                        Background Job
                        (aggregation)
                              |
                          S3 Archive
                        (old partitions)
```

**Traffic flow for a redirect:**

```
1. Client hits GET /abc123
2. FastAPI checks Redis → cache hit → return long_url instantly
3. If cache miss → query urls table (indexed, fast)
4. Return 302 redirect to client
5. Async: insert into click_events (non-blocking)
6. Background job: aggregate click_events → update urls.click_count
```

**Traffic flow for URL creation:**

```
1. Client hits POST /v1/urls
2. FastAPI validates input
3. BEGIN TRANSACTION
4. INSERT into urls → get id
5. encode_base62(id) → short_code
6. UPDATE urls SET short_code
7. COMMIT
8. Return short_url to client
```

---

## Key Mental Models

**Design from queries, not from intuition**

Every table, every column, every index should exist because a specific query needs it. If you cannot point to a query that justifies a design choice, reconsider it.

**Separate your hot read table from your hot write table**

`urls` is read millions of times per day. `click_events` is written millions of times per day. Keeping them separate means neither operation interferes with the other.

**Pre-compute what you read frequently**

`click_count` on the `urls` table is an example of a materialized value — computed in the background, read instantly. This pattern appears everywhere in large systems. Do not aggregate at read time when you can aggregate at write time.

**Indexes make lookups fast, not aggregations**

An index on `short_code` makes `WHERE short_code = 'abc123'` instant. It does not make `COUNT(*)` fast. Know which problem each tool solves.

**Partitioning is a maintenance strategy, not just a performance one**

Dropping a partition is instant. Deleting rows from a large table is slow. If your data has a natural time dimension and you know you will need to delete old data, partition from the start.

**Transactions are your safety net, not your solution**

Transactions prevent partial writes. They do not fix bad schema design, bad query patterns, or write contention. Use them for atomicity, but do not rely on them to compensate for design problems elsewhere.

---

## Final Deep Check

Before moving to Milestone 5, sit with this question:

> Why do we not store `long_url` in Redis permanently and skip the database entirely?

Think about it from three angles:

- **Durability** — what happens to Redis data if the server restarts?
- **Consistency** — what is the source of truth for your data?
- **System boundaries** — what is Redis designed for, and what is a database designed for?

Answer this, and you will understand exactly why every component in this architecture exists and why none of them can be replaced by the others.

---

## Next

Milestone 5: Caching Strategy — how Redis fits into the redirect path, cache invalidation patterns, TTL design, and what to do when the cache lies to you.
