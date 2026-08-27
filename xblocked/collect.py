from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .model import STATUS_BLOCKED, STATUS_OK, CheckResult, classify
from .rawclient import RawClient


@dataclass
class Candidate:
    user_id: str
    screen_name: str
    name: str
    sources: set[str] = field(default_factory=set)
    block_status: Optional[str] = None


def _is_final(status: Optional[str]) -> bool:
    return status in (STATUS_OK, STATUS_BLOCKED, "SUSPENDED", "DEACTIVATED", "UNAVAILABLE")


def _user_page(
    fn: Callable[[Optional[str], Optional[int]], tuple[list[Any], Optional[str]]],
    limit: int,
    delay_seconds: float,
    count: int = 100,
    max_pages: int = 100,
    budget=None,
) -> list[Any]:
    cursor: Optional[str] = None
    out: list[Any] = []
    pages = 0
    while True:
        pages += 1
        if pages > max_pages:
            break
        if budget is not None and not budget.try_take():
            return out
        items, cursor = fn(cursor, count)
        out.extend(items)
        if len(out) >= limit:
            return out[:limit]
        if not items:
            return out
        if not cursor or cursor == "0":
            return out
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    return out[:limit]


def _tweet_page(fn, limit, delay_seconds, count: int = 100, max_pages: int = 100, budget=None) -> list[dict]:
    cursor: Optional[str] = None
    out: list[dict] = []
    pages = 0
    while True:
        pages += 1
        if pages > max_pages:
            break
        if budget is not None and not budget.try_take():
            return out
        tweets, cursor = fn(cursor, count)
        out.extend(tweets)
        if len(out) >= limit:
            return out[:limit]
        if not tweets:
            return out
        if not cursor or cursor == "0":
            return out
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    return out[:limit]


def _from_check(result: CheckResult, source: str, me_id: str) -> Optional[Candidate]:
    if not result.user_id or result.user_id == me_id:
        return None
    status = result.status if _is_final(result.status) else None
    return Candidate(
        user_id=result.user_id,
        screen_name=result.screen_name or "",
        name=result.name or "",
        sources={source},
        block_status=status,
    )


def _author_block_status(author: Any) -> Optional[str]:
    if not isinstance(author, dict):
        return None
    typename = author.get("__typename")
    if typename == "UserUnavailable":
        reason = author.get("reason") or ""
        if reason == "Blocked":
            return STATUS_BLOCKED
        if reason in ("Suspended", "Deactivated"):
            return None
        return None
    if typename != "User":
        return None
    rp = author.get("relationship_perspectives") or {}
    if rp.get("blocked_by") is True:
        return STATUS_BLOCKED
    if rp.get("blocked_by") is False:
        return STATUS_OK
    return None


def _walk_tweet(tweet: dict, source: str, me_id: str, seen: set[str], out: list[Candidate]) -> None:
    if not isinstance(tweet, dict) or tweet.get("__typename") in ("TweetTombstone", "TweetUnavailable"):
        return
    tid = tweet.get("id_str") or tweet.get("rest_id")
    if tid:
        if tid in seen:
            return
        seen.add(tid)
    author = (tweet.get("core") or {}).get("user_results", {}).get("result")
    if isinstance(author, dict):
        aid = author.get("rest_id")
        if aid and aid != me_id:
            out.append(
                Candidate(
                    user_id=aid,
                    screen_name=(author.get("core") or {}).get("screen_name") or "",
                    name=(author.get("core") or {}).get("name") or "",
                    sources={source},
                    block_status=_author_block_status(author),
                )
            )
    legacy = tweet.get("legacy") or {}
    entities = legacy.get("entities") or {}
    for m in entities.get("user_mentions") or []:
        mid = m.get("id_str")
        if mid and mid != me_id:
            out.append(Candidate(user_id=mid, screen_name=m.get("screen_name") or "", name="", sources={source}, block_status=None))
    rid = legacy.get("in_reply_to_user_id_str")
    if rid and rid != me_id:
        out.append(Candidate(user_id=rid, screen_name=legacy.get("in_reply_to_screen_name") or "", name="", sources={source}, block_status=None))
    for key in ("quoted_status_result", "retweeted_status_result"):
        sub = (legacy.get(key) or {}).get("result") if isinstance(legacy.get(key), dict) else None
        if isinstance(sub, dict):
            _walk_tweet(sub, f"{source}:{key.split('_')[0]}", me_id, seen, out)


def _tweet_candidates(tweets: list[dict], source: str, me_id: str) -> list[Candidate]:
    out: list[Candidate] = []
    seen: set[str] = set()
    for t in tweets:
        _walk_tweet(t, source, me_id, seen, out)
    return out


_STATUS_PRIORITY = {STATUS_BLOCKED: 3, STATUS_OK: 2, "SUSPENDED": 1, "DEACTIVATED": 1, "UNAVAILABLE": 1}


def merge(seen: dict[str, Candidate], candidates: list[Candidate]) -> None:
    for c in candidates:
        key = c.user_id or ("@" + c.screen_name)
        if key in seen:
            old = seen[key]
            old.sources |= c.sources
            if not old.screen_name:
                old.screen_name = c.screen_name
            if not old.name:
                old.name = c.name
            if c.block_status and _STATUS_PRIORITY.get(c.block_status, 0) > _STATUS_PRIORITY.get(old.block_status, 0):
                old.block_status = c.block_status
        else:
            seen[key] = c


def collect_candidates(
    client: RawClient,
    me_id: str,
    me_screen_name: str,
    limits: dict[str, int],
    delay_seconds: float,
    budget=None,
) -> tuple[dict[str, Candidate], list[str]]:
    seen: dict[str, Candidate] = {}
    skipped: list[str] = []
    mu = threading.Lock()

    def cap(key: str) -> int:
        return int(limits.get(key, 0) or 0)

    def record_skip(label: str, exc: Exception) -> None:
        with mu:
            skipped.append(f"{label}: {exc}")

    def finish(label: str, cands: list[Candidate], n_items: int, t0: float) -> None:
        with mu:
            merge(seen, cands)
            print(f"[collect][{label}] {n_items} items in {time.time()-t0:.1f}s", flush=True)

    def do_user(name: str, fn, limit: int) -> None:
        if limit <= 0:
            return
        t0 = time.time()
        try:
            items = _user_page(fn, limit, delay_seconds, budget=budget)
            cands = []
            for r in items:
                c = _from_check(r, name, me_id)
                if c:
                    cands.append(c)
            finish(name, cands, len(items), t0)
        except Exception as exc:  # noqa: BLE001
            record_skip(name, exc)

    def fetch_tweets(name: str, fn, limit: int) -> None:
        if limit <= 0:
            return
        t0 = time.time()
        try:
            tweets = _tweet_page(fn, limit, delay_seconds, budget=budget)
            finish(name, _tweet_candidates(tweets, name, me_id), len(tweets), t0)
        except Exception as exc:  # noqa: BLE001
            record_skip(name, exc)

    ex = ThreadPoolExecutor(max_workers=10)
    futs: dict = {}

    def submit(label: str, fn) -> None:
        futs[ex.submit(fn)] = label

    user_sources = [
        ("max_following", "following", lambda cur, cnt: client.following(me_id, cursor=cur, count=cnt)),
        ("max_followers", "followers", lambda cur, cnt: client.followers(me_id, cursor=cur, count=cnt)),
        ("max_followers_you_know", "followers_you_know",
         lambda cur, cnt: client.followers_you_know(me_id, cursor=cur, count=cnt)),
    ]
    tweet_sources = [
        ("max_likes", "likes", lambda cur, cnt: client.likes(me_id, cursor=cur, count=cnt)),
        ("max_bookmarks", "bookmarks", lambda cur, cnt: client.bookmarks(cursor=cur, count=cnt)),
        ("max_connect", "connect", lambda cur, cnt: client.connect_tab(cursor=cur, count=cnt)),
        ("max_notifications", "notifications", lambda cur, cnt: client.notifications(cursor=cur, count=cnt)),
    ]
    for key, name, fn in user_sources:
        if cap(key) > 0:
            submit(name, lambda n=name, f=fn, k=key: do_user(n, f, cap(k)))
    for key, name, fn in tweet_sources:
        if cap(key) > 0:
            submit(name, lambda n=name, f=fn, k=key: fetch_tweets(n, f, cap(k)))
    if limits.get("use_search"):
        for query in limits.get("search_queries") or []:
            q_lim = cap("max_own_tweets") or 100
            submit(
                f"search:{query}",
                lambda qq=query, ql=q_lim: fetch_tweets(
                    f"search:{qq}", lambda cur, cnt, q=qq: client.search(q, cursor=cur, count=cnt), ql
                ),
            )

    needs_own = cap("max_own_tweets") > 0
    needs_threads = cap("max_tweet_threads") > 0
    needs_favrt = cap("max_favoriters") > 0 or cap("max_retweeters") > 0
    my_limit = max(cap("max_tweet_threads"), 20 if needs_favrt else 0, cap("max_own_tweets"))
    fut_my = ex.submit(_tweet_page,
                       lambda cur, cnt: client.user_tweets(me_id, cursor=cur, count=cnt),
                       my_limit, delay_seconds, budget=budget) if my_limit > 0 else None

    def chain_my() -> None:
        tweets: list[dict] = []
        try:
            if fut_my is not None:
                tweets = fut_my.result()
        except Exception as exc:  # noqa: BLE001
            record_skip("my_tweets", exc)
            return
        if not tweets:
            return
        ids = [t.get("id_str") or t.get("rest_id") for t in tweets]
        ids = [x for x in ids if x]
        if needs_own:
            try:
                finish("own_tweets",
                       _tweet_candidates(tweets[: cap("max_own_tweets")], "own_tweets", me_id),
                       min(len(tweets), cap("max_own_tweets")), time.time())
            except Exception as exc:  # noqa: BLE001
                record_skip("own_tweets", exc)
        derived: dict = {}

        def thread_task(tid: str):
            replies = _tweet_page(lambda cur, cnt: client.tweet_thread(tid, cursor=cur, count=cnt),
                                  50, 0.0, max_pages=3, budget=budget)
            return _tweet_candidates(replies, f"replies:{tid[:8]}", me_id)

        for tid in ids[: cap("max_tweet_threads")] if needs_threads else []:
            derived[ex.submit(thread_task, tid)] = f"replies:{tid[:8]}"
        for tid in ids[:20]:
            if cap("max_favoriters") > 0:
                derived[ex.submit(
                    lambda tid=tid: do_user(f"favoriters:{tid[:8]}",
                                            lambda cur, cnt, t=tid: client.favoriters(t, cursor=cur, count=cnt),
                                            cap("max_favoriters")))] = f"favoriters:{tid[:8]}"
            if cap("max_retweeters") > 0:
                derived[ex.submit(
                    lambda tid=tid: do_user(f"retweeters:{tid[:8]}",
                                            lambda cur, cnt, t=tid: client.retweeters(t, cursor=cur, count=cnt),
                                            cap("max_retweeters")))] = f"retweeters:{tid[:8]}"
        for f in derived:
            try:
                got = f.result()
                if isinstance(got, list):
                    with mu:
                        merge(seen, got)
                        print(f"[collect][{derived[f]}] merged {len(got)} candidates", flush=True)
            except Exception as exc:  # noqa: BLE001
                record_skip(derived[f], exc)

    fut_chain = ex.submit(chain_my) if (needs_own or needs_threads or needs_favrt) else None

    all_futs = list(futs) + ([fut_chain] if fut_chain is not None else [])
    wait(all_futs)
    for f, label in futs.items():
        try:
            f.result()
        except Exception as exc:  # noqa: BLE001
            record_skip(label, exc)
    if fut_chain is not None:
        try:
            fut_chain.result()
        except Exception as exc:  # noqa: BLE001
            record_skip("chain_my", exc)
    ex.shutdown(wait=True)

    if budget is not None and (budget.exhausted or budget.used):
        print(f"[collect] {budget.summary()}", flush=True)

    return seen, skipped