"""CloakBrowser-based X login (proven path, 2026 JFAPI flow).

Flow (validated live): email -> knowledge_check(username) -> [verify_code ->
"Use password" switch] -> password -> home. Cookies (auth_token/ct0) are
extracted from the browser context (httpOnly included).
"""
from __future__ import annotations

import os
import time
import urllib.parse
from typing import Optional

from .auth_login import LoginError, LoginResult, credentials_from

_IDENT_SELECTORS = ("input[name='text']", "input[type='text']", "input:not([type])")


def _headless() -> bool:
    return os.environ.get("XB_LOGIN_HEADLESS", "") == "1"


def _first_visible(page, selectors: list[str], timeout_ms: int = 4000):
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for sel in selectors:
            try:
                for loc in page.locator(sel).all():
                    try:
                        if loc.is_visible() and loc.is_enabled() and loc.is_editable():
                            return loc
                    except Exception:  # noqa: BLE001
                        continue
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.3)
    return None


def _click_text_button(page, texts: tuple[str, ...], timeout_ms: int = 3000) -> bool:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for t in texts:
            try:
                btn = page.get_by_role("button", name=t, exact=True).first
                if btn.is_visible():
                    btn.click()
                    return True
            except Exception:  # noqa: BLE001
                pass
            try:
                for loc in page.get_by_text(t, exact=True).all():
                    try:
                        parent = loc.locator("xpath=ancestor::button[1]")
                        if parent.count() > 0:
                            parent.first.click()
                            return True
                        if loc.is_visible():
                            loc.click()
                            return True
                    except Exception:  # noqa: BLE001
                        continue
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.3)
    return False


def _screen_text(page) -> str:
    try:
        return page.locator("div[role='dialog']").first.inner_text(timeout=1500)
    except Exception:  # noqa: BLE001
        try:
            return page.locator("body").inner_text(timeout=1500)
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

    from cloakbrowser import launch

    browser = launch(headless=headless)
    try:
        context = browser.new_context(locale="ja-JP")
        page = context.new_page()
        page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=45000)

        ident = email or username
        inp = _first_visible(page, list(_IDENT_SELECTORS), 30000)
        if inp is None:
            raise LoginError("login page did not render an identifier input")
        inp.fill(ident)
        if not _click_text_button(page, ("続ける", "Next"), 2000):
            page.keyboard.press("Enter")

        logged_in = False
        for i in range(40):  # ~60s budget for the interactive steps
            time.sleep(1.5)
            url = page.url
            if url.rstrip("/").endswith("x.com/home") or "/i/jf/onboarding" not in url and "/i/flow" not in url:
                logged_in = True
                break
            screen = _screen_text(page)
            if i % 3 == 0:
                print(f"[login-browser] step={i} url={url[:70]} screen={screen[:80]!r}", flush=True)
            if "正しくありません" in screen:
                raise LoginError("knowledge check rejected the username")
            if "許可されていません" in screen:
                raise LoginError("X risk-blocked this login (account or environment)")
            pwd = _first_visible(page, ["input[type='password']"], 500)
            if pwd is not None:
                pwd.fill(password)
                page.keyboard.press("Enter")
                time.sleep(2)
                continue
            if _click_text_button(page, ("パスワードを使用", "Use password"), 800):
                print("[login-browser] clicked use-password switch", flush=True)
                continue
            kinp = None
            try:
                lbl = page.get_by_role("textbox", name="ユーザー名").first
                if lbl.is_visible():
                    kinp = lbl
            except Exception:  # noqa: BLE001
                kinp = None
            if kinp is None:
                kinp = _first_visible(page, list(_IDENT_SELECTORS), 500)
            if kinp is not None and username:
                cur = kinp.input_value()
                if cur != username:
                    kinp.fill(username)
                if not _click_text_button(page, ("続ける", "Next"), 1500):
                    page.keyboard.press("Enter")
                continue

        if not logged_in:
            raise LoginError("login did not complete within the time budget")

        time.sleep(2)
        cookies_list = context.cookies("https://x.com")
        picked: dict[str, str] = {}
        user_id: Optional[str] = None
        for c in cookies_list:
            if c["name"] in ("auth_token", "ct0"):
                picked[c["name"]] = c["value"]
            if c["name"] == "twid" and c.get("value", "").startswith("u%3D"):
                user_id = urllib.parse.unquote(c["value"])[2:]
        if not picked.get("auth_token"):
            raise LoginError("login finished but auth_token cookie was not found")
        return LoginResult(cookies=picked, user_id=user_id,
                           screen_name=username or None)
    finally:
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass