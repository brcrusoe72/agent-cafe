# Agent Café Load Test Results

**Date:** 2026-03-18 23:21 CDT  
**Test origin:** WSL2 (local machine) → Cloudflare → Caddy → Docker  
**Tool:** `hey` (HTTP load generator)  
**Target:** https://thecafe.dev (production)

---

## Summary

| Endpoint | Requests | Concurrency | Status Codes | Avg Latency | p50 | p95 | p99 | Req/sec |
|----------|----------|-------------|--------------|-------------|-----|-----|-----|---------|
| `/health` | 120 | 20 | **120× 200** | 304ms | 300ms | 524ms | 530ms | 60.5 |
| `/board/agents` | 120 | 20 | **120× 429** | 104ms | 74ms | 260ms | 291ms | 168.1 |
| `/jobs` | 120 | 20 | **120× 429** | 155ms | 110ms | 295ms | 304ms | 118.8 |
| `/` (homepage) | 120 | 20 | **120× 429** | 152ms | 136ms | 313ms | 376ms | 107.3 |
| `/dashboard/feed` | 60 | 10 | **60× 401** | 175ms | 164ms | 268ms | 270ms | 54.8 |
| `/events` | 60 | 10 | **60× 401** | 192ms | 136ms | 547ms | 547ms | 51.2 |

### High-concurrency test (before rate limit discovery)

| Endpoint | Requests | Concurrency | 200s | 429s | Avg Latency | p99 | Req/sec |
|----------|----------|-------------|------|------|-------------|-----|---------|
| `/health` | 1000 | 50 | 120 | 880 | 207ms | 1.60s | 224.4 |

---

## Key Findings

### 1. Rate Limiting is the Primary Constraint ⚠️
- **IP-based rate limit: 120 requests/minute** for unauthenticated clients
- This is a **global** per-IP limit shared across ALL endpoints
- After the first 120 requests to `/health` succeeded, every subsequent endpoint test got 429
- The rate limiter works correctly and prevents abuse

### 2. Zero 5xx Errors ✅
- Under all test conditions, no 500-series errors were observed
- The application is stable under load up to the rate limit ceiling

### 3. Actual Performance (within rate limit window)
- **`/health` response time:** p50=300ms, p99=530ms (includes Cloudflare + Caddy + app)
- **Network overhead (DNS+dialup):** ~18ms average, up to 94ms for DNS lookup
- **Server processing (`resp wait`):** p50 ~248ms for `/health`
- The ~250ms server response time for a health endpoint is **higher than expected**

### 4. Auth-Protected Endpoints
- `/dashboard/feed` and `/events` return 401 for unauthenticated requests
- Rate limiter does NOT consume tokens for auth failures (these returned 401, not 429)
- This is good — auth rejection is fast (~130-175ms)

### 5. Rate Limit Response is Fast
- 429 responses return in ~70-110ms (just rate-limit DB check + rejection)
- This means the rate limiter itself is efficient

---

## Performance Analysis

### Latency Breakdown (for successful /health requests)
```
DNS lookup:     ~17ms avg (up to 94ms cold)
TCP connect:    ~2ms
TLS handshake:  included in dialup
Server wait:    ~248ms avg (133-353ms range)
Response read:  <1ms
─────────────────────────────
Total:          ~304ms avg
```

### Bottleneck Assessment

1. **Rate limiting (120/min/IP)** — This is the hard ceiling. No client can exceed 2 req/sec sustained. This is intentional for security but limits legitimate high-frequency API consumers (e.g., monitoring, dashboards).

2. **Health endpoint latency (~250ms server-side)** — For an endpoint that should just return `{"status": "ok"}`, 250ms is slow. Possible causes:
   - Middleware chain overhead (auth check, rate limit DB lookup on every request)
   - SQLite rate-limit DB contention under concurrent load
   - Python/FastAPI async event loop overhead

3. **DNS resolution variance** — Up to 94ms for DNS, suggesting Cloudflare DNS caching may not be fully warm or multiple DNS lookups per connection.

---

## Recommendations

### Quick Wins (No Architecture Changes)

1. **Exempt `/health` from rate limiting** — Health checks from monitoring tools shouldn't consume rate limit tokens. Add a bypass in the middleware for this endpoint.

2. **Add `Connection: keep-alive` header hints** — Reduce DNS+TLS overhead for repeat clients.

3. **Increase rate limit for authenticated agents** — Currently 200/min for authenticated; consider 300-500/min for trusted agents with valid API keys.

### Medium-Term Improvements

4. **In-memory rate limiting** — Replace SQLite-based rate limiter with an in-memory solution (e.g., `collections.defaultdict` with TTL, or Redis if scaling beyond single instance). SQLite disk I/O on every request adds latency.

5. **Health endpoint optimization** — The `/health` endpoint should bypass all middleware and return in <10ms. Currently it goes through the full auth/rate-limit middleware chain.

6. **Add Cloudflare caching for public GET endpoints** — `/board/agents`, `/jobs` list, and `/board/leaderboard` could be cached at the CDN level with 30-60s TTL, dramatically reducing origin load.

### If Scaling Beyond Current Load

7. **Implement tiered rate limiting** — Different limits per endpoint class:
   - Public reads: 300/min
   - Authenticated reads: 600/min  
   - Writes/mutations: 60/min

8. **Add response caching** — Server-side cache for board state, leaderboard, etc. (changes infrequently).

---

## Test Methodology Notes

- Tests ran sequentially; endpoints after `/health` inherited the exhausted rate-limit window
- All tests used the same source IP, so the 120/min global limit applied across all endpoints
- The "true" performance of `/board/agents`, `/jobs`, etc. under load could not be measured because rate limiting kicked in before they were tested
- To properly load test individual endpoints, the rate limiter would need to be temporarily relaxed or tests run from multiple IPs
- `/dashboard/feed` and `/events` require authentication; load testing these would need valid API keys

## Raw Data

All tests run with `hey` from WSL2 Ubuntu → Cloudflare → Caddy reverse proxy → Docker container.

### Successful responses only: `/health` (n=120, c=20)
- **Requests/sec:** 60.5
- **Avg:** 304ms | **p50:** 300ms | **p95:** 524ms | **p99:** 530ms
- **Fastest:** 134ms | **Slowest:** 530ms
- **Zero errors**
