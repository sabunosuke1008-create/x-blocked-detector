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
from .client import build_client, resolve_me


@dataclass
class Outcome:
    candidate: Candidate
    result: CheckResult


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
) -> tuple[list[Outcome], list[str], str]:
    client = build_client(cfg.cookies, cfg.tid_mode, refresh_query_ids=refresh_query_ids)
    print(f"[runner] client built (tid_mode={cfg.tid_mode})", flush=True)
    _t0 = time.time()
    screen_name = me_screen_name_override or cfg.me_screen_name
    me_id, me_screen = resolve_me(client, screen_name, me_user_id_override or cfg.me_user_id)
    print(f"[runner] me = @{me_screen} ({me_id})  t=+{time.time()-_t0:.1f}s", flush=True)

    seen, skipped = collect_candidates(
        client,
        me_id=me_id,
        me_screen_name=me_screen,
        limits=cfg.limits,
        delay_seconds=cfg.delay_seconds,
    )
    print(f"[runner] collected {len(seen)} candidates; skipped={skipped}  t=+{time.time()-_t0:.1f}s", flush=True)

    if limit_override is not None:
        seen = dict(list(seen.items())[:limit_override])
        print(f"[runner] limited to {len(seen)}", flush=True)

    ids = [c.user_id for c in seen.values() if c.user_id]
    by_res_id: dict[str, CheckResult] = {}
    if ids:
        results = batch_check(client, ids, cfg.batch_size)
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
                done_map[cand.user_id or cand.screen_name] = Outcome(candidate=cand, result=res)
                done += 1
                _throttle_if_exhausted(client)
                if progress:
                    progress(len(done_map), len(seen))

    outcomes = list(done_map.values())
    return outcomes, skipped, me_id