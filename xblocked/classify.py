from __future__ import annotations

import time
from typing import Any

from .model import (
    STATUS_BLOCKED,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_SUSPENDED,
    STATUS_DEACTIVATED,
    STATUS_UNAVAILABLE,
    STATUS_UNKNOWN,
    CheckResult,
    classify,
    parse_tweets_timeline,
    parse_users_timeline,
)
from .rawclient import RawClient

__all__ = [
    "STATUS_BLOCKED",
    "STATUS_OK",
    "STATUS_SUSPENDED",
    "STATUS_DEACTIVATED",
    "STATUS_UNAVAILABLE",
    "STATUS_UNKNOWN",
    "STATUS_ERROR",
    "CheckResult",
    "classify",
]

BATCH_SLEEP_SECONDS = 1.0


def batch_check(client: RawClient, user_ids: list[str], batch_size: int = 100) -> list[CheckResult]:
    results: list[CheckResult] = []
    for start in range(0, len(user_ids), batch_size):
        chunk = user_ids[start : start + batch_size]
        try:
            chunk_results = client.users_by_rest_ids(chunk)
        except Exception as exc:  # noqa: BLE001
            for uid in chunk:
                results.append(CheckResult(user_id=uid, status=STATUS_ERROR, error=str(exc)))
            continue
        mapped = {r.user_id: r for r in chunk_results}
        for uid in chunk:
            if uid in mapped:
                results.append(mapped[uid])
            else:
                results.append(CheckResult(user_id=uid, status=STATUS_UNAVAILABLE, detail="not returned"))
        if BATCH_SLEEP_SECONDS > 0:
            time.sleep(BATCH_SLEEP_SECONDS)
    return results


def single_check_by_id(client: RawClient, user_id: str) -> CheckResult:
    try:
        res = client.users_by_rest_ids([user_id])
        return res[0] if res else CheckResult(user_id=user_id, status=STATUS_UNAVAILABLE, detail="no user")
    except Exception as exc:  # noqa: BLE001
        return CheckResult(user_id=user_id, status=STATUS_ERROR, error=str(exc))


def single_check_by_screen_name(client: RawClient, screen_name: str) -> CheckResult:
    try:
        return client.mobile_user(screen_name)
    except Exception as exc:  # noqa: BLE001
        try:
            return client.me(screen_name)
        except Exception as exc2:  # noqa: BLE001
            return CheckResult(screen_name=screen_name, status=STATUS_ERROR, error=f"{exc} | fallback: {exc2}")