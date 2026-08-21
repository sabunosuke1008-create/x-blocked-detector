from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import bs4
import requests
from x_client_transaction.utils import generate_headers

from .util import cookie_str

PLACEHOLDER_URL = (
    "https://raw.githubusercontent.com/fa0311/twitter-openapi/"
    "590dae5c9f8575abc91d3774946bfe6f23960aba/src/config/placeholder.json"
)

OPS = [
    "UserByScreenName",
    "UsersByRestIds",
    "UserByRestId",
    "Following",
    "Followers",
    "UserTweetsAndReplies",
    "UserTweets",
    "SearchTimeline",
    "Bookmarks",
    "Likes",
]

_CACHE_FILE = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "xblocked" / "query_ids.json"
_TTL_SECONDS = 6 * 3600

_OP_PAIR = re.compile(
    r'(?:operationName:"([A-Za-z0-9_]+)"\s*,\s*(?:queryId|persistedQuery):"?([A-Za-z0-9_-]{20,25})"?'
    r'|(?:queryId|persistedQuery):"?([A-Za-z0-9_-]{20,25})?"?\s*,\s*operationName:"([A-Za-z0-9_]+)")'
)
_MAIN_JS = re.compile(r"main\.[0-9a-f]+\.js")


def _load_cache() -> dict:
    if not _CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        if time.time() - data.get("fetched_at", 0) < _TTL_SECONDS:
            return data.get("ids", {})
    except Exception:
        pass
    return {}


def _save_cache(ids: dict) -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps({"fetched_at": time.time(), "ids": ids}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def fetch_current_query_ids(cookies: dict[str, str], timeout: int = 90) -> dict[str, str]:
    headers = generate_headers()
    headers["cookie"] = cookie_str(cookies)
    if cookies.get("ct0"):
        headers["x-csrf-token"] = cookies["ct0"]
    session = requests.Session()
    session.headers = headers

    page = session.get("https://x.com/home", timeout=timeout)
    soup = bs4.BeautifulSoup(page.text, "html.parser")
    main_url = None
    for sc in soup.find_all("script"):
        src = sc.get("src") or ""
        if _MAIN_JS.search(src) and src.endswith(".js"):
            main_url = src if src.startswith("http") else "https://x.com" + src
            break
    if main_url is None:
        raise RuntimeError("main web bundle not found in page")

    js = session.get(main_url, timeout=timeout).text
    found: dict[str, str] = {}
    for m in _OP_PAIR.finditer(js):
        op = m.group(1) or m.group(4)
        qid = m.group(2) or m.group(3)
        if op and qid:
            found[op] = qid
    if not found:
        raise RuntimeError("no queryId mappings found in main bundle")
    return found


def current_query_ids(cookies: dict[str, str], refresh: bool = False, timeout: int = 90) -> dict[str, str]:
    if not refresh:
        cached = _load_cache()
        if cached:
            return cached
    ids = fetch_current_query_ids(cookies, timeout=timeout)
    _save_cache(ids)
    return ids


def apply_query_ids(placeholder: dict, ids: dict[str, str]) -> int:
    applied = 0
    for key, flag in placeholder.items():
        if not isinstance(flag, dict):
            continue
        new_id = ids.get(key)
        if not new_id or not flag.get("queryId"):
            continue
        if flag["queryId"] == new_id:
            continue
        flag["queryId"] = new_id
        path = flag.get("@path")
        if path:
            flag["@path"] = re.sub(r"/graphql/[A-Za-z0-9_-]+/", f"/graphql/{new_id}/", path)
        applied += 1
    return applied


import urllib.request

def fetch_placeholder() -> dict:
    with urllib.request.urlopen(PLACEHOLDER_URL, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))