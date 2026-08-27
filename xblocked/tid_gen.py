from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

_TID_SCRIPT = Path(__file__).parent / "pw_tid_gen.js"
_TID_PATH_RE = re.compile(r'"tid"\s*:\s*"([^"]+)"')
_session_opened = False


def _resolve_playwright_argv() -> Optional[list[str]]:
    """Locate the playwright-cli tool without hardcoding a machine path.

    Order: XB_PLAYWRIGHT_CLI env var -> PATH lookup for `playwright-cli` ->
    `npx playwright cli` (npx on PATH). Returns None when unavailable; callers
    then degrade gracefully (TID not generated).
    """
    override = os.environ.get("XB_PLAYWRIGHT_CLI")
    if override:
        return [override]
    found = shutil.which("playwright-cli")
    if found:
        return [found]
    npx = shutil.which("npx")
    if npx:
        return [npx, "playwright", "cli"]
    return None


def _ensure_browser() -> None:
    global _session_opened
    if _session_opened:
        return
    base = _resolve_playwright_argv()
    if base is None:
        return
    argv = [*base, "open", "https://x.com/home"]
    try:
        subprocess.run(
            argv,
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
        _session_opened = True
    except Exception:
        _session_opened = False


def generate_tid(path: str, method: str = "GET") -> Optional[str]:
    return generate_tids([(path, method)]).get((path, method))


def generate_tids(paths: list[tuple[str, str]]) -> dict[tuple[str, str], Optional[str]]:
    out: dict[tuple[str, str], Optional[str]] = {}
    for p, meth in paths:
        out[(p, meth)] = None
    base = _resolve_playwright_argv()
    if base is None:
        return out
    try:
        _ensure_browser()
    except Exception:
        return out
    if not _session_opened:
        return out
    script = _TID_SCRIPT.read_text(encoding="utf-8")
    script = script.replace(
        "window.__TX_PATH__",
        json.dumps(paths[0][0]),
    ).replace(
        "window.__TX_METHOD__",
        json.dumps(paths[0][1]),
    )
    tmp = _TID_SCRIPT.parent / "pw_tid_tmp.js"
    tmp.write_text(script, encoding="utf-8")

    argv = [*base, "--raw", "run-code", "--filename=" + str(tmp)]
    try:
        result = subprocess.run(
            argv,
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace",
        )
    except Exception:
        tmp.unlink(missing_ok=True)
        close_browser()
        return out
    tmp.unlink(missing_ok=True)

    # playwright-cli --raw emits the JS result as a JSON-encoded string:
    #   '"{\"tid\":\"...\"}"'  -> json.loads x2 -> {"tid": "..."}
    tid: Optional[str] = None
    try:
        inner = json.loads(result.stdout.strip())
        if isinstance(inner, str):
            data = json.loads(inner)
            if isinstance(data, dict):
                tid = data.get("tid")
    except Exception:
        m = _TID_PATH_RE.search(result.stdout)
        if m:
            tid = m.group(1)
    if tid and len(tid) > 50:
        for key in out:
            out[key] = tid
    return out


def close_browser() -> None:
    global _session_opened
    try:
        base = _resolve_playwright_argv()
        if base is not None:
            subprocess.run([*base, "close"], capture_output=True, timeout=15,
                           encoding="utf-8", errors="replace")
    except Exception:
        pass
    _session_opened = False