from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

_PLAYWRIGHT_CLI = r"C:\Users\otame\AppData\Roaming\npm\playwright-cli.cmd"
_TID_SCRIPT = Path(__file__).parent / "pw_tid_gen.js"
_TID_PATH_RE = re.compile(r'"tid"\s*:\s*"([^"]+)"')

_session_opened = False


def _ensure_browser() -> None:
    global _session_opened
    if _session_opened:
        return
    subprocess.run(
        [_PLAYWRIGHT_CLI, "open", "https://x.com/home"],
        capture_output=True, text=True, timeout=30,
    )
    _session_opened = True


def generate_tid(path: str, method: str = "GET") -> Optional[str]:
    return generate_tids([(path, method)]).get((path, method))


def generate_tids(paths: list[tuple[str, str]]) -> dict[tuple[str, str], Optional[str]]:
    _ensure_browser()
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

    result = subprocess.run(
        [_PLAYWRIGHT_CLI, "run-code", "--filename=" + str(tmp)],
        capture_output=True, text=True, timeout=60,
    )
    tmp.unlink(missing_ok=True)

    m = _TID_PATH_RE.search(result.stdout)
    out: dict[tuple[str, str], Optional[str]] = {}
    if m and len(m.group(1)) > 50:
        for p, meth in paths:
            out[(p, meth)] = m.group(1)
    else:
        for p, meth in paths:
            out[(p, meth)] = None
    return out


def close_browser() -> None:
    global _session_opened
    try:
        subprocess.run([_PLAYWRIGHT_CLI, "close"], capture_output=True, timeout=15)
    except Exception:
        pass
    _session_opened = False