from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

from .classify import (
    STATUS_BLOCKED,
    STATUS_ERROR,
    STATUS_OK,
    CheckResult,
    batch_check,
    single_check_by_screen_name,
)
from .collect import Candidate, collect_candidates
from .config import Config
from .model import (
    STATUS_DEACTIVATED,
    STATUS_OK,
    STATUS_SMART_BLOCKED,
    STATUS_SUSPENDED,
    STATUS_UNAVAILABLE,
)
from .client import build_client, resolve_me


@dataclass
class Outcome:
    candidate: Candidate
    result: CheckResult
    cached: bool = False


# verdicts that may be reused from cache; blocked statuses are always re-probed live
_CACHEABLE = {STATUS_OK, STATUS_SUSPENDED, STATUS_DEACTIVATED, STATUS_UNAVAILABLE}


def partition_cached(
    pending: list[Candidate],
    prev_accounts: dict,
    ttl_seconds: int,
    now: Optional[float] = None,
) -> tuple[list[tuple[Candidate, str, Optional[int]]], list[Candidate]]:
    """Split candidates needing a check into (cache_hits, still_pending).

    Cache hits become (candidate, status, ts). Blocked statuses never come
    from the cache so every previously-blocked account is re-probed each run.
    Records without an integer ts are treated as stale.
    """
    if ttl_seconds <= 0:
        return [], pending
    now_f = time.time() if now is None else now
    hits: list[tuple[Candidate, str, Optional[int]]] = []
    fresh: list[Candidate] = []
    for cand in pending:
        key = cand.user_id or cand.screen_name or ""
        rec = prev_accounts.get(key) if isinstance(prev_accounts, dict) else None
        if not isinstance(rec, dict):
            fresh.append(cand)
            continue
        status = rec.get("status")
        ts = rec.get("ts")
        if status in _CACHEABLE and isinstance(ts, int) and (now_f - ts) <= ttl_seconds:
            hits.append((cand, str(status), ts))
        else:
            fresh.append(cand)
    return hits, fresh


def _result_from_candidate(cand: Candidate) -> CheckResult:
    return CheckResult(
        user_id=cand.user_id,
        screen_name=cand.screen_name,
        name=cand.name,
        status=cand.block_status or "UNKNOWN",
        blocked_by=cand.block_status == STATUS_BLOCKED,
    )


def _fast_probe(client, me_id: str, cand: Candidate) -> CheckResult:
    try:
        return client.friendship_check(me_id, target_id=cand.user_id or None, target_screen_name=cand.screen_name or None)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(user_id=cand.user_id, screen_name=cand.screen_name, status=STATUS_ERROR, error=str(exc))


def _throttle_if_exhausted(client) -> None:
    if client.rate_remaining is not None and client.rate_remaining <= 0 and client.rate_reset:
        wait = max(1, int(client.rate_reset) - int(time.time()) + 2)
        print(f"[runner] rate budget exhausted, sleeping {wait}s", flush=True)
        time.sleep(wait)
        client.rate_remaining = None


def run_scan(
    cfg: Config,
    me_screen_name_override: str = "",
    me_user_id_override: str = "",
    limit_override: Optional[int] = None,
    progress=None,
    refresh_query_ids: bool = False,
    on_result=None,
) -> tuple[list[Outcome], list[str], str]:
    client = build_client(cfg.cookies, cfg.tid_mode, refresh_query_ids=refresh_query_ids)
    print(f"[runner] client built (tid_mode={cfg.tid_mode})", flush=True)
    _t0 = time.time()
    screen_name = me_screen_name_override or cfg.me_screen_name
    me_id, me_screen = resolve_me(client, screen_name, me_user_id_override or cfg.me_user_id)
    print(f"[runner] me = @{me_screen} ({me_id})  t=+{time.time()-_t0:.1f}s", flush=True)

    budget = None
    if cfg.max_pages > 0 or cfg.time_budget_seconds > 0:
        from .budget import PageBudget

        budget = PageBudget(max_pages=cfg.max_pages, seconds=cfg.time_budget_seconds)
    seen, skipped = collect_candidates(
        client,
        me_id=me_id,
        me_screen_name=me_screen,
        limits=cfg.limits,
        delay_seconds=cfg.delay_seconds,
        budget=budget,
    )
    print(f"[runner] collected {len(seen)} candidates; skipped={skipped}  t=+{time.time()-_t0:.1f}s", flush=True)

    if limit_override is not None:
        seen = dict(list(seen.items())[:limit_override])
        print(f"[runner] limited to {len(seen)}", flush=True)

    ids = [c.user_id for c in seen.values() if c.user_id]
    by_res_id: dict[str, CheckResult] = {}
    if ids and not getattr(run_scan, "_batch_always_fails", False):
        results = batch_check(client, ids, cfg.batch_size)
        error_count = sum(1 for r in results if r.status == STATUS_ERROR)
        if len(results) > 0 and error_count == len(results):
            run_scan._batch_always_fails = True
            print("[runner] batch check always fails, skipping future calls", flush=True)
        print(f"[runner] batch check done: {len(results)} results  t=+{time.time()-_t0:.1f}s", flush=True)
        by_res_id = {r.user_id: r for r in results}

    done_map: dict[str, Outcome] = {}
    pending: list[Candidate] = []
    for cand in seen.values():
        res = by_res_id.get(cand.user_id) if cand.user_id else None
        if cand.block_status is not None:
            done_map[cand.user_id or cand.screen_name] = Outcome(candidate=cand, result=_result_from_candidate(cand))
        elif res is not None and res.status != STATUS_ERROR:
            done_map[cand.user_id or cand.screen_name] = Outcome(candidate=cand, result=res)
        else:
            pending.append(cand)

    # verdict cache: reuse fresh non-blocked verdicts, always re-probe blockers
    prev_accounts: dict = {}
    if cfg.state_file:
        try:
            from .report import load_state

            st = load_state(cfg.state_file)
            acc = st.get("accounts") if isinstance(st, dict) else None
            if isinstance(acc, dict):
                prev_accounts = acc
        except Exception:  # noqa: BLE001
            prev_accounts = {}
    ttl_seconds = int(getattr(cfg, "cache_ttl_hours", 0) or 0) * 3600

    def _emit(o: Outcome) -> None:
        if on_result is None:
            return
        try:
            on_result(o)
        except Exception:  # noqa: BLE001
            pass

    cache_hits, pending = partition_cached(pending, prev_accounts, ttl_seconds)
    for cand, status, _ts in cache_hits:
        o = Outcome(
            candidate=cand,
            result=CheckResult(
                user_id=cand.user_id,
                screen_name=cand.screen_name,
                name=cand.name,
                status=status,
                blocked_by=status == STATUS_BLOCKED,
            ),
            cached=True,
        )
        done_map[cand.user_id or cand.screen_name] = o
        _emit(o)
    if cache_hits:
        print(f"[runner] {len(cache_hits)} verdicts served from cache  t=+{time.time()-_t0:.1f}s", flush=True)

    concurrency = min(max(int(getattr(cfg, "concurrency", 6) or 6), 1), 16)
    if pending:
        print(f"[runner] probing {len(pending)} candidates with concurrency={concurrency}  t=+{time.time()-_t0:.1f}s", flush=True)
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_fast_probe, client, me_id, cand): cand for cand in pending}
            done = 0
            for fut in as_completed(futures):
                cand = futures[fut]
                try:
                    res = fut.result()
                except Exception as exc:  # noqa: BLE001
                    res = CheckResult(user_id=cand.user_id, screen_name=cand.screen_name, status=STATUS_ERROR, error=str(exc))
                if res.status == STATUS_ERROR and cand.screen_name:
                    res = single_check_by_screen_name(client, cand.screen_name)
                o = Outcome(candidate=cand, result=res)
                done_map[cand.user_id or cand.screen_name] = o
                _emit(o)
                done += 1
                _throttle_if_exhausted(client)
                if progress:
                    progress(len(done_map), len(seen))

    outcomes = list(done_map.values())

    if cfg.state_file:
        try:
            prev_blocked = [
                key for key, value in prev_accounts.items()
                if isinstance(value, dict) and value.get("status") in (STATUS_BLOCKED, STATUS_SMART_BLOCKED)
            ]
            already = {o.candidate.user_id or o.candidate.screen_name for o in outcomes}
            recheck = [k for k in prev_blocked if k not in already]
            if recheck:
                print(f"[runner] rechecking {len(recheck)} previously-blocked accounts  t=+{time.time()-_t0:.1f}s", flush=True)
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    futures = {}
                    for key in recheck:
                        cand = Candidate(user_id=key, screen_name="", name="", sources={"prev_blocked"})
                        futures[pool.submit(_fast_probe, client, me_id, cand)] = cand
                    for fut in as_completed(futures):
                        cand = futures[fut]
                        try:
                            res = fut.result()
                        except Exception as exc:  # noqa: BLE001
                            res = CheckResult(user_id=cand.user_id, status=STATUS_ERROR, error=str(exc))
                        o = Outcome(candidate=cand, result=res)
                        outcomes.append(o)
                        _emit(o)
        except Exception as exc:  # noqa: BLE001
            print(f"[runner] prev-blocked recheck skipped: {exc}", flush=True)

    return outcomes, skipped, me_id