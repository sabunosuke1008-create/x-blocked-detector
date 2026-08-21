from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "cookies": {"auth_token": "", "ct0": ""},
    "me": {"screen_name": "", "user_id": ""},
    "limits": {
        "max_following": 0,
        "max_followers": 0,
        "max_own_tweets": 100,
        "max_likes": 100,
        "max_bookmarks": 100,
        "max_connect": 50,
        "max_notifications": 50,
        "max_followers_you_know": 0,
        "max_tweet_threads": 10,
        "max_favoriters": 0,
        "max_retweeters": 0,
        "use_search": False,
        "search_queries": [],
    },
    "delay_seconds": 1.0,
    "batch_size": 100,
    "concurrency": 6,
    "tid_mode": "auto",
    "output_csv": "blocked_report.csv",
    "state_file": ".state.json",
}


@dataclass
class Config:
    cookies: dict[str, str]
    me_screen_name: str
    me_user_id: str = ""
    limits: dict[str, Any] = field(default_factory=dict)
    delay_seconds: float = 1.0
    batch_size: int = 100
    concurrency: int = 6
    tid_mode: str = "auto"
    output_csv: str = "blocked_report.csv"
    state_file: str = ".state.json"

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        merged = DEFAULT_CONFIG | raw
        limits = DEFAULT_CONFIG["limits"] | (merged["limits"] or {})
        me = merged["me"] or {}
        return cls(
            cookies=merged["cookies"] or {},
            me_screen_name=str(me.get("screen_name", "")),
            me_user_id=str(me.get("user_id", "")),
            limits=limits,
            delay_seconds=float(merged.get("delay_seconds", 1.0)),
            batch_size=int(merged.get("batch_size", 100)),
            concurrency=int(merged.get("concurrency", 6)),
            tid_mode=str(merged.get("tid_mode", "auto")),
            output_csv=str(merged.get("output_csv", "blocked_report.csv")),
            state_file=str(merged.get("state_file", ".state.json")),
        )

    def limit(self, key: str, default: int = 0) -> int:
        return int(self.limits.get(key, default))

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.cookies.get("auth_token"):
            problems.append("cookies.auth_token is not set")
        if not self.cookies.get("ct0"):
            problems.append("cookies.ct0 is not set")
        if not self.me_screen_name:
            problems.append("me.screen_name is not set (handle without @)")
        return problems