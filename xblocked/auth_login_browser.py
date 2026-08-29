"""CloakBrowser-based X login (proven path, 2026 JFAPI flow).

Validated flow: email -> knowledge_check(username) -> [verify_code ->
"Use password" switch] -> password -> home. Cookies (auth_token/ct0) are
extracted from the browser context (httpOnly included).

Key selectors (from live DOM):
  input[name='username_or_email'], input[name='challenge_response'],
  input[name='password']
"""
from __future__ import annotations

import os
import time
import urllib.parse
from typing import Optional

from .auth_login import LoginError, LoginResult, credentials_from

_SEL_EMAIL = "input[name='username_or_email']"
_SEL_KC = "input[name='challenge_response']"
_SEL_PWD = "input[name='password']"


def _headless() -> bool:
    return os.environ.get("XB_LOGIN_HEADLESS", "") == "1"


def _visible(page, sel: str, timeout_ms: int = 4000):
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        try:
            loc = page.locator(sel).last
            if loc.is_visible() and loc.is_editable():
                return loc
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.25)
    return None


def _click_text_button(page, texts: tuple[str, ...], timeout_ms: int = 2000) -> bool:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for t in texts:
            try:
                btn = page.get_by_role("button", name=t, exact=True).last
                if btn.is_visible():
                    btn.click()
                    return True
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.25)
    return False


def _submit_and_wait(page, sel_visible: str, wait_s: float = 3.0) -> None:
    if not _click_text_button(page, ("続ける", "ログイン", "Next", "Log in"), 1500):
        page.keyboard.press("Enter")
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            if not page.locator(sel_visible).first.is_visible():
                return
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.3)


def run_login_browser(cfg, headless: Optional[bool] = None) -> LoginResult:
    email, username, password, _totp = credentials_from(cfg)
    if not (email or username) or not password:
        raise LoginError(
            "missing credentials: config auth{} or env XB_LOGIN_EMAIL/USERNAME/PASSWORD"
        )
    if headless is None:
        headless = _headless()

    from cloakbrowser import launch

    browser = launch(headless=headless)
    try:
        context = browser.new_context(locale="ja-JP")
        page = context.new_page()
        page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=45000)

        # step 1: identifier
        inp = _visible(page, _SEL_EMAIL, 30000)
        if inp is None:
            raise LoginError("login page did not render username_or_email input")
        inp.fill(email or username)
        _submit_and_wait(page, _SEL_EMAIL)

        # step 2: knowledge check (optional)
        kc = _visible(page, _SEL_KC, 8000)
        if kc is not None:
            if not username:
                raise LoginError("knowledge check requires the account username")
            kc.fill(username)
            _submit_and_wait(page, _SEL_KC)

        # step 3: email verify code screen may appear -> switch to password
        deadline = time.time() + 10
        while time.time() < deadline:
            if _click_text_button(page, ("パスワードを使用", "Use password"), 800):
                break
            if _visible(page, _SEL_PWD, 300) is not None:
                break
            time.sleep(0.4)

        # step 4: password
        pwd = _visible(page, _SEL_PWD, 15000)
        if pwd is None:
            screen = ""
            try:
                screen = page.locator("div[role='dialog']").first.inner_text(timeout=1500)
            except Exception:  # noqa: BLE001
                pass
            raise LoginError(f"password input not reached; screen={screen[:120]!r}")
        pwd.press_sequentially(password, delay=40)
        try:
            pwd.press("Enter")
        except Exception:  # noqa: BLE001
            _submit_and_wait(page, _SEL_PWD)

        # step 5: wait for login completion (cookies are the source of truth)
        deadline = time.time() + 30
        picked: dict[str, str] = {}
        user_id: Optional[str] = None
        while time.time() < deadline:
            time.sleep(1)
            cookies_list = context.cookies("https://x.com")
            picked = {}
            for c in cookies_list:
                if c["name"] in ("auth_token", "ct0"):
                    picked[c["name"]] = c["value"]
                if c["name"] == "twid" and c.get("value", "").startswith("u%3D"):
                    user_id = urllib.parse.unquote(c["value"])[2:]
            if picked.get("auth_token") and picked.get("ct0"):
                break
        if not picked.get("auth_token"):
            screen = ""
            try:
                screen = page.locator("div[role='dialog']").last.inner_text(timeout=1500)
            except Exception:  # noqa: BLE001
                pass
            raise LoginError(
                f"login did not complete (final url={page.url[:80]!r} screen={screen[:150]!r})")
        return LoginResult(cookies=picked, user_id=user_id, screen_name=username or None)
    finally:
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass