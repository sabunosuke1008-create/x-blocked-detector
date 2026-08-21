from __future__ import annotations

from .classify import CheckResult
from .query_ids import apply_query_ids, current_query_ids, fetch_placeholder
from .rawclient import RawClient

ME_URL = "https://x.com/home"


def build_client(
    cookies: dict[str, str],
    tid_mode: str = "auto",
    refresh_query_ids: bool = False,
) -> RawClient:
    placeholder = fetch_placeholder()
    ids = current_query_ids(cookies, refresh=refresh_query_ids)
    applied = apply_query_ids(placeholder, ids)
    if applied:
        print(f"[client] applied {applied} fresh query ids", flush=True)
    return RawClient(cookies=cookies, placeholder=placeholder)


def resolve_me(client: RawClient, me_screen_name: str, me_user_id: str = "") -> tuple[str, str]:
    result = client.me(me_screen_name)
    if result.status not in ("OK", "BLOCKED_BY") or not result.user_id:
        raise RuntimeError(f"could not resolve @{me_screen_name} ({result.detail} {result.error})")
    return result.user_id, result.screen_name or me_screen_name