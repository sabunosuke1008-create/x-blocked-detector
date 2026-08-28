"""Password-based login via twikit; converts the session into config cookies.

Primary path: twikit's request-based login flow (guest token -> ui_metrics ->
identifier -> password -> optional TOTP -> duplication check). twikit generates
its own x-client-transaction-id via x_client_transaction; when X's web HTML no
longer exposes the ondemand bundle (observed 2026), we transparently replace
twikit's TID generator with our own Playwright-based one.

Interactive: X may ask for an email verification code (LoginAcid) or a TOTP
code in the terminal; twikit prompts via input() and the CLI passes it through.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Optional


class LoginError(Exception):
    """Login failed; str(exc) explains why."""


class LoginDenied(LoginError):
    """X refused the attempt (DenyLogin / risk control)."""


class LoginNeedsUnlock(LoginError):
    """Account locked (326). Unlock manually at https://x.com/account/access."""


class _TidUnavailable(Exception):
    """Internal: twikit could not bootstrap its transaction-id generator."""


@dataclass
class LoginResult:
    cookies: dict[str, str]
    user_id: Optional[str] = None
    screen_name: Optional[str] = None


def _pick_cookies(all_cookies: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in ("auth_token", "ct0"):
        value = all_cookies.get(key)
        if value:
            out[key] = str(value)
    return out


def credentials_from(cfg) -> tuple[str, str, str, str]:
    """Resolve (email, username, password, totp) - env vars win over config."""
    auth = getattr(cfg, "auth", None) or {}
    email = os.environ.get("XB_LOGIN_EMAIL") or auth.get("email", "")
    username = os.environ.get("XB_LOGIN_USERNAME") or auth.get("username", "")
    password = os.environ.get("XB_LOGIN_PASSWORD") or auth.get("password", "")
    totp = os.environ.get("XB_LOGIN_TOTP") or auth.get("totp_secret", "")
    return email, username, password, totp


def _make_client():
    try:
        from twikit import Client
    except ImportError as exc:
        raise LoginError("twikit is not installed (pip install twikit)") from exc
    return Client("en-US")


def _patch_tid_generator(client) -> bool:
    """Swap twikit's TID generation for our Playwright-based generator."""
    try:
        from . import tid_gen

        if tid_gen._resolve_playwright_argv() is None:
            return False
        client.client_transaction.home_page_response = "seeded"

        def _gen(method: str, path: str):
            return tid_gen.generate_tid(path or "/graphql", method or "GET")

        client.client_transaction.generate_transaction_id = _gen  # type: ignore[method-assign]
        return True
    except Exception:
        return False


def _classify(exc: Exception) -> Exception:
    name = type(exc).__name__
    msg = str(exc)
    if name == "AccountLocked":
        return LoginNeedsUnlock(
            "account locked; unlock at https://x.com/account/access (captcha required)"
        )
    if name in ("BadRequest", "Unauthorized"):
        return LoginError(f"credentials rejected ({name}): {msg[:200]}")
    lowered = msg.lower()
    tid_markers = ("ondemand", "key_byte", "nonetype", "transaction", "client_transaction")
    if any(marker in lowered for marker in tid_markers):
        return _TidUnavailable(msg)
    if name == "TwitterException":
        return LoginDenied(msg[:300])
    return LoginError(f"{name}: {msg[:300]}")


async def _attempt(
    email: str, username: str, password: str, totp: str,
    enable_ui_metrics: bool, patch_tid: bool,
) -> LoginResult:
    client = _make_client()
    if patch_tid and not _patch_tid_generator(client):
        raise LoginError("twikit TID bootstrap failed and Playwright fallback is unavailable")
    try:
        await client.login(
            auth_info_1=email or username,
            auth_info_2=(username if email else None) or (email if username else None),
            password=password,
            totp_secret=totp or None,
            enable_ui_metrics=enable_ui_metrics,
        )
    except Exception as exc:  # noqa: BLE001
        raise _classify(exc) from exc

    user_id: Optional[str] = None
    screen_name: Optional[str] = None
    try:
        user = await client.user()
        user_id = str(user.id)
        screen_name = user.screen_name
    except Exception:  # noqa: BLE001
        pass
    cookies = _pick_cookies(client.get_cookies())
    if not cookies.get("auth_token"):
        raise LoginError("login flow finished but no auth_token cookie was set")
    return LoginResult(cookies=cookies, user_id=user_id, screen_name=screen_name)


async def _login_async(email: str, username: str, password: str, totp: str) -> LoginResult:
    try:
        import js2py_  # noqa: F401
        ui_metrics = True
    except ImportError:
        ui_metrics = False
    try:
        return await _attempt(email, username, password, totp, ui_metrics, patch_tid=False)
    except _TidUnavailable:
        return await _attempt(email, username, password, totp, ui_metrics, patch_tid=True)


def run_login(cfg) -> LoginResult:
    email, username, password, totp = credentials_from(cfg)
    if not (email or username) or not password:
        raise LoginError(
            "missing credentials: set config auth.{email,username,password} "
            "or env XB_LOGIN_EMAIL / XB_LOGIN_USERNAME / XB_LOGIN_PASSWORD"
        )
    return asyncio.run(_login_async(email, username, password, totp))


def node_engine_available() -> bool:
    import shutil
    from pathlib import Path
    if not shutil.which("node"):
        return False
    script = Path(__file__).resolve().parent.parent / "node_login" / "login_dump.mjs"
    return script.exists()


def run_login_node(cfg, out_path: Optional[str] = None) -> LoginResult:
    """Login via the Node engine (the-convocation/twitter-scraper, 2026 flow)."""
    import json as _json
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    email, username, password, totp = credentials_from(cfg)
    if not (email or username) or not password:
        raise LoginError("missing credentials (config auth{} or XB_LOGIN_* env)")
    if not node_engine_available():
        raise LoginError("node engine not available (need node in PATH + node_login/)")

    script = Path(__file__).resolve().parent.parent / "node_login" / "login_dump.mjs"
    out = out_path or str(Path(tempfile.gettempdir()) / "xb_login_cookies.json")
    env = os.environ | {
        "XB_LOGIN_EMAIL": email, "XB_LOGIN_USERNAME": username,
        "XB_LOGIN_PASSWORD": password, "XB_LOGIN_TOTP": totp, "OUT": out,
    }
    proc = subprocess.run([shutil.which("node"), str(script)], env=env,
                          capture_output=True, text=True, timeout=300,
                          encoding="utf-8", errors="replace", cwd=str(script.parent))
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-300:]
        if "status: 403" in tail or "cloudflare" in tail.lower():
            raise LoginError("Cloudflare blocked the login endpoint (403) from this IP/network")
        raise LoginError(f"node engine failed: {tail}")
    data = _json.loads(Path(out).read_text(encoding="utf-8"))
    cookies = {k: str(v) for k, v in (data.get("cookies") or {}).items() if v}
    if not cookies.get("auth_token"):
        raise LoginError("node engine returned no auth_token")
    return LoginResult(cookies=cookies,
                       user_id=data.get("user_id"), screen_name=data.get("screen_name"))
