from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def _user_page(fn: Callable[[Optional[str], Optional[int]], tuple[list[Any], Optional[str]]], limit: int, delay_seconds: float, count: int = 20, max_pages: int = 100) -> list[Any]:
    cursor: Optional[str] = None
    out: list[Any] = []
    pages = 0
    while True:
        pages += 1
        if pages > max_pages:
            break
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


def _tweet_page(fn, limit, delay_seconds, count: int = 20, max_pages: int = 100) -> list[dict]:
    cursor: Optional[str] = None
    out: list[dict] = []
    pages = 0
    while True:
        pages += 1
        if pages > max_pages:
            break
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
) -> tuple[dict[str, Candidate], list[str]]:
    seen: dict[str, Candidate] = {}
    skipped: list[str] = []

    def cap(key: str) -> int:
        return int(limits.get(key, 0) or 0)

    def run_user(name: str, fn, limit: int) -> None:
        if limit <= 0:
            return
        try:
            _s = time.time()
            items = _user_page(fn, limit, delay_seconds)
            cands = []
            for r in items:
                c = _from_check(r, name, me_id)
                if c:
                    cands.append(c)
            merge(seen, cands)
            print(f"[collect][{name}] {len(items)} items in {time.time()-_s:.1f}s", flush=True)
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{name}: {exc}")

    def run_tweets(name: str, fn, limit: int) -> None:
        if limit <= 0:
            return
        try:
            _s = time.time()
            tweets = _tweet_page(fn, limit, delay_seconds)
            merge(seen, _tweet_candidates(tweets, name, me_id))
            print(f"[collect][{name}] {len(tweets)} tweets in {time.time()-_s:.1f}s", flush=True)
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{name}: {exc}")

    parallel_sources: list[tuple[str, str, Callable]] = [
        ("max_following", "following", lambda: run_user("following", lambda cur, cnt: client.following(me_id, cursor=cur, count=cnt), cap("max_following"))),
        ("max_followers", "followers", lambda: run_user("followers", lambda cur, cnt: client.followers(me_id, cursor=cur, count=cnt), cap("max_followers"))),
        ("max_own_tweets", "own_tweets", lambda: run_tweets("own_tweets", lambda cur, cnt: client.user_tweets(me_id, cursor=cur, count=cnt), cap("max_own_tweets"))),
        ("max_likes", "likes", lambda: run_tweets("likes", lambda cur, cnt: client.likes(me_id, cursor=cur, count=cnt), cap("max_likes"))),
        ("max_bookmarks", "bookmarks", lambda: run_tweets("bookmarks", lambda cur, cnt: client.bookmarks(cursor=cur, count=cnt), cap("max_bookmarks"))),
        ("max_connect", "connect", lambda: run_tweets("connect", lambda cur, cnt: client.connect_tab(cursor=cur, count=cnt), cap("max_connect"))),
        ("max_notifications", "notifications", lambda: run_tweets("notifications", lambda cur, cnt: client.notifications(cursor=cur, count=cnt), cap("max_notifications"))),
        ("max_followers_you_know", "followers_you_know", lambda: run_user("followers_you_know", lambda cur, cnt: client.followers_you_know(me_id, cursor=cur, count=cnt), cap("max_followers_you_know"))),
    ]
    active = [(label, fn) for key, label, fn in parallel_sources if cap(key) > 0]
    if active:
        with ThreadPoolExecutor(max_workers=min(len(active), 8)) as pool:
            futures = {pool.submit(fn): name for name, fn in active}
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as exc:  # noqa: BLE001
                    skipped.append(f"{futures[fut]}: {exc}")

    if cap("max_tweet_threads") > 0:
        try:
            _s = time.time()
            mine = _tweet_page(lambda cur, cnt: client.user_tweets(me_id, cursor=cur, count=cnt), cap("max_tweet_threads"), 0.0)
            thread_ids = [t.get("id_str") or t.get("rest_id") for t in mine]
            thread_ids = [x for x in thread_ids if x]

            def fetch_thread(tid: str) -> list[Candidate]:
                replies = _tweet_page(lambda cur, cnt: client.tweet_thread(tid, cursor=cur, count=cnt), 50, 0.0, max_pages=3)
                return _tweet_candidates(replies, f"replies:{tid[:8]}", me_id)

            with ThreadPoolExecutor(max_workers=min(len(thread_ids), 8)) as tp:
                all_cands = []
                futures = {tp.submit(fetch_thread, tid): tid for tid in thread_ids}
                for fut in as_completed(futures):
                    try:
                        all_cands.extend(fut.result())
                    except Exception:
                        pass
                merge(seen, all_cands)
            print(f"[collect][tweet_thr] {len(thread_ids)} threads in {time.time()-_s:.1f}s", flush=True)
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"tweet_threads: {exc}")

    if limits.get("use_search"):
        for query in limits.get("search_queries", []):
            run_tweets(f"search:{query}", lambda cur, cnt: client.search(query, cursor=cur, count=cnt), cap("max_own_tweets") or 100)

    my_tweets: list[str] = []
    if cap("max_favoriters") > 0 or cap("max_retweeters") > 0:
        try:
            tweets = _tweet_page(lambda cur, cnt: client.user_tweets(me_id, cursor=cur, count=cnt), 20, delay_seconds)
            my_tweets = [t.get("id_str") or t.get("rest_id") for t in tweets]
            my_tweets = [x for x in my_tweets if x]
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"my_tweets: {exc}")

    fr_rt_tasks: list[tuple[str, Callable]] = []
    if cap("max_favoriters") > 0:
        for tweet_id in my_tweets:
            fr_rt_tasks.append((f"favoriters:{tweet_id[:8]}", lambda tid=tweet_id: run_user(f"favoriters:{tid[:8]}", lambda cur, cnt: client.favoriters(tid, cursor=cur, count=cnt), cap("max_favoriters"))))
    if cap("max_retweeters") > 0:
        for tweet_id in my_tweets:
            fr_rt_tasks.append((f"retweeters:{tweet_id[:8]}", lambda tid=tweet_id: run_user(f"retweeters:{tid[:8]}", lambda cur, cnt: client.retweeters(tid, cursor=cur, count=cnt), cap("max_retweeters"))))

    if fr_rt_tasks:
        _s = time.time()
        with ThreadPoolExecutor(max_workers=min(len(fr_rt_tasks), 10)) as pool:
            futures = {pool.submit(fn): name for name, fn in fr_rt_tasks}
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as exc:  # noqa: BLE001
                    skipped.append(f"{futures[fut]}: {exc}")
        print(f"[collect][fav+rt] {len(fr_rt_tasks)} tasks in {time.time()-_s:.1f}s", flush=True)

    return seen, skipped