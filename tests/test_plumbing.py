import json
import time

import pytest

from xblocked.classify import (
    STATUS_BLOCKED,
    STATUS_ERROR,
    STATUS_OK,
    CheckResult,
    batch_check,
    single_check_by_screen_name,
)
from xblocked.collect import _walk_tweet, collect_candidates
from xblocked.model import parse_tweets_timeline, parse_users_timeline
from xblocked.query_ids import apply_query_ids


class _FakeRaw:
    def __init__(self, payload=None, exc=None):
        self.payload = payload
        self.exc = exc

    def users_by_rest_ids(self, ids):
        if self.exc:
            raise self.exc
        entries = []
        for uid in ids:
            if uid in self.payload:
                entries.append(self.payload[uid])
        return entries

    def me(self, screen_name):
        if self.exc:
            raise self.exc
        return self.payload.get(screen_name)


def _res(uid, sn, blocked=False, status=None):
    return CheckResult(user_id=uid, screen_name=sn, name=sn, status=status or (STATUS_BLOCKED if blocked else STATUS_OK), blocked_by=blocked)


def test_batch_check_ok():
    client = _FakeRaw(payload={"1": _res("1", "a", blocked=True), "2": _res("2", "b"), "3": _res("3", "c")})
    results = batch_check(client, ["1", "2", "3", "99"], batch_size=5)
    by_id = {r.user_id: r for r in results}
    assert by_id["1"].status == STATUS_BLOCKED
    assert by_id["1"].blocked_by is True
    assert by_id["2"].status == STATUS_OK
    assert by_id["99"].status == "UNAVAILABLE"


def test_batch_check_chunking():
    payload = {str(i): _res(str(i), f"s{i}", blocked=(i == 7)) for i in range(1, 12)}
    client = _FakeRaw(payload=payload)
    results = batch_check(client, [str(i) for i in range(1, 12)], batch_size=5)
    assert len(results) == 11
    assert [r.user_id for r in results] == [str(i) for i in range(1, 12)]
    assert [r for r in results if r.status == STATUS_BLOCKED][0].user_id == "7"


def test_batch_check_error():
    client = _FakeRaw(exc=RuntimeError("rate limited"))
    results = batch_check(client, ["1", "2"], batch_size=5)
    assert all(r.status == STATUS_ERROR for r in results)
    assert "rate limited" in results[0].error


def test_single_check_by_screen_name():
    client = _FakeRaw(payload={"t": _res("10", "t", blocked=True)})
    assert single_check_by_screen_name(client, "t").status == STATUS_BLOCKED
    client2 = _FakeRaw(exc=RuntimeError("x"))
    assert single_check_by_screen_name(client2, "t").status == STATUS_ERROR


def _tl_entries(entries):
    return {"data": {"instructions": [{"type": "TimelineAddEntries", "entries": entries}]}}


def _cursor_entry(v):
    return {"content": {"cursorType": "Bottom", "value": v, "entryType": "TimelineTimelineCursor"}}


def _user_entry(rest_id, sn, unavail=False):
    node = {"__typename": "UserUnavailable", "reason": "Blocked"} if unavail else {
        "__typename": "User", "rest_id": rest_id, "core": {"screen_name": sn, "name": sn},
        "relationship_perspectives": {"blocked_by": True, "blocking": False},
    }
    return {"content": {"entryType": "TimelineTimelineItem", "itemContent": {"user_results": {"result": node}}}}


def test_parse_users_timeline():
    data = _tl_entries([_cursor_entry("c1"), _user_entry("1", "a"), _user_entry("2", "b"), _user_entry("3", "c", unavail=True)])
    users, cur = parse_users_timeline(data)
    assert cur == "c1"
    assert len(users) == 3
    assert users[0].status == STATUS_BLOCKED
    assert users[2].user_id is None  # unavailable has no rest_id


def _tweet_entry(tid, author_rid, author_sn, mentions=(), reply_to=None, quoted=None):
    tweet = {
        "__typename": "Tweet", "id_str": tid, "rest_id": tid,
        "core": {"user_results": {"result": {"__typename": "User", "rest_id": author_rid, "core": {"screen_name": author_sn, "name": author_sn}}}},
        "legacy": {"entities": {"user_mentions": [{"id_str": m, "screen_name": m} for m in mentions]}, "in_reply_to_user_id_str": reply_to, "in_reply_to_screen_name": reply_to},
    }
    if quoted:
        tweet["legacy"]["quoted_status_result"] = {"result": quoted}
    return {"content": {"entryType": "TimelineTimelineItem", "itemContent": {"tweet_results": {"result": tweet}}}}


def test_parse_tweets_timeline():
    quoted_body = {"__typename": "Tweet", "id_str": "q1", "rest_id": "q1"}
    data = _tl_entries([_cursor_entry("c9"), _tweet_entry("t1", "a1", "alice", mentions=["bob"], reply_to="carol", quoted=quoted_body)])
    tweets, cur = parse_tweets_timeline(data)
    assert cur == "c9"
    assert len(tweets) == 1  # quoted tweets are nested inside legacy, not separate entries
    assert tweets[0]["id_str"] == "t1"


def test_walk_tweet_collects_candidates():
    seen = []
    out = []
    t = {"__typename": "Tweet", "id_str": "t1",
         "core": {"user_results": {"result": {"__typename": "User", "rest_id": "me", "core": {"screen_name": "me", "name": "me"}}}},
         "legacy": {"entities": {"user_mentions": [{"id_str": "bob", "screen_name": "bob"}]}, "in_reply_to_user_id_str": "carol", "in_reply_to_screen_name": "carol"}}
    _walk_tweet(t, "own", "me", set(), out)
    ids = {c.user_id for c in out}
    assert "bob" in ids and "carol" in ids
    assert "me" not in ids


def test_apply_query_ids():
    placeholder = {"UsersByRestIds": {"queryId": "OLD", "@path": "/i/api/graphql/OLD/UsersByRestIds"}}
    ids = {"UsersByRestIds": "NEWID1234567890123456", "Other": "X"}
    n = apply_query_ids(placeholder, ids)
    assert n == 1
    assert placeholder["UsersByRestIds"]["queryId"] == "NEWID1234567890123456"
    assert placeholder["UsersByRestIds"]["@path"] == "/i/api/graphql/NEWID1234567890123456/UsersByRestIds"