from xblocked.model import STATUS_BLOCKED, STATUS_OK, STATUS_SUSPENDED, STATUS_UNAVAILABLE, classify


def _user(rest_id="100", screen_name="sn_100", blocked_by=False, rp_absent=False):
    return {
        "__typename": "User",
        "rest_id": rest_id,
        "core": {"created_at": "", "name": f"Name {rest_id}", "screen_name": screen_name},
        "legacy": {"screen_name": screen_name, "name": f"Name {rest_id}"},
        "relationship_perspectives": {} if rp_absent else {"blocked_by": blocked_by, "blocking": False, "followed_by": False, "following": True, "muting": False},
    }


def _unavailable(reason="Suspended", message="m"):
    return {"__typename": "UserUnavailable", "reason": reason, "message": message}


def _outer(node):
    return {"result": node}


def test_source_user_blocked_by_true():
    r = classify(_outer(_user("100", "a", blocked_by=True)))
    assert r.status == STATUS_BLOCKED
    assert r.blocked_by is True
    assert r.user_id == "100"
    assert r.screen_name == "a"


def test_source_user_blocked_by_false():
    r = classify(_outer(_user("200", "b", blocked_by=False)))
    assert r.status == STATUS_OK
    assert r.blocked_by is False


def test_source_user_missing_rp():
    r = classify(_outer(_user("300", "c", rp_absent=True)))
    assert r.status == STATUS_OK
    assert r.blocked_by is False


def test_unavailable_reasons():
    assert classify(_outer(_unavailable("Suspended"))).status == STATUS_SUSPENDED
    assert classify(_outer(_unavailable("Deactivated"))).status == "DEACTIVATED"
    assert classify(_outer(_unavailable("Blocked"))).status == STATUS_BLOCKED
    assert classify(_outer(_unavailable("Weird"))).status == STATUS_UNAVAILABLE


def test_none():
    assert classify(None).status == STATUS_UNAVAILABLE
    assert classify({"result": None}).status == STATUS_UNAVAILABLE

def _cand(uid, sn):
    from xblocked.collect import Candidate
    return Candidate(user_id=uid, screen_name=sn, name="")


def test_partition_cache_hit_within_ttl():
    import time as t
    from xblocked.runner import partition_cached
    now = t.time()
    prev = {"100": {"status": STATUS_OK, "ts": int(now - 3600)}}
    hits, fresh = partition_cached([_cand("100", "a")], prev, ttl_seconds=7200, now=now)
    assert len(hits) == 1 and not fresh
    cand, status, ts = hits[0]
    assert status == STATUS_OK and ts == int(now - 3600)


def test_partition_cache_blocked_never_cached():
    import time as t
    from xblocked.model import STATUS_BLOCKED as SB
    from xblocked.runner import partition_cached
    now = t.time()
    prev = {"200": {"status": SB, "ts": int(now)}}
    hits, fresh = partition_cached([_cand("200", "b")], prev, ttl_seconds=999999, now=now)
    assert not hits and len(fresh) == 1


def test_partition_cache_stale_and_missing_ts():
    import time as t
    from xblocked.runner import partition_cached
    now = t.time()
    prev = {
        "300": {"status": STATUS_OK, "ts": int(now - 10_000_000)},
        "400": {"status": STATUS_OK},
    }
    hits, fresh = partition_cached([_cand("300", "c"), _cand("400", "d")], prev, ttl_seconds=3600, now=now)
    assert not hits and {c.user_id for c in fresh} == {"300", "400"}


def test_partition_cache_ttl_zero_disables():
    from xblocked.runner import partition_cached
    hits, fresh = partition_cached([_cand("500", "e")], {"500": {"status": STATUS_OK, "ts": 1}}, ttl_seconds=0)
    assert not hits and len(fresh) == 1


def test_save_state_preserves_ts_for_cached_hits(tmp_path):
    from xblocked.report import load_state, save_state
    from xblocked.collect import Candidate
    from xblocked.classify import CheckResult
    from xblocked.runner import Outcome

    p = tmp_path / "st.json"
    o1 = Outcome(candidate=Candidate(user_id="9", screen_name="x", name=""),
                 result=CheckResult(user_id="9", screen_name="x", status=STATUS_OK))
    save_state(p, [o1])
    ts1 = load_state(p)["accounts"]["9"]["ts"]

    o2 = Outcome(candidate=Candidate(user_id="9", screen_name="x", name=""),
                 result=CheckResult(user_id="9", screen_name="x", status=STATUS_OK),
                 cached=True)
    save_state(p, [o2])
    st = load_state(p)["accounts"]["9"]
    assert st["ts"] == ts1

    o3 = Outcome(candidate=Candidate(user_id="9", screen_name="x", name=""),
                 result=CheckResult(user_id="9", screen_name="x", status=STATUS_OK),
                 cached=False)
    save_state(p, [o3])
    assert load_state(p)["accounts"]["9"]["ts"] >= ts1
