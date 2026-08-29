from __future__ import annotations

import json
import time
from typing import Any, Optional

import httpx

from .model import CheckResult, classify, parse_tweets_timeline, parse_users_timeline
from .util import cookie_str

ACCESS_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
API_HOST = "https://x.com"


class RawClient:
    def __init__(self, cookies: dict[str, str], placeholder: dict[str, Any], timeout: int = 40):
        self.cookies = cookies
        self.placeholder = placeholder
        self.timeout = timeout
        # Standard web client headers. (The twitter_openapi_python get_header()
        # fetch is unreliable; these match the live x.com web client.)
        self.headers: dict[str, str] = {
            "authorization": f"Bearer {ACCESS_TOKEN}",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
            ),
            "accept": "*/*",
            "accept-encoding": "identity",
            "accept-language": "ja,en-US;q=0.9,en;q=0.8",
            "referer": "https://x.com/",
            "priority": "u=1, i",
            "x-twitter-client-language": "ja",
            "x-twitter-active-user": "yes",
        }
        self.headers["cookie"] = cookie_str(cookies)
        if cookies.get("ct0"):
            self.headers["x-csrf-token"] = cookies["ct0"]
        self.rate_remaining: Optional[int] = None
        self.rate_reset: Optional[int] = None
        self._session = httpx.Client(
            http2=True,
            headers=self.headers,
            timeout=self.timeout,
            limits=httpx.Limits(max_connections=48, max_keepalive_connections=24),
        )

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        last_exc: Exception | None = None
        backoffs = (0.05, 0.08, 0.15, 0.3, 0.6)
        for attempt in range(len(backoffs) + 1):
            try:
                return self._session.request(method, url, timeout=self.timeout, **kwargs)
            except (
                httpx.TransportError,
                ConnectionError,
                OSError,
            ) as exc:
                if isinstance(exc, httpx.TimeoutException):
                    raise
                if isinstance(exc, RuntimeError) and "deque mutated" not in str(exc):
                    raise
                # transient local socket errors (e.g. Windows WSAEWOULDBLOCK 10035)
                last_exc = exc
                if attempt < len(backoffs):
                    time.sleep(backoffs[attempt])
        assert last_exc is not None
        raise last_exc

    def _track_rate(self, resp: httpx.Response) -> None:
        try:
            self.rate_remaining = int(resp.headers.get("x-rate-limit-remaining", self.rate_remaining or -1))
            self.rate_reset = int(resp.headers.get("x-rate-limit-reset", self.rate_reset or 0))
        except (TypeError, ValueError):
            pass

    def graphql(self, key: str, variables: dict[str, Any], retries: int = 1) -> dict[str, Any]:
        flag = self.placeholder[key]
        params = {
            "variables": json.dumps(flag.get("variables", {}) | variables),
            "features": json.dumps(flag.get("features", {})),
            "fieldToggles": json.dumps(flag.get("fieldToggles", {})),
        }
        url = API_HOST + flag["@path"]
        for attempt in range(retries + 1):
            resp = self._request("GET", url, params=params)
            if resp.status_code == 429 and attempt < retries:
                time.sleep(60)
                continue
            if resp.status_code != 200:
                raise _ApiError(resp.status_code, resp.text[:500])
            self._track_rate(resp)
            return resp.json()
        raise _ApiError(429, "rate limited")

    def me(self, screen_name: str) -> CheckResult:
        data = self.graphql("UserByScreenName", {"screen_name": screen_name})
        node = (data.get("data") or {}).get("user", {}).get("result") if data.get("data") else None
        return classify(node)

    MOBILE_USER_OP_ID = "DuN4Qld4UROZ63wKFX8cfw"
    MOBILE_USER_OP_NAME = "GetUserByScreenNameQuery"

    _shared_tid: Optional[str] = None
    _shared_tid_at: float = 0.0
    _tid_broken: bool = False
    _TID_TTL = 90.0

    def _get_shared_tid(self) -> Optional[str]:
        now = time.time()
        if self._shared_tid and now - self._shared_tid_at < self._TID_TTL:
            return self._shared_tid
        if self._tid_broken:
            return None
        try:
            from .tid_gen import generate_tid
            tid = generate_tid("/graphql", "GET")
            if tid:
                self._shared_tid = tid
                self._shared_tid_at = now
                return tid
            self._tid_broken = True
        except Exception:
            self._tid_broken = True
        return None

    def mobile_user(self, screen_name: str, use_tid: bool = True) -> CheckResult:
        path = f"/graphql/{self.MOBILE_USER_OP_ID}/{self.MOBILE_USER_OP_NAME}"
        params = {
            "variables": json.dumps({
                "screen_name": screen_name,
                "include_profile_info": True,
                "include_can_pay": False,
            })
        }
        headers = dict(self.headers)
        if use_tid:
            tid = self._get_shared_tid()
            if tid:
                headers["x-client-transaction-id"] = tid
        url = API_HOST + path
        for attempt in range(2):
            resp = self._request("GET", url, params=params, headers=headers)
            if resp.status_code == 429 and attempt < 1:
                time.sleep(60)
                continue
            if resp.status_code != 200:
                raise _ApiError(resp.status_code, resp.text[:200])
            self._track_rate(resp)
            d = resp.json()
            node = ((d.get("data") or {}).get("user_result") or {}).get("result")
            return classify(node)
        raise _ApiError(429, "rate limited")

    def users_by_rest_ids(self, user_ids: list[str]) -> list[CheckResult]:
        data = self.graphql("UsersByRestIds", {"userIds": user_ids})
        d = data.get("data") or {}
        return [classify(entry.get("result")) for entry in d.get("users", [])]

    def following(self, user_id: str, cursor: Optional[str] = None, count: int = 20) -> tuple[list[CheckResult], Optional[str]]:
        return parse_users_timeline(self.graphql("Following", {"userId": user_id, "count": count, "includePromotedContent": False, "cursor": cursor}))

    def followers(self, user_id: str, cursor: Optional[str] = None, count: int = 20) -> tuple[list[CheckResult], Optional[str]]:
        return parse_users_timeline(self.graphql("Followers", {"userId": user_id, "count": count, "includePromotedContent": False, "cursor": cursor}))

    def user_tweets_and_replies(self, user_id: str, cursor: Optional[str] = None, count: int = 20) -> tuple[list[dict], Optional[str]]:
        return parse_tweets_timeline(self.graphql("UserTweetsAndReplies", {"userId": user_id, "count": count, "includePromotedContent": True, "withCommunity": True, "withVoice": True, "cursor": cursor}))

    def user_tweets(self, user_id: str, cursor: Optional[str] = None, count: int = 20) -> tuple[list[dict], Optional[str]]:
        return parse_tweets_timeline(self.graphql("UserTweets", {"userId": user_id, "count": count, "includePromotedContent": True, "withVoice": True, "cursor": cursor}))

    def search(self, query: str, cursor: Optional[str] = None, count: int = 20) -> tuple[list[dict], Optional[str]]:
        return parse_tweets_timeline(self.graphql("SearchTimeline", {"rawQuery": query, "count": count, "querySource": "typed_query", "product": "Latest", "cursor": cursor}))

    def likes(self, user_id: str, cursor: Optional[str] = None, count: int = 20) -> tuple[list[dict], Optional[str]]:
        return parse_tweets_timeline(self.graphql("Likes", {"userId": user_id, "count": count, "includePromotedContent": False, "withVoice": True, "cursor": cursor}))

    def bookmarks(self, cursor: Optional[str] = None, count: int = 20) -> tuple[list[dict], Optional[str]]:
        return parse_tweets_timeline(self.graphql("Bookmarks", {"count": count, "includePromotedContent": True, "cursor": cursor}))

    def connect_tab(self, cursor: Optional[str] = None, count: int = 20) -> tuple[list[dict], Optional[str]]:
        return parse_tweets_timeline(self.graphql("ConnectTabTimeline", {"count": count, "cursor": cursor}))

    def notifications(self, cursor: Optional[str] = None, count: int = 20) -> tuple[list[dict], Optional[str]]:
        return parse_tweets_timeline(self.graphql("NotificationsTimeline", {"timeline_type": "All", "count": count, "cursor": cursor}))

    def tweet_thread(self, focal_tweet_id: str, cursor: Optional[str] = None, count: int = 20) -> tuple[list[dict], Optional[str]]:
        return parse_tweets_timeline(self.graphql("TweetDetail", {"focalTweetId": focal_tweet_id, "cursor": cursor, "referrer": "home"}))

    def followers_you_know(self, user_id: str, cursor: Optional[str] = None, count: int = 20) -> tuple[list[CheckResult], Optional[str]]:
        return parse_users_timeline(self.graphql("FollowersYouKnow", {"userId": user_id, "count": count, "includePromotedContent": False, "cursor": cursor}))

    def favoriters(self, tweet_id: str, cursor: Optional[str] = None, count: int = 20) -> tuple[list[CheckResult], Optional[str]]:
        return parse_users_timeline(self.graphql("Favoriters", {"tweetId": tweet_id, "count": count, "includePromotedContent": False, "cursor": cursor}))

    def retweeters(self, tweet_id: str, cursor: Optional[str] = None, count: int = 20) -> tuple[list[CheckResult], Optional[str]]:
        return parse_users_timeline(self.graphql("Retweeters", {"tweetId": tweet_id, "count": count, "includePromotedContent": False, "cursor": cursor}))

    def friendship_check(self, me_id: str, target_id: Optional[str] = None, target_screen_name: Optional[str] = None) -> CheckResult:
        from .model import STATUS_BLOCKED, STATUS_ERROR, STATUS_DEACTIVATED, STATUS_UNAVAILABLE

        params: dict[str, Any] = {"source_id": me_id}
        if target_id:
            params["target_id"] = target_id
        else:
            params["target_screen_name"] = target_screen_name
        url = "https://api.x.com/1.1/friendships/show.json"
        resp = self._request("GET", url, params=params)
        if resp.status_code == 404:
            return CheckResult(user_id=target_id, screen_name=target_screen_name, status=STATUS_DEACTIVATED, detail="not found")
        if resp.status_code == 429:
            raise _ApiError(429, "rate limited")
        if resp.status_code != 200:
            raise _ApiError(resp.status_code, resp.text[:200])
        self._track_rate(resp)
        rel = (resp.json().get("relationship") or {}).get("source") or {}
        blocked_by = bool(rel.get("blocked_by"))
        return CheckResult(
            user_id=str(rel.get("id_str") or target_id or ""),
            screen_name=rel.get("screen_name") or target_screen_name or "",
            name=rel.get("screen_name") or "",
            status=STATUS_BLOCKED if blocked_by else "OK",
            blocked_by=blocked_by,
            detail="" if blocked_by else "connection ok",
        )


class _ApiError(RuntimeError):
    def __init__(self, status: int, text: str):
        super().__init__(f"HTTP {status}: {text[:200]}")
        self.status = status
        self.text = text