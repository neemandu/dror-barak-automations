"""The dashboard on Lambda — the same pages, no server and no memory.

:mod:`src.dashboard` serves the page with a stdlib ``HTTPServer`` and keeps
sessions in a dict. Fine on a laptop; useless on Lambda, where every instance
has its own dict and Dror would be logged out at random. This entrypoint renders
the very same pages and keeps the session **in the cookie**: an expiry time
signed with ``SIGN_LINK_SECRET`` (domain-separated, so a dashboard cookie can
never pass as a signing link and vice versa). Any instance can verify it, and
nothing has to be stored.

Login throttling stays per instance: five wrong passwords lock that instance for
15 minutes, and API Gateway's rate limit does the rest.

Routes, under the stage prefix (``/dev``): ``GET /dashboard``, ``GET|POST /login``,
``GET /logout``, ``GET /healthz``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any, Optional
from urllib.parse import parse_qs

from . import dashboard, questionnaire_admin
from .lib import config
from .lib.logging_setup import get_logger

log = get_logger("dashboard", "lambda")

COOKIE = dashboard.SESSION_COOKIE
TTL = dashboard.SESSION_TTL_SECONDS


# ------------------------------------------------------------------ session


def _key() -> bytes:
    # One secret for the stack, but a distinct MAC domain: a valid dashboard
    # cookie must never verify as anything else the secret signs.
    return hashlib.sha256(b"dashboard:" + config.require("SIGN_LINK_SECRET").encode("utf-8")).digest()


def issue_session(now: Optional[float] = None) -> str:
    expiry = int(now if now is not None else time.time()) + TTL
    mac = hmac.new(_key(), f"{expiry}".encode("ascii"), hashlib.sha256).digest()
    return f"{expiry}.{base64.urlsafe_b64encode(mac).decode('ascii').rstrip('=')}"


def valid_session(token: Optional[str], now: Optional[float] = None) -> bool:
    if not token or "." not in token:
        return False
    expiry, _, mac = token.partition(".")
    if not expiry.isdigit():
        return False
    expected = base64.urlsafe_b64encode(
        hmac.new(_key(), expiry.encode("ascii"), hashlib.sha256).digest()).decode("ascii").rstrip("=")
    if not hmac.compare_digest(mac, expected):
        return False
    return int(expiry) > (now if now is not None else time.time())


# ------------------------------------------------------------------ helpers


def _cookie_value(event: dict[str, Any]) -> Optional[str]:
    raw = list(event.get("cookies") or [])
    header = {k.lower(): v for k, v in (event.get("headers") or {}).items()}.get("cookie")
    if header:
        raw += header.split(";")
    for part in raw:
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value
    return None


def _set_cookie(token: str = "", clear: bool = False) -> str:
    if clear:
        return f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax; Secure"
    return f"{COOKIE}={token}; Path=/; Max-Age={TTL}; HttpOnly; SameSite=Lax; Secure"


def _html(status: int, body: bytes, cookie: Optional[str] = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "statusCode": status,
        "headers": {
            "Content-Type": "text/html; charset=utf-8",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        },
        "body": body.decode("utf-8"),
    }
    if cookie:
        out["cookies"] = [cookie]
    return out


def _redirect(to: str, cookie: Optional[str] = None) -> dict[str, Any]:
    out: dict[str, Any] = {"statusCode": 303, "headers": {"Location": to, "Cache-Control": "no-store"}, "body": ""}
    if cookie:
        out["cookies"] = [cookie]
    return out


# ------------------------------------------------------------------- handler


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    config.load_dotenv()
    rc = event.get("requestContext") or {}
    http = rc.get("http") or {}
    method = str(http.get("method") or event.get("httpMethod") or "GET").upper()
    path = str(http.get("path") or event.get("rawPath") or "/")
    stage = str(rc.get("stage") or "")
    base = f"/{stage}" if stage and stage != "$default" else ""
    route = path[len(base):] if base and path.startswith(base) else path
    route = route.rstrip("/") or "/"
    q = {k: v for k, v in (event.get("queryStringParameters") or {}).items() if v}
    ip = str(http.get("sourceIp") or "")

    if route in ("/", "/dashboard") and method == "GET":
        if not valid_session(_cookie_value(event)):
            return _redirect(f"{base}/login")
        return _html(200, dashboard._dashboard_page(dashboard._load(q), q, base))

    if route == "/login" and method == "GET":
        return _html(200, dashboard._login_page(base=base))

    if route == "/login" and method == "POST":
        if dashboard._locked_out(ip):
            return _html(429, dashboard._login_page("יותר מדי נסיונות. נסה שוב בעוד רבע שעה.", base))
        body = str(event.get("body") or "")
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        supplied = (parse_qs(body).get("password") or [""])[0]
        try:
            ok = dashboard._check_password(supplied)
        except config.ConfigError as exc:
            log.error("dashboard_misconfigured", extra={"error": str(exc)})
            return _html(500, dashboard._login_page(str(exc), base))
        if not ok:
            dashboard._record_failure(ip)
            log.warning("dashboard_login_failed", extra={"ip": ip})
            return _html(401, dashboard._login_page("סיסמה שגויה.", base))
        dashboard._attempts.pop(ip, None)
        return _redirect(f"{base}/dashboard", _set_cookie(issue_session()))

    if route == "/logout":
        return _redirect(f"{base}/login", _set_cookie(clear=True))

    if route == "/healthz":
        return {"statusCode": 200, "headers": {"Content-Type": "text/plain"}, "body": "ok"}

    if route == "/admin" or route.startswith("/admin/"):
        if not valid_session(_cookie_value(event)):
            if route.startswith("/admin/api/"):
                return {"statusCode": 401, "headers": {"Content-Type": "application/json"},
                        "body": '{"errors":["login"]}'}
            return _redirect(f"{base}/login")
        raw = event.get("body") or ""
        body = base64.b64decode(raw) if event.get("isBase64Encoded") else str(raw).encode("utf-8")
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        resp = questionnaire_admin.handle(method, route, q, body, headers, base=base,
                                          dry_run=config.get_bool("WEBHOOK_DRY_RUN"))
        if resp.status in (301, 302, 303):
            return _redirect(resp.location)
        out = {"statusCode": resp.status,
               "headers": {"Content-Type": resp.content_type, "Cache-Control": "no-store",
                           "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                           "Referrer-Policy": "no-referrer"},
               "body": resp.body}
        if resp.disposition:
            out["headers"]["Content-Disposition"] = resp.disposition
        return out

    return _html(404, dashboard._not_found(base))
