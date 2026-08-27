from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .classify import STATUS_BLOCKED, STATUS_ERROR, STATUS_SUSPENDED
from .runner import Outcome


def summarize(outcomes: list[Outcome]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for o in outcomes:
        counts[o.result.status] = counts.get(o.result.status, 0) + 1
    return counts


def print_report(outcomes: list[Outcome], skipped: list[str]) -> None:
    counts = summarize(outcomes)
    print("===== blocked-by scan report =====")
    for status in sorted(counts):
        print(f"  {status:<14}: {counts[status]}")
    if skipped:
        print("  skipped sources:")
        for s in skipped:
            print(f"    - {s}")
    blocked = [o for o in outcomes if o.result.status == STATUS_BLOCKED]
    print(f"----- BLOCKED_BY ({len(blocked)}) -----")
    for o in sorted(blocked, key=lambda o: o.candidate.screen_name.lower()):
        c, r = o.candidate, o.result
        print(f"  @{c.screen_name or '?'}  {c.name or ''}  [id={c.user_id}]  sources={','.join(sorted(c.sources))}")
    print(f"----- ERROR / SUSPENDED (reference) -----")
    for o in outcomes:
        if o.result.status in (STATUS_ERROR, STATUS_SUSPENDED):
            print(f"  [{o.result.status}] @{o.candidate.screen_name or '?'} detail={o.result.detail} error={o.result.error}")


def to_csv(outcomes: list[Outcome], path: str | Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["user_id", "screen_name", "name", "status", "blocked_by", "detail", "error", "sources", "cached"])
        for o in outcomes:
            c, r = o.candidate, o.result
            writer.writerow(
                [
                    c.user_id,
                    c.screen_name,
                    c.name,
                    r.status,
                    r.blocked_by,
                    r.detail,
                    r.error,
                    ",".join(sorted(c.sources)),
                    "yes" if o.cached else "",
                ]
            )


def load_state(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(path: str | Path, outcomes: list[Outcome]) -> None:
    prev = load_state(path)
    prev_accounts = prev.get("accounts", {}) if isinstance(prev, dict) else {}
    now_ts = int(time.time())
    accounts = {}
    for o in outcomes:
        key = o.candidate.user_id or o.candidate.screen_name
        old = prev_accounts.get(key)
        old_ts = old.get("ts") if isinstance(old, dict) else None
        # ts = last time this verdict was observed live. Cached hits keep the
        # original observation time so the TTL window is not stretched.
        ts = old_ts if (o.cached and isinstance(old_ts, int)) else now_ts
        accounts[key] = {
            "status": o.result.status,
            "screen_name": o.candidate.screen_name,
            "ts": ts,
        }
    payload = {"scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "accounts": accounts}
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def diff_state(
    path: str | Path,
    outcomes: list[Outcome],
) -> tuple[list[str], list[str]]:
    prev = load_state(path)
    prev_accounts = prev.get("accounts", {})
    now = {o.candidate.user_id or o.candidate.screen_name: o.result.status for o in outcomes}
    newly_blocked: list[str] = []
    newly_unblocked: list[str] = []
    for key, status in now.items():
        old = prev_accounts.get(key, {}).get("status") if isinstance(prev_accounts.get(key), dict) else None
        if status == STATUS_BLOCKED and old != STATUS_BLOCKED:
            newly_blocked.append(key)
        elif old == STATUS_BLOCKED and status != STATUS_BLOCKED:
            newly_unblocked.append(key)
    return newly_blocked, newly_unblocked