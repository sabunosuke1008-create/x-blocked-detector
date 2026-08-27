# Scaling & Performance Notes

Measured on Surface Go 4 (Intel N200, 8GB RAM), httpx HTTP/2 build.

## Measured facts (2026-08)

| Metric | Value |
| --- | --- |
| Inline block_status coverage from collection | **91–93%** of candidates need NO individual probe |
| friendship_check throughput C=8 / 16 / 32 | 36.7 / 83.1 / 84.9 req/s (CPU-bound plateau ~85/s) |
| Server-side rate limiting during bursts (409 probes) | none observed; no x-rate-limit headers on mobile query |
| Collection yield (all sources, moderate depth) | 632–643 unique candidates in ~15–20s |
| Fan-out task failures before socket fix | 7/40 (WSAEWOULDBLOCK 10035, Windows non-blocking send buffer) |
| After `_request` transient retry | 0 failures |

## Bottleneck model at 1000–10000 candidates

1. Probes are NOT the bottleneck: even a hypothetical 10k-probe worst case = ~118s at
   85/s; realistically ~90% come pre-classified from collection payloads (~10s of probes
   per 10k candidates).
2. Collection pagination dominates: reaching 10k unique engagement contacts requires
   deep cursor chains (bookmarks ≈73 items/s incl. RTT) plus per-tweet favoriters /
   retweeters requests.
3. Local client reliability matters more than server limits at high concurrency
   (socket would-block, httpcore deque races) — both now retried in `_request`.

## Shipped optimizations

- httpx HTTP/2 migration, unified pooled client (0.16s avg latency per check)
- Transient retry inside `_request`: WinError 10035 (WSAEWOULDBLOCK), ConnectionError/
  OSError, httpcore "deque mutated during iteration" x6 with exponential-ish backoff
  (timeouts re-raised). Fan-out failures: 7/40 -> 0
- Connection pool raised 32/16 -> 48/24 h2 connections
- Default concurrency 6 -> 12 (probe throughput plateau measured ~85/s)
- **Unified collection pipeline**: all sources + threads + favoriters/retweeters run in
  one pool; my_tweets fetched once and shared (previously 3 serial phases duplicated it).
  Smoke scan: 6.1s -> 4.5s collection phase
- **Verdict cache** (`cache_ttl_hours`, default 168): OK/SUSPENDED/DEACTIVATED/
  UNAVAILABLE verdicts persist in state.json with observation ts; warm re-scan serves
  them without any HTTP call (blocked statuses always re-probed live). Measured:
  probe stage 11 requests -> 0 on second run. CLI: `--cache-ttl`, `--clear-cache`.
- **Budget modes**: `max_pages` / `time_budget_seconds` (CLI `--max-pages`, `--time-budget`) cap total collection pages and wall clock across all sources; reverse-chronological feeds make the cut naturally recent-first. Smoke: 82->66 candidates in 1.5s collection.
- **Progressive alerts**: `on_result` hook emits `  [!!] @user BLOCKED_BY` lines during probing
  instead of only at the end; `progress` ticks every 25 checks.

## Roadmap (ranked by impact)

1. **Verdict cache + incremental resume** — persist screen_name->{status,ts} and last
   cursors in state.json; re-scan only new contacts (recheck blocked ones each run).
   Repeat scans drop from O(pool) to O(new). Biggest practical win.
2. **Intra-source page pipelining** — prefetch next cursor page while parsing current;
   hides ~150–300ms RTT per page (~1.5–2x collection).
3. **Budget modes** — `--max-pages` / time budget, most-recent-engagement-first ordering
   so giant accounts get useful fast scans instead of hour-long full sweeps.
4. **Progressive reporting** — stream results during scan.
5. Optional/multipliers (default OFF): second-account cookie pools multiply budgets xN
   (TOS-gray). External paid batch profile APIs exist but return NO blocked_by /
   smart_blocked_by fields — useless for detection itself.
6. Open item: no working bulk friendship-status GraphQL op found (UsersByRestIds stays
   Cloudflare-403). Re-diff fa0311/twitter_openapi operation list on X app updates.
