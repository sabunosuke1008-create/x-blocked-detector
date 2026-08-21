from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

STATUS_BLOCKED = "BLOCKED_BY"
STATUS_SMART_BLOCKED = "SMART_BLOCKED_BY"
STATUS_OK = "OK"
STATUS_SUSPENDED = "SUSPENDED"
STATUS_DEACTIVATED = "DEACTIVATED"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_ERROR = "ERROR"

_REASON_STATUS = {
    "Blocked": STATUS_BLOCKED,
    "Suspended": STATUS_SUSPENDED,
    "Suspension": STATUS_SUSPENDED,
    "Deactivated": STATUS_DEACTIVATED,
    "Deleted": STATUS_DEACTIVATED,
    "Unavailable": STATUS_UNAVAILABLE,
    "NftDisclosure": STATUS_UNAVAILABLE,
}


@dataclass
class CheckResult:
    user_id: Optional[str] = None
    screen_name: Optional[str] = None
    name: Optional[str] = None
    status: str = STATUS_UNKNOWN
    blocked_by: Optional[bool] = None
    detail: str = ""
    error: str = ""


def _is_dict(x: Any) -> bool:
    return isinstance(x, dict)


def _unwrap(union: Any) -> Any:
    if union is None:
        return None
    if not _is_dict(union) and hasattr(union, "actual_instance"):
        return union.actual_instance
    return union


def _attr(node: Any, name: str, default: Any = None) -> Any:
    if _is_dict(node):
        return node.get(name, default)
    return getattr(node, name, default)


def _screen_name(node: Any) -> str:
    core = _attr(node, "core")
    if core is not None and _attr(core, "screen_name"):
        return str(_attr(core, "screen_name"))
    legacy = _attr(node, "legacy")
    if legacy is not None and _attr(legacy, "screen_name"):
        return str(_attr(legacy, "screen_name"))
    return ""


def _name(node: Any) -> str:
    core = _attr(node, "core")
    if core is not None and _attr(core, "name"):
        return str(_attr(core, "name"))
    legacy = _attr(node, "legacy")
    if legacy is not None and _attr(legacy, "name"):
        return str(_attr(legacy, "name"))
    return ""


def _classify_user_dict(u: dict) -> CheckResult:
    rp = u.get("relationship_perspectives") or {}
    blocked_by = bool(rp.get("blocked_by"))
    smart_blocked_by = u.get("smart_blocked_by")
    smart_flag = bool(smart_blocked_by) if smart_blocked_by is not None else None
    core = u.get("core") or {}
    if blocked_by:
        status = STATUS_BLOCKED
    elif smart_flag:
        status = STATUS_SMART_BLOCKED
    else:
        status = STATUS_OK
    return CheckResult(
        user_id=u.get("rest_id"),
        screen_name=core.get("screen_name") or (u.get("legacy") or {}).get("screen_name") or "",
        name=core.get("name") or (u.get("legacy") or {}).get("name") or "",
        status=status,
        blocked_by=blocked_by,
        detail="smart" if smart_flag else "",
    )


def classify(node: Any) -> CheckResult:
    inst = _unwrap(node)
    if inst is None:
        return CheckResult(status=STATUS_UNAVAILABLE, detail="no result")
    if not _is_dict(inst) and hasattr(inst, "result"):
        inst = _unwrap(inst.result)
    if _is_dict(inst) and isinstance(inst.get("result"), dict):
        inst = inst["result"]
    if inst is None:
        return CheckResult(status=STATUS_UNAVAILABLE, detail="empty result")
    if _is_dict(inst):
        typename = inst.get("__typename")
        if typename == "User":
            return _classify_user_dict(inst)
        if typename == "UserUnavailable":
            reason = inst.get("reason") or ""
            return CheckResult(
                status=_REASON_STATUS.get(reason, STATUS_UNAVAILABLE),
                detail=reason or "unavailable",
                error=inst.get("message") or "",
            )
        return CheckResult(status=STATUS_UNAVAILABLE, detail=typename or "no typename")
    kind = getattr(inst, "typename", None) or type(inst).__name__
    if kind == "User":
        rp = _attr(inst, "relationship_perspectives")
        blocked_by = bool(_attr(rp, "blocked_by")) if rp is not None else False
        smart_val = _attr(inst, "smart_blocked_by")
        smart_flag = bool(smart_val) if smart_val is not None else False
        if blocked_by:
            status = STATUS_BLOCKED
        elif smart_flag:
            status = STATUS_SMART_BLOCKED
        else:
            status = STATUS_OK
        return CheckResult(
            user_id=_attr(inst, "rest_id"),
            screen_name=_screen_name(inst),
            name=_name(inst),
            status=status,
            blocked_by=blocked_by,
            detail="smart" if smart_flag else "",
        )
    if kind == "UserUnavailable":
        reason = _attr(inst, "reason") or ""
        return CheckResult(
            status=_REASON_STATUS.get(reason, STATUS_UNAVAILABLE),
            detail=reason or "unavailable",
            error=_attr(inst, "message") or "",
        )
    return CheckResult(status=STATUS_UNAVAILABLE, detail=kind)


def _entries(instr: list[Any]) -> list[dict]:
    out: list[dict] = []
    for inst in instr or []:
        if inst.get("type") == "TimelineAddEntries":
            out.extend(inst.get("entries") or [])
    return out


def _find_instructions(data: dict[str, Any]) -> list[Any]:
    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "instructions" and isinstance(value, list):
                    return value
                hit = walk(value)
                if hit is not None:
                    return hit
        elif isinstance(node, list):
            for item in node:
                hit = walk(item)
                if hit is not None:
                    return hit
        return None

    hit = walk(data)
    return hit if hit is not None else []


def _cursor_of(entries: list[dict]) -> Optional[str]:
    for e in entries:
        content = e.get("content") or {}
        if content.get("cursorType") == "Bottom":
            return content.get("value")
    return None


def parse_users_timeline(data: dict[str, Any]) -> tuple[list[CheckResult], Optional[str]]:
    if not data.get("data"):
        return [], None
    entries = _entries(_find_instructions(data["data"]))
    users: list[CheckResult] = []
    for e in entries:
        content = e.get("content") or {}
        if content.get("entryType") == "TimelineTimelineItem":
            item = content.get("itemContent") or {}
            ur = item.get("user_results")
            if ur:
                users.append(classify(ur))
    return users, _cursor_of(entries)


def parse_tweets_timeline(data: dict[str, Any]) -> tuple[list[dict], Optional[str]]:
    if not data.get("data"):
        return [], None
    entries = _entries(_find_instructions(data["data"]))
    tweets: list[dict] = []
    for e in entries:
        content = e.get("content") or {}
        if content.get("entryType") == "TimelineTimelineItem":
            item = content.get("itemContent") or {}
            tr = item.get("tweet_results", {}).get("result")
            if tr and tr.get("__typename") not in ("TweetTombstone", "TweetUnavailable"):
                tweets.append(tr)
        elif content.get("entryType") == "TimelineTimelineModule":
            for mi in content.get("items") or []:
                it = mi.get("item", {}).get("itemContent", {})
                tr = it.get("tweet_results", {}).get("result")
                if tr and tr.get("__typename") not in ("TweetTombstone", "TweetUnavailable"):
                    tweets.append(tr)
    return tweets, _cursor_of(entries)