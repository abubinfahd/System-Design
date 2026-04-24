# Milestone 3: Production-Grade API Design

API design is not just endpoints. It includes correctness, idempotency, validation, failure handling, and scale readiness. An API that works on the happy path but breaks under retry, bad input, or partial writes is not production-grade.

---

## Table of Contents

- [API Design Principles](#api-design-principles)
- [Endpoint 1: Create Short URL](#endpoint-1-create-short-url)
- [Endpoint 2: Redirect](#endpoint-2-redirect)
- [Endpoint 3: Analytics](#endpoint-3-analytics)
- [Error Handling](#error-handling)
- [Rate Limiting](#rate-limiting)
- [Security Basics](#security-basics)
- [API Versioning](#api-versioning)
- [Short Code Generation: Design Decision](#short-code-generation-design-decision)
- [Write Order and Consistency](#write-order-and-consistency)
- [Handling Partial Failures with Transactions](#handling-partial-failures-with-transactions)
- [Key Engineering Principles](#key-engineering-principles)

---

## API Design Principles

Before writing any endpoint, define the rules the entire API must follow.

- APIs must be stateless — no server-side session state, required for horizontal scaling
- APIs must be idempotent where possible — repeating a request must not create duplicate side effects
- APIs must handle invalid input safely — never trust client input
- APIs must support high read traffic efficiently — the redirect path is the hot path and must be treated differently from all other endpoints

---

## Endpoint 1: Create Short URL

```
POST /v1/urls
```

**Request body**

```json
{
  "long_url": "https://example.com/page",
  "custom_alias": "my-link",
  "expires_at": "2026-12-31T00:00:00Z"
}
```

`custom_alias` and `expires_at` are optional.

**Validation rules**

- URL must have a valid scheme and domain
- URL must not exceed 2,048 characters
- `custom_alias` must match `[a-zA-Z0-9_-]` only
- `custom_alias` must be unique across the system

**Response**

```json
{
  "short_code": "abc123",
  "short_url": "https://yourapp.com/abc123",
  "created_at": "2026-04-17T10:00:00Z"
}
```

**Idempotency**

If a client retries due to a network failure, a naive implementation creates a duplicate row. The correct solution is an idempotency key.

The client sends:

```
Idempotency-Key: <unique-request-id>
```

The server stores a hash of the request keyed by that value. On retry, it returns the original response instead of processing again. This prevents duplicate DB rows and inconsistent state.

**FastAPI implementation**

```python
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, HttpUrl

app = FastAPI()


class CreateURLRequest(BaseModel):
    long_url: HttpUrl
    custom_alias: str | None = None


@app.post("/v1/urls")
def create_url(req: CreateURLRequest, idempotency_key: str | None = Header(None)):
    if req.custom_alias and not req.custom_alias.isalnum():
        raise HTTPException(status_code=400, detail="Invalid alias")

    short_code = generate_code()

    return {
        "short_code": short_code,
        "short_url": f"https://app/{short_code}"
    }
```

Pydantic validates `HttpUrl` automatically. The idempotency key is read from the header without boilerplate. The function is stateless and extensible.

---

## Endpoint 2: Redirect

```
GET /{short_code}
```

This is the most critical endpoint in the system. It is called on every link click and handles the majority of total traffic.

**Behavior**

Look up the long URL and return:

```
HTTP 302 Found
Location: https://original-url.com
```

**Edge cases**

| Scenario        | Status Code |
|-----------------|-------------|
| Not found       | 404         |
| Expired URL     | 410 Gone    |
| Malformed input | 400         |

**Performance requirement**

This endpoint must avoid heavy logic, avoid unnecessary DB hits, and be cache-friendly. Every millisecond of added latency is multiplied across millions of requests. The redirect path should hit Redis first and only fall through to Postgres on a cache miss.

---

## Endpoint 3: Analytics

```
GET /v1/urls/{short_code}/analytics
```

**Response**

```json
{
  "short_code": "abc123",
  "total_clicks": 1024,
  "created_at": "2026-04-17T10:00:00Z"
}
```

**Design decision**

Only aggregated counts are returned. Per-user logs, geo data, and device breakdowns are excluded intentionally. This keeps the system simple and avoids storing sensitive user data without a clear requirement. Add granularity only when there is a real product need.

---

## Error Handling

All errors must follow a consistent structure. Inconsistent error formats force clients to write different parsing logic for every failure case.

**Standard error format**

```json
{
  "error": {
    "code": "INVALID_URL",
    "message": "The provided URL is not valid"
  }
}
```

**Error reference**

| Scenario      | Status Code |
|---------------|-------------|
| Invalid URL   | 400         |
| Alias taken   | 409         |
| Not found     | 404         |
| Expired       | 410         |
| Server error  | 500         |

---

## Rate Limiting

Rate limiting prevents abuse and protects the system from spam URL creation.

**Policy**

```
POST /v1/urls   →  10 requests/min per IP
GET /{code}     →  unlimited (served via cache)
```

The redirect endpoint is not rate-limited because it is already protected by caching. Rate limiting it would add latency to the hot path without meaningful protection.

---

## Security Basics

**Input sanitization**

Validate and sanitize all input before processing. Reject URLs with dangerous schemes (e.g., `javascript:`, `data:`). Consider blocking known malicious domains. Optionally enforce HTTPS-only long URLs depending on the product requirement.

**What to prevent**

- Malicious URL injection
- Open redirect abuse
- Enumeration of short codes (mitigated partially by Base62 encoding)

---

## API Versioning

All endpoints are prefixed with `/v1/`. This allows future breaking changes to be introduced under `/v2/` without affecting existing clients. Versioning should be defined from day one — retrofitting it later requires coordinated client migrations.

---

## Short Code Generation: Design Decision

The short code (e.g., `abc123`) can be generated two ways. This is one of the most consequential decisions in the system.

### Option 1: Random String

Generate a random alphanumeric string, check if it exists, retry if it does.

**Pros**
- Simple to implement
- No dependency on DB ID

**Cons**
- Collision risk increases as the table grows
- Each generation requires a DB lookup to verify uniqueness
- Under load: generate, check, retry, check, retry — extra queries on every write
- No ordering — harder to debug and analyze

### Option 2: Auto-Increment ID + Base62 Encoding

Insert the row first to get a guaranteed unique DB ID, then encode that ID into a short alphanumeric string.

Base62 uses the character set: `0-9`, `a-z`, `A-Z` (62 total characters).

**Encoding examples**

| DB ID | Base62 Encoded |
|-------|----------------|
| 1     | "1"            |
| 62    | "10"           |
| 125   | "cb"           |

**Pros**
- No collision possible — DB guarantees ID uniqueness
- Encoding is deterministic — no retries needed
- Faster — no extra DB lookup required
- Compact — shorter than UUID
- Ordered — useful for debugging and analytics

**Cons**
- Predictable — users can increment the code and enumerate URLs
- Mitigated by XOR with a secret if enumeration is a concern

**Comparison**

| Factor          | Random String | Base62 (ID)  |
|-----------------|---------------|--------------|
| Collision risk  | Possible      | None         |
| DB queries      | Extra (retry) | Minimal      |
| Performance     | Worse         | Better       |
| Predictability  | Safe          | Predictable  |

### Recommended Approach

Use auto-increment ID with Base62 encoding. For this system the goals are performance, simplicity, and correctness. Random string generation introduces collision overhead that compounds at scale.

**Implementation**

```python
import string

BASE62 = string.digits + string.ascii_letters


def encode_base62(num: int) -> str:
    if num == 0:
        return BASE62[0]

    base = len(BASE62)
    result = []

    while num:
        num, rem = divmod(num, base)
        result.append(BASE62[rem])

    return ''.join(reversed(result))
```

Time complexity: O(log62 N). No extra DB calls. Deterministic output.

**Optional hardening**

If URL enumeration is a concern, XOR the ID with a secret before encoding:

```python
encoded = encode_base62(id ^ SECRET_SALT)
```

---

## Write Order and Consistency

### The Wrong Approach

```
1. Generate short_code
2. Insert into DB
```

**Problem 1: Collision on generation**

The generated code may already exist. You must check the DB, then retry. Under load this creates a loop of generate, check, retry, check — extra queries on every write.

**Problem 2: Race condition**

Two concurrent requests may generate the same code simultaneously. Both attempt to insert. One fails on the unique constraint. Now you need retry logic, which adds complexity and latency.

### The Correct Approach

```
1. INSERT row (long_url only) → DB returns unique ID
2. Encode ID with Base62 → short_code
3. UPDATE row with short_code
```

The database owns uniqueness. The application owns encoding. These responsibilities do not overlap.

**Pseudocode**

```python
def create_url(long_url: str):
    new_row = insert_url(long_url)       # returns unique id
    short_code = encode_base62(new_row.id)
    update_short_code(new_row.id, short_code)
    return short_code
```

---

## Handling Partial Failures with Transactions

**The failure scenario**

```
Step 1: INSERT  →  id = 125, short_code = NULL
Step 2: crash
Step 3: UPDATE  →  never executes
```

The DB now contains a row with no short code. The redirect will never work. This is an orphan row and an inconsistent state.

### Solution 1: Database Transaction (Primary)

Wrap both operations in a transaction. If anything fails before commit, the DB rolls back automatically. No broken data.

```python
def create_url(session, long_url: str):
    try:
        new_url = URL(long_url=long_url)
        session.add(new_url)
        session.flush()   # get ID without committing

        short_code = encode_base62(new_url.id)
        new_url.short_code = short_code

        session.commit()
        return short_code

    except Exception:
        session.rollback()
        raise
```

`flush()` assigns the ID from the DB sequence without committing the transaction. `commit()` only executes after the short code is assigned. A crash at any point before `commit()` leaves no trace in the DB.

### Solution 2: Background Repair Job (Secondary safety net)

Even with transactions, edge cases exist. A background job provides a fallback.

```sql
SELECT * FROM urls WHERE short_code IS NULL;
```

For each result, generate and update the short code. This runs periodically and silently repairs any rows that slipped through.

### Tradeoff Summary

| Approach          | Reliability    | Complexity |
|-------------------|----------------|------------|
| No transaction    | Broken         | Low        |
| Transaction       | Strong         | Medium     |
| Repair job added  | Very strong    | Low add-on |

---

## Key Findings

**Uniqueness belongs to the database, not the application**

Do not generate identifiers in the application and hope they are unique. Let the DB assign the ID, then encode it. This eliminates an entire class of bugs.

**The redirect path is not a normal endpoint**

Every design decision for the redirect endpoint should optimize for speed. No heavy logic. No unnecessary DB hits. Cache everything possible. This endpoint is called orders of magnitude more often than any other.

**System design is about failure paths, not happy paths**

A system that works when everything goes right is not designed. A system that handles crashes, retries, race conditions, and invalid input consistently — that is designed.

**Idempotency prevents silent data corruption**

Without idempotency keys on write endpoints, client retries create duplicate data. This is not an edge case. Networks fail. Clients retry. Design for it from the start.

---

References:

- https://lahin31.github.io/system-design-bangla/
- https://github.com/karanpratapsingh/system-design
- https://github.com/donnemartin/system-design-primer
- https://www.geeksforgeeks.org/system-design/system-design-tutorial/
- LLM

## Next

Milestone 4: Database Design — indexing strategy, transactions, read replicas, and the decisions that 80% of backend engineers get wrong.
