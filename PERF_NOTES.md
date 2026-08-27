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
- Transient retry inside `_request`: WinError 10035, ConnectionError/OSError,
  httpcore "deque mutated during iteration" (excl. timeouts)
- Connection pool raised 32/16 -> 48/24 h2 connections
- Default concurrency 6 -> 12 (measured safe; plateau ~85/s)

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
