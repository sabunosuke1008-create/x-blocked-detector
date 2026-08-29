"""CloakBrowser-based X login via raw CDP (no Playwright dependency).

Proven 2026 JFAPI flow: email -> knowledge_check(username) -> [verify_code
-> "Use password" switch] -> password -> home.

The JF SPA keeps old screens in the DOM, so every selector targets the LAST
matching element and inputs are set through the React-compatible native
setter + input events (mirrors what a real browser does).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from typing import Optional

import websocket

from .auth_login import LoginError, LoginResult, credentials_from

_SEL_EMAIL = "username_or_email"
_SEL_KC = "challenge_response"
_SEL_PWD = "password"


def _headless() -> bool:
    # Headed Chrome ignores --remote-debugging-address (binds 127.0.0.1 only),
    # and loopback is unreliable on some setups; default to headless where
    # 0.0.0.0 binding works and the LAN IP is reachable.
    return os.environ.get("XB_LOGIN_HEADLESS", "1") == "1"


class _CDP:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=10,
                                              origin="http://127.0.0.1")
        self._id = 0

    def cmd(self, method: str, params: Optional[dict] = None, timeout: float = 20) -> dict:
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = json.loads(self.ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"])[:150])
                return msg.get("result", {})
        raise TimeoutError(method)

    def js(self, expr: str, timeout: float = 15):
        r = self.cmd("Runtime.evaluate",
                     {"expression": expr, "returnByValue": True}, timeout)
        val = r.get("result", {}).get("value")
        if r.get("exceptionDetails"):
            raise RuntimeError(str(r["exceptionDetails"].get("text"))[:120])
        return val

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def _js_str(s: str) -> str:
    return json.dumps(s)


def _submit_form(cdp: _CDP, input_name: str, texts: tuple[str, ...],
                 timeout_s: float = 3.0) -> bool:
    """Click the submit control INSIDE the form that owns the given input.

    Each JF screen is its own <form>; clicking a text-matched control
    anywhere on the page would hit another screen's button.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for t in texts:
            expr = (
                "((name, txt) => { const els = document.querySelectorAll"
                "(\"input[name='\" + name + \"']\"); const e = els[els.length - 1];"
                " if (!e) return 'no-input'; const form = e.closest('form');"
                " if (!form) return 'no-form';"
                " const alive = x => x.offsetParent !== null;"
                " const btns = [...form.querySelectorAll('button, [role=\"button\"]')]"
                ".filter(x => alive(x) && x.textContent.trim() === txt);"
                " if (btns.length) { btns[btns.length - 1].click(); return 'button'; }"
                " const spans = [...form.querySelectorAll('div, span')]"
                ".filter(x => alive(x) && x.childElementCount === 0 &&"
                " x.textContent.trim() === txt);"
                " if (spans.length) { spans[spans.length - 1].click(); return 'text'; }"
                " return null; })(" + _js_str(input_name) + ", " + _js_str(t) + ")"
            )
            try:
                if cdp.js(expr, 8):
                    return True
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.3)
    return False


def _hash(cdp: _CDP) -> str:
    try:
        return str(cdp.js("location.hash", 8) or "")
    except Exception:  # noqa: BLE001
        return ""


def _wait_hash(cdp: _CDP, keywords: tuple[str, ...], timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        h = _hash(cdp)
        if any(k in h for k in keywords):
            return True
        time.sleep(0.4)
    return False


def _wait_input(cdp: _CDP, name: str, timeout_s: float) -> bool:
    """Wait until the LAST input with this name is visible and editable."""
    deadline = time.time() + timeout_s
    expr = (
        "(() => { const els = document.querySelectorAll"
        "(\"input[name='" + name + "']\"); const e = els[els.length - 1];"
        " if (!e) return false; const r = e.getBoundingClientRect();"
        " return r.width > 0 && r.height > 0 && !e.disabled && !e.readOnly; })()"
    )
    while time.time() < timeout_s * 0 + deadline:
        try:
            if cdp.js(expr, 8):
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.4)
    return False


def _set_value(cdp: _CDP, name: str, value: str, attempts: int = 3) -> bool:
    """Set an input value React-compatibly; verify it sticks."""
    expr = (
        "((name, val) => { const els = document.querySelectorAll"
        "(\"input[name='\" + name + \"']\"); const e = els[els.length - 1];"
        " if (!e) return 'missing'; e.focus();"
        " const setter = Object.getOwnPropertyDescriptor("
        "window.HTMLInputElement.prototype, 'value').set;"
        " setter.call(e, ''); e.dispatchEvent(new Event('input', {bubbles: true}));"
        " setter.call(e, val); e.dispatchEvent(new Event('input', {bubbles: true}));"
        " e.dispatchEvent(new Event('change', {bubbles: true}));"
        " return e.value === val ? 'ok' : 'failed'; })"
        "(" + _js_str(name) + ", " + _js_str(value) + ")"
    )
    for _ in range(attempts):
        try:
            if cdp.js(expr, 10) == "ok":
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.6)
    return False


def _click_text(cdp: _CDP, texts: tuple[str, ...], timeout_s: float = 3.0) -> bool:
    """Click a control by exact visible text (button/[role=button] first)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for t in texts:
            expr = (
                "((txt) => { const alive = e => e && e.offsetParent !== null;"
                " const btns = [...document.querySelectorAll"
                "('button, [role=\"button\"]')].filter(e => alive(e) &&"
                " e.textContent.trim() === txt);"
                " if (btns.length) { btns[btns.length - 1].click(); return 'button'; }"
                " const spans = [...document.querySelectorAll('div, span')]"
                ".filter(e => alive(e) && e.childElementCount === 0 &&"
                " e.textContent.trim() === txt);"
                " if (spans.length) { spans[spans.length - 1].click(); return 'text'; }"
                " return null; })" + "(" + _js_str(t) + ")"
            )
            try:
                if cdp.js(expr, 8):
                    return True
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.3)
    return False


def _submit_and_wait(cdp: _CDP, name: str, wait_s: float = 6.0) -> bool:
    if not _click_text(cdp, ("続ける", "ログイン", "Next", "Log in"), 1500):
        expr = ("(() => { const els = document.querySelectorAll"
                "(\"input[name='" + name + "']\"); const e = els[els.length - 1];"
                " if (!e) return; e.dispatchEvent(new KeyboardEvent("
                "'keydown', {key: 'Enter', bubbles: true}));"
                " e.form && e.form.requestSubmit && e.form.requestSubmit(); })()")
        try:
            cdp.js(expr, 8)
        except Exception:  # noqa: BLE001
            pass
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            gone = cdp.js(
                "(() => { const els = document.querySelectorAll"
                "(\"input[name='" + name + "']\"); const e = els[els.length - 1];"
                " if (!e) return true; const r = e.getBoundingClientRect();"
                " return !(r.width > 0 && r.height > 0); })()", 8)
            if gone:
                return True
        except Exception:  # noqa: BLE001
            return True
        time.sleep(0.3)
    return False


def _fill_and_submit(cdp: _CDP, name: str, value: str, tries: int = 3) -> bool:
    for _ in range(tries):
        if not _set_value(cdp, name, value):
            continue
        if _submit_and_wait(cdp, name):
            return True
    return False


def _screen_text(cdp: _CDP) -> str:
    try:
        return str(cdp.js(
            "(() => { const d = document.querySelectorAll"
            "(\"div[role='dialog']\"); return d.length ?"
            " d[d.length - 1].innerText : ''; })()", 8) or "")
    except Exception:  # noqa: BLE001
        return ""


def run_login_browser(cfg, headless: Optional[bool] = None) -> LoginResult:
    email, username, password, _totp = credentials_from(cfg)
    if not (email or username) or not password:
        raise LoginError(
            "missing credentials: config auth{} or env XB_LOGIN_EMAIL/USERNAME/PASSWORD"
        )
    if headless is None:
        headless = _headless()

    from cloakbrowser import build_args, ensure_binary

    exe = ensure_binary()
    args = build_args(stealth_args=True, extra_args=None, locale="ja-JP",
                       headless=headless)
    user_dir = tempfile.mkdtemp(prefix="xb_login_")
    # 127.0.0.1 loopback can be blocked machine-wide (VPN kill-switch
    # filters), so expose CDP on all interfaces and use the LAN IP.
    import socket as _socket
    import random
    _probe = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        _probe.connect(("8.8.8.8", 80))
        local_ip = _probe.getsockname()[0]
    except Exception:  # noqa: BLE001
        local_ip = "127.0.0.1"
    finally:
        _probe.close()
    port = random.randint(9400, 9499)
    launch_args = [
        exe, *args,
        f"--user-data-dir={user_dir}",
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=0.0.0.0",
        "--remote-allow-origins=*",
        "--no-first-run", "about:blank",
    ]
    proc = subprocess.Popen(launch_args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    cdp: Optional[_CDP] = None
    try:
        # wait for CDP
        ws_url = None
        base = f"http://{local_ip}:{port}"
        deadline = time.time() + 45
        while time.time() < deadline and ws_url is None:
            if proc.poll() is not None:
                raise LoginError("cloak browser exited during launch")
            try:
                req = urllib.request.Request(
                    f"{base}/json/list",
                    headers={"Accept": "application/json"})
                targets = json.loads(urllib.request.urlopen(req, timeout=5).read())
                for t in targets:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        ws_url = t["webSocketDebuggerUrl"].replace("127.0.0.1", local_ip)
                        break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        if ws_url is None:
            raise LoginError("CDP endpoint did not come up")

        cdp = _CDP(ws_url)
        cdp.cmd("Page.enable")
        cdp.cmd("Runtime.enable")
        cdp.cmd("Page.navigate", {"url": "https://x.com/i/flow/login"}, 30)
        if not _wait_input(cdp, _SEL_EMAIL, 40):
            raise LoginError(f"login page did not render; screen={_screen_text(cdp)[:100]!r}")

        # step 1: identifier (screen detection via URL hash - the JF SPA
        # keeps old screens in the DOM, so input presence is meaningless)
        if not _set_value(cdp, _SEL_EMAIL, email or username):
            raise LoginError(f"could not type identifier; screen={_screen_text(cdp)[:120]!r}")
        _submit_form(cdp, _SEL_EMAIL, ("続ける", "Next"))
        if not _wait_hash(cdp, ("knowledge_check", "login_enter_password", "verify_code"), 20):
            raise LoginError(f"identifier submit did not advance; screen={_screen_text(cdp)[:120]!r}")

        # step 2: knowledge check (optional)
        if "knowledge_check" in _hash(cdp):
            if not username:
                raise LoginError("knowledge check requires the account username")
            if not _set_value(cdp, _SEL_KC, username):
                raise LoginError("could not type knowledge check answer")
            _submit_form(cdp, _SEL_KC, ("続ける", "Next"))
            if not _wait_hash(cdp, ("login_enter_password", "verify_code"), 20):
                screen = _screen_text(cdp)
                if "正しくありません" in screen:
                    raise LoginError("knowledge check rejected the username")
                raise LoginError(f"knowledge check did not advance; screen={screen[:120]!r}")

        # step 3: verify_code screen -> switch to password
        deadline = time.time() + 15
        while "verify_code" in _hash(cdp) and time.time() < deadline:
            if _click_text(cdp, ("パスワードを使用", "Use password"), 1500):
                time.sleep(1)

        # step 4: password
        if "login_enter_password" not in _hash(cdp):
            _wait_hash(cdp, ("login_enter_password",), 10)
        if "login_enter_password" not in _hash(cdp):
            raise LoginError(f"password screen not reached; screen={_screen_text(cdp)[:120]!r}")
        if not _set_value(cdp, _SEL_PWD, password):
            raise LoginError("could not type password")
        _submit_form(cdp, _SEL_PWD, ("続ける", "ログイン", "Next", "Log in"))

        # step 5: completion = cookies present (source of truth)
        cdp.cmd("Network.enable")
        deadline = time.time() + 30
        picked: dict[str, str] = {}
        user_id: Optional[str] = None
        while time.time() < deadline:
            time.sleep(1)
            try:
                cookies = cdp.cmd("Network.getAllCookies", {}, 15).get("cookies", [])
            except Exception:  # noqa: BLE001
                continue
            picked = {}
            for c in cookies:
                if c["name"] in ("auth_token", "ct0"):
                    picked[c["name"]] = c["value"]
                if c["name"] == "twid" and c.get("value", "").startswith("u%3D"):
                    user_id = urllib.parse.unquote(c["value"])[2:]
            if picked.get("auth_token") and picked.get("ct0"):
                break
        if not picked.get("auth_token"):
            raise LoginError(
                f"login did not complete (url={str(cdp.js('location.href', 8))[:80]!r} "
                f"screen={_screen_text(cdp)[:150]!r})")
        return LoginResult(cookies=picked, user_id=user_id, screen_name=username or None)
    finally:
        if cdp is not None:
            cdp.close()
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass