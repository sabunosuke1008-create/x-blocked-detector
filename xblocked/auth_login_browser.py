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
import socket
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
    # Headed (default) is stealthier and renders identically to a real
    # browser; the CDP port is self-calibrated so loopback filtering on
    # some ports is avoided. XB_LOGIN_HEADLESS=1 forces headless.
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


def _real_click_dialog(cdp: _CDP, texts: tuple[str, ...],
                       timeout_s: float = 4.0) -> bool:
    """Real-mouse-click a button (by text) inside the ACTIVE dialog."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for t in texts:
            try:
                pos = cdp.js(
                    '((txt) => { const ds = [...document.querySelectorAll'
                    '("div[role=dialog]")];'
                    ' for (let i = ds.length - 1; i >= 0; i--) {'
                    ' const btns = [...ds[i].querySelectorAll("button, [role=button]")]'
                    '.filter(b => b.textContent.trim() === txt &&'
                    ' b.offsetParent !== null && !b.disabled);'
                    ' const b = btns[btns.length - 1]; if (!b) continue;'
                    ' const r = b.getBoundingClientRect();'
                    ' if (r.width > 30 && r.height > 10) {'
                    ' const cx = r.x + r.width / 2, cy = r.y + r.height / 2;'
                    ' const hit = document.elementFromPoint(cx, cy);'
                    ' if (hit && (hit === b || b.contains(hit)))'
                    ' return {x: cx, y: cy}; } }'
                    ' return null; })(' + json.dumps(t) + ')', 6)
                if pos:
                    cdp.cmd("Input.dispatchMouseEvent",
                            {"type": "mouseMoved", "x": pos["x"], "y": pos["y"]}, 8)
                    cdp.cmd("Input.dispatchMouseEvent",
                            {"type": "mousePressed", "x": pos["x"], "y": pos["y"],
                             "button": "left", "clickCount": 1}, 8)
                    cdp.cmd("Input.dispatchMouseEvent",
                            {"type": "mouseReleased", "x": pos["x"], "y": pos["y"],
                             "button": "left", "clickCount": 1}, 8)
                    print(f"[login-browser] real-clicked {t!r} at "
                          f"({pos['x']:.0f},{pos['y']:.0f})", flush=True)
                    return True
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.4)
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


def _real_click_submit(cdp: _CDP, name: str, texts: tuple[str, ...],
                       timeout_s: float = 6.0) -> bool:
    """Wait for the form submit control to be enabled, then click it with
    real mouse events (React sometimes ignores synthetic .click())."""
    deadline = time.time() + timeout_s
    pos = None
    while time.time() < deadline:
        for t in texts:
            try:
                pos = cdp.js(
                    '((name, txt) => { const els = document.querySelectorAll'
                    '("input[name='" + name + "']"); const e = els[els.length - 1];'
                    ' if (!e) return null; const form = e.closest("form");'
                    ' if (!form) return null;'
                    ' const btns = [...form.querySelectorAll("button, [role=button]")]'
                    '.filter(b => b.textContent.trim() === txt &&'
                    ' !b.disabled && b.offsetParent !== null);'
                    ' const b = btns[btns.length - 1]; if (!b) return null;'
                    ' const r = b.getBoundingClientRect();'
                    ' if (r.width < 10) return null;'
                    ' return {x: r.x + r.width / 2, y: r.y + r.height / 2}; })'
                    '(' + json.dumps(name) + ', ' + json.dumps(t) + ')', 6)
                if pos:
                    break
            except Exception:  # noqa: BLE001
                pass
        if pos:
            break
        time.sleep(0.3)
    if not pos:
        return False
    try:
        cdp.cmd("Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": pos["x"], "y": pos["y"]}, 8)
        cdp.cmd("Input.dispatchMouseEvent",
                {"type": "mousePressed", "x": pos["x"], "y": pos["y"],
                 "button": "left", "clickCount": 1}, 8)
        cdp.cmd("Input.dispatchMouseEvent",
                {"type": "mouseReleased", "x": pos["x"], "y": pos["y"],
                 "button": "left", "clickCount": 1}, 8)
        return True
    except Exception:  # noqa: BLE001
        return False


def _input_gone(cdp: _CDP, name: str) -> bool:
    try:
        return bool(cdp.js(
            '(() => { const els = document.querySelectorAll'
            '("input[name='' + name + '']"); const e = els[els.length - 1];'
            ' if (!e) return true; const r = e.getBoundingClientRect();'
            ' return !(r.width > 0 && r.height > 0); })()', 8))
    except Exception:  # noqa: BLE001
        return True


def _fill_submit_real(cdp: _CDP, name: str, value: str,
                      texts: tuple[str, ...], tries: int = 4) -> bool:
    """Set a React input value and submit via real mouse click, retrying
    until the input leaves the active screen."""
    for _ in range(tries):
        if not _set_value(cdp, name, value):
            continue
        _real_click_submit(cdp, name, texts)
        deadline = time.time() + 6.0
        while time.time() < deadline:
            if _input_gone(cdp, name):
                return True
            time.sleep(0.3)
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

    last_err: Optional[Exception] = None
    for attempt in range(2):
        try:
            return _run_login_once(cfg, email, username, password, headless)
        except (LoginError, Exception) as exc:  # noqa: BLE001
            last_err = exc
            print(f"[login-browser] attempt {attempt + 1} failed: "
                  f"{str(exc)[:100]}", flush=True)
    raise last_err if last_err else LoginError("login failed")


def _run_login_once(cfg, email: str, username: str, password: str,
                    headless: bool) -> LoginResult:
    from cloakbrowser import build_args, ensure_binary

    exe = ensure_binary()
    args = build_args(stealth_args=True, extra_args=None, locale="ja-JP",
                       headless=headless)
    user_dir = tempfile.mkdtemp(prefix="xb_login_")

    # Some machines silently drop TCP connects on certain loopback ports
    # (security/VPN WFP filters). Self-calibrate: find a port where a real
    # connect() succeeds, so CDP is reachable regardless.
    def _pick_port() -> Optional[int]:
        candidates = (list(range(9220, 9240)) + list(range(8220, 8240))
                      + list(range(1024, 1064)) + list(range(2920, 2940)))
        for cand in candidates:
            try:
                srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                srv.bind(("127.0.0.1", cand))
                srv.listen(1)
                c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                c.settimeout(1.2)
                try:
                    c.connect(("127.0.0.1", cand))
                    c.close()
                    srv.close()
                    return cand
                except Exception:  # noqa: BLE001
                    c.close()
            except Exception:  # noqa: BLE001
                pass
            finally:
                try:
                    srv.close()
                except Exception:  # noqa: BLE001
                    pass
        return None

    port = _pick_port()
    if port is None:
        raise LoginError("no reachable loopback port found for CDP")
    local_ip = "127.0.0.1"
    launch_args = [
        exe, *args,
        f"--user-data-dir={user_dir}",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        "--window-size=1400,900",
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
        if not _fill_submit_real(cdp, _SEL_EMAIL, email or username,
                                 ("続ける", "Next")):
            raise LoginError(f"identifier step failed; screen={_screen_text(cdp)[:120]!r}")

        # step 2: knowledge check (optional) — try the "Use password" switch
        # first: it jumps straight to the password screen, skipping both the
        # username quiz and the email verification code.
        if "knowledge_check" in _hash(cdp):
            _real_click_dialog(cdp, ("パスワードを使用", "Use password"))
            time.sleep(1.5)
        if "login_enter_password" not in _hash(cdp) and "knowledge_check" in _hash(cdp):
            if not username:
                raise LoginError("knowledge check requires the account username")
            if not _fill_submit_real(cdp, _SEL_KC, username, ("続ける", "Next")):
                screen = _screen_text(cdp)
                if "正しくありません" in screen:
                    raise LoginError("knowledge check rejected the username")
                raise LoginError(f"knowledge check did not advance; screen={screen[:120]!r}")

        # step 3: verify_code screen -> switch to password
        deadline = time.time() + 15
        while "verify_code" in _hash(cdp) and time.time() < deadline:
            _real_click_dialog(cdp, ("パスワードを使用", "Use password"))
            time.sleep(1.5)

        # step 4: password (native form submit - avoids mis-aimed clicks
        # near the forgot-password link, which would reset the whole flow)
        if "login_enter_password" not in _hash(cdp):
            _wait_hash(cdp, ("login_enter_password",), 10)
        if "login_enter_password" not in _hash(cdp):
            raise LoginError(f"password screen not reached; screen={_screen_text(cdp)[:120]!r}")
        if not _set_value(cdp, _SEL_PWD, password):
            raise LoginError("could not type password")
        # submit exactly like a real user: focus the input, press Enter
        # via CDP key events (requestSubmit/mis-aimed clicks reset the flow)
        try:
            cdp.js(
                "(() => { const els = document.querySelectorAll"
                "(\"input[name='" + _SEL_PWD + "']\"); const e = els[els.length - 1];"
                " if (e) { e.focus(); return 'focused'; } return 'no-input'; })()", 8)
            cdp.cmd("Input.dispatchKeyEvent",
                    {"type": "keyDown", "key": "Enter", "code": "Enter",
                     "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}, 8)
            cdp.cmd("Input.dispatchKeyEvent",
                    {"type": "keyUp", "key": "Enter", "code": "Enter",
                     "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}, 8)
        except Exception:  # noqa: BLE001
            pass

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
            try:
                all_c = cdp.cmd("Network.getAllCookies", {}, 15).get("cookies", [])
                names = sorted({c["name"] for c in all_c})
            except Exception as exc:  # noqa: BLE001
                names = f"cookie dump failed: {str(exc)[:60]}"
            try:
                title = cdp.js("document.title", 8)
                cur = cdp.js("location.href", 8)
            except Exception:  # noqa: BLE001
                title, cur = "?", "?"
            raise LoginError(
                f"login did not complete (url={str(cur)[:80]!r} "
                f"title={str(title)[:40]!r} cookies={names})")
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