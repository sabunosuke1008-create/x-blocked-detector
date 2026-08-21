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