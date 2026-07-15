"""Dashboard — one page where Dror sees everything the automations did.

Read-only by design: it shows the run-log, grouped into subjects (invoices, leads,
campaign reports, ...), with links out to the real artefacts in Drive / ClickUp /
Morning. Nothing can be triggered from here, so the page cannot cause an action —
the worst a visitor can do is read.

That "worst case" is still client phone numbers, monthly prices and contract links,
so the page requires a password and refuses to start without one.

Run:
    python -m src.dashboard                 # needs DASHBOARD_PASSWORD in .env
    python -m src.dashboard --dry-run       # sample data, no .env needed

Configure ``DASHBOARD_PASSWORD``, ``DASHBOARD_PORT`` (default 8080) and, when
served over HTTPS, leave ``DASHBOARD_INSECURE_COOKIE`` unset so the session cookie
is marked Secure.
"""

from __future__ import annotations

import argparse
import hmac
import html
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from .lib import config, run_log, subjects

SESSION_COOKIE = "dror_dash"
SESSION_TTL_SECONDS = 12 * 60 * 60

# Login throttling: a read-only page still shouldn't be brute-forceable.
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60

_sessions: dict[str, float] = {}  # token -> expiry (epoch seconds)
_attempts: dict[str, list[float]] = {}  # client ip -> recent failure times

DRY_RUN = False


# --------------------------------------------------------------------------- auth


def _password() -> str:
    """The dashboard password, or raise if unset.

    Deliberately fails closed: an unauthenticated dashboard would publish every
    client's phone number and price to anyone who found the URL.
    """
    value = config.get("DASHBOARD_PASSWORD")
    if not value:
        raise config.ConfigError(
            "DASHBOARD_PASSWORD is not set. The dashboard shows client data and "
            "will not serve without a password. Set it in .env (see .env.example)."
        )
    return value


def _check_password(supplied: str) -> bool:
    # compare_digest to avoid leaking the password's length/prefix via timing.
    return hmac.compare_digest(supplied.encode("utf-8"), _password().encode("utf-8"))


def _new_session() -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time() + SESSION_TTL_SECONDS
    _prune_sessions()
    return token


def _prune_sessions() -> None:
    now = time.time()
    for token, expiry in list(_sessions.items()):
        if expiry < now:
            del _sessions[token]


def _valid_session(token: Optional[str]) -> bool:
    if not token:
        return False
    expiry = _sessions.get(token)
    if expiry is None:
        return False
    if expiry < time.time():
        del _sessions[token]
        return False
    return True


def _locked_out(ip: str) -> bool:
    recent = [t for t in _attempts.get(ip, []) if t > time.time() - LOCKOUT_SECONDS]
    _attempts[ip] = recent
    return len(recent) >= MAX_ATTEMPTS


def _record_failure(ip: str) -> None:
    _attempts.setdefault(ip, []).append(time.time())


# ------------------------------------------------------------------------- render


def _esc(value: Any) -> str:
    """Escape for HTML. Every run-log field is data we did not write."""
    return html.escape("" if value is None else str(value), quote=True)


_STATUS_STYLE = {
    "ok": ("✓", "ok"),
    "error": ("✕", "err"),
    "skipped": ("–", "skip"),
}

CSS = """
:root { color-scheme: light dark; --bg:#f6f7f9; --card:#fff; --line:#e3e6ea;
  --text:#1a1d21; --muted:#6b7280; --ok:#0a7c42; --err:#c0271c; --skip:#8a6d16; }
@media (prefers-color-scheme: dark) { :root { --bg:#14171a; --card:#1c2024;
  --line:#2c3238; --text:#e7eaee; --muted:#9aa3ad; --ok:#3ec27f; --err:#ff6b5e;
  --skip:#d6a92b; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text); font-family:system-ui,
  "Segoe UI", Arial, sans-serif; font-size:15px; line-height:1.5; }
.wrap { max-width:1000px; margin:0 auto; padding:24px 16px 64px; }
h1 { font-size:22px; margin:0 0 4px; }
.sub { color:var(--muted); font-size:13px; margin-bottom:20px; }
.cards { display:flex; flex-wrap:wrap; gap:10px; margin-bottom:20px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:10px 16px; min-width:96px; }
.card b { display:block; font-size:22px; }
.card span { color:var(--muted); font-size:12px; }
form.filters { background:var(--card); border:1px solid var(--line);
  border-radius:10px; padding:12px; margin-bottom:20px; display:flex;
  flex-wrap:wrap; gap:8px; align-items:center; }
select, input, button { font:inherit; padding:6px 10px; border-radius:8px;
  border:1px solid var(--line); background:var(--bg); color:var(--text); }
button { cursor:pointer; }
section { background:var(--card); border:1px solid var(--line); border-radius:10px;
  margin-bottom:14px; overflow:hidden; }
section > h2 { font-size:15px; margin:0; padding:10px 14px;
  border-bottom:1px solid var(--line); }
section > h2 span { color:var(--muted); font-weight:400; font-size:13px; }
table { width:100%; border-collapse:collapse; }
td { padding:8px 14px; border-bottom:1px solid var(--line); vertical-align:top; }
tr:last-child td { border-bottom:0; }
.t { color:var(--muted); white-space:nowrap; width:1%; font-variant-numeric:tabular-nums; }
.s { width:1%; font-weight:700; }
.s.ok { color:var(--ok); } .s.err { color:var(--err); } .s.skip { color:var(--skip); }
.who { color:var(--muted); font-size:13px; }
.tag { display:inline-block; font-size:11px; border:1px solid var(--line);
  border-radius:6px; padding:0 5px; color:var(--muted); margin-inline-start:6px; }
a { color:inherit; }
.alert { border-color:var(--err); }
.alert > h2 { color:var(--err); }
.empty { padding:28px 14px; text-align:center; color:var(--muted); }
.login { max-width:320px; margin:14vh auto; background:var(--card);
  border:1px solid var(--line); border-radius:12px; padding:24px; }
.login input { width:100%; margin:10px 0; }
.login button { width:100%; background:var(--text); color:var(--bg); border:0; }
.bad { color:var(--err); font-size:13px; }
"""


def _page(title: str, body: str) -> bytes:
    return f"""<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{_esc(title)}</title><style>{CSS}</style></head>
<body>{body}</body></html>""".encode("utf-8")


def _login_page(error: str = "") -> bytes:
    note = f'<p class="bad">{_esc(error)}</p>' if error else ""
    return _page(
        "כניסה — לוח בקרה",
        f"""<form class="login" method="post" action="/login">
        <h1>לוח בקרה</h1>
        <div class="sub">האוטומציות של דרור ברק</div>
        {note}
        <input type="password" name="password" placeholder="סיסמה" autofocus
               autocomplete="current-password">
        <button type="submit">כניסה</button></form>""",
    )


def _row(entry: dict[str, Any]) -> str:
    mark, cls = _STATUS_STYLE.get(str(entry.get("status")), ("•", ""))
    when = subjects.parse_ts(entry) or ""
    bits = [f'<td class="t">{_esc(when)}</td>', f'<td class="s {cls}">{mark}</td>']

    main = f"<b>{_esc(entry.get('action'))}</b>"
    if entry.get("dry_run"):
        main += '<span class="tag">הרצת ניסיון</span>'
    detail = entry.get("detail")
    # A detail that is just a URL is rendered as the link below, not as text.
    if detail and not str(detail).startswith("http"):
        main += f'<div class="who">{_esc(detail)}</div>'
    for label, url in subjects.links_for(entry):
        main += f'<div><a href="{_esc(url)}" target="_blank" rel="noopener noreferrer">{_esc(label)} ↗</a></div>'
    bits.append(f"<td>{main}</td>")

    who = entry.get("client_id") or ""
    bits.append(f'<td class="who">{_esc(who)}</td>')
    return "<tr>" + "".join(bits) + "</tr>"


def _dashboard_page(entries: list[dict[str, Any]], q: dict[str, str]) -> bytes:
    counts = subjects.counts(entries)
    cards = "".join(
        f'<div class="card"><b>{counts[key]}</b><span>{label}</span></div>'
        for key, label in (
            ("total", "פעולות"),
            ("ok", "הצליחו"),
            ("error", "שגיאות"),
            ("skipped", "דילוגים"),
        )
    )

    subject_opts = '<option value="">כל הנושאים</option>' + "".join(
        f'<option value="{s.key}"{" selected" if q.get("subject") == s.key else ""}>'
        f"{s.icon} {_esc(s.label)}</option>"
        for s in subjects.SUBJECTS.values()
    )
    # Every known client, not just those in the current filter — otherwise picking
    # one client would remove every other option and strand you there.
    all_entries = _SAMPLE if DRY_RUN else run_log.read_all()
    client_opts = '<option value="">כל הלקוחות</option>' + "".join(
        f'<option value="{_esc(c)}"{" selected" if q.get("client") == c else ""}>{_esc(c)}</option>'
        for c in subjects.client_ids(all_entries)
    )
    days_opts = "".join(
        f'<option value="{d}"{" selected" if q.get("days", "7") == str(d) else ""}>'
        f"{label}</option>"
        for d, label in ((1, "היום"), (7, "7 ימים"), (30, "30 יום"), (365, "הכל"))
    )

    filters = f"""<form class="filters" method="get" action="/dashboard">
      <select name="subject">{subject_opts}</select>
      <select name="client">{client_opts}</select>
      <select name="days">{days_opts}</select>
      <input type="search" name="q" placeholder="חיפוש חופשי" value="{_esc(q.get('q',''))}">
      <button type="submit">סנן</button>
      <a href="/logout" style="margin-inline-start:auto"><button type="button">יציאה</button></a>
    </form>"""

    body_sections = ""
    # Failures first — being in the dark about breakages is the problem this solves.
    failed = subjects.failures(entries)
    if failed:
        rows = "".join(_row(e) for e in failed[:20])
        body_sections += (
            f'<section class="alert"><h2>⚠️ דורש טיפול <span>({len(failed)})</span></h2>'
            f"<table>{rows}</table></section>"
        )

    for subject, group in subjects.group_by_subject(entries):
        rows = "".join(
            _row(e) for e in sorted(group, key=lambda e: str(e.get("ts")), reverse=True)
        )
        body_sections += (
            f"<section><h2>{subject.icon} {_esc(subject.label)} "
            f"<span>({len(group)})</span></h2><table>{rows}</table></section>"
        )

    if not entries:
        body_sections = '<section><div class="empty">אין פעילות בטווח הזה.</div></section>'

    return _page(
        "לוח בקרה — דרור ברק",
        f"""<div class="wrap"><h1>לוח בקרה</h1>
        <div class="sub">כל מה שהאוטומציות עשו. הדף לצפייה בלבד — לא מפעיל כלום.</div>
        <div class="cards">{cards}</div>{filters}{body_sections}</div>""",
    )


# ------------------------------------------------------------------------ filters


def _filter(entries: list[dict[str, Any]], q: dict[str, str]) -> list[dict[str, Any]]:
    if q.get("subject"):
        entries = [e for e in entries if subjects.subject_for(e).key == q["subject"]]
    if q.get("client"):
        entries = [e for e in entries if str(e.get("client_id") or "") == q["client"]]
    if q.get("q"):
        needle = q["q"].lower()
        entries = [
            e
            for e in entries
            if any(needle in str(v).lower() for v in e.values() if v is not None)
        ]
    return entries


def _load(q: dict[str, str]) -> list[dict[str, Any]]:
    # Dry-run skips the date window (the sample data has fixed dates that would
    # fall outside it) but must still honour the filters — otherwise the demo
    # quietly shows everything regardless of what you picked.
    if DRY_RUN:
        return _filter(_SAMPLE, q)
    try:
        days = int(q.get("days", "7"))
    except ValueError:
        days = 7
    since = datetime.now(timezone.utc) - timedelta(days=max(days, 1))
    return _filter(run_log.read_since(since), q)


# Sample data so `--dry-run` shows a realistic page with no .env and no real log.
_SAMPLE: list[dict[str, Any]] = [
    {"ts": "2026-07-15T09:12:00Z", "automation": "lead_to_contacts", "action": "contact_saved",
     "status": "ok", "client_id": "מכללת אלפא", "detail": "+972501111111"},
    {"ts": "2026-07-15T09:30:00Z", "automation": "send_questionnaire",
     "action": "questionnaire_sent", "status": "ok", "client_id": "מכללת אלפא"},
    {"ts": "2026-07-15T10:02:00Z", "automation": "onboarding", "action": "drive_folder_created",
     "status": "ok", "client_id": "מכללת בטא",
     "detail": "https://drive.google.com/drive/folders/abc123"},
    {"ts": "2026-07-15T10:03:00Z", "automation": "onboarding", "action": "morning_client_created",
     "status": "ok", "client_id": "מכללת בטא", "detail": "לקוח 4471"},
    {"ts": "2026-07-15T11:00:00Z", "automation": "monthly_payment_requests",
     "action": "payment_requested", "status": "ok", "client_id": "מכללת אלפא",
     "detail": "3500₪ יולי", "url": "https://app.greeninvoice.co.il/documents/99"},
    {"ts": "2026-07-15T11:01:00Z", "automation": "monthly_payment_requests",
     "action": "no_price", "status": "skipped", "client_id": "מכללת גמא",
     "detail": "no monthly_price"},
    {"ts": "2026-07-15T12:20:00Z", "automation": "campaign_summary", "action": "campaign_report_built",
     "status": "error", "client_id": "מכללת אלפא", "detail": "Meta API: token expired"},
    {"ts": "2026-07-15T13:40:00Z", "automation": "social_prep", "action": "prep_report_ready",
     "status": "ok", "client_id": "מכללת בטא", "dry_run": True,
     "detail": "https://docs.google.com/document/d/xyz789"},
]


# ------------------------------------------------------------------------- server


class Handler(BaseHTTPRequestHandler):
    server_version = "DrorDash"

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        route = urlparse(self.path)
        if route.path in ("/", "/dashboard"):
            if not self._authed():
                return self._redirect("/login")
            q = {k: v[0] for k, v in parse_qs(route.query).items() if v and v[0]}
            return self._send(200, _dashboard_page(_load(q), q))
        if route.path == "/login":
            return self._send(200, _login_page())
        if route.path == "/logout":
            _sessions.pop(self._cookie() or "", None)
            return self._redirect("/login", clear=True)
        if route.path == "/healthz":
            return self._send(200, b"ok", "text/plain")
        self._send(404, _page("404", '<div class="wrap">לא נמצא</div>'))

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/login":
            return self._send(404, _page("404", '<div class="wrap">לא נמצא</div>'))

        ip = self.client_address[0]
        if _locked_out(ip):
            return self._send(429, _login_page("יותר מדי נסיונות. נסה שוב בעוד רבע שעה."))

        length = int(self.headers.get("Content-Length", 0) or 0)
        form = parse_qs(self.rfile.read(length).decode("utf-8")) if length else {}
        supplied = (form.get("password") or [""])[0]

        try:
            ok = _check_password(supplied)
        except config.ConfigError as exc:
            return self._send(500, _login_page(str(exc)))

        if not ok:
            _record_failure(ip)
            return self._send(401, _login_page("סיסמה שגויה."))

        _attempts.pop(ip, None)
        self._redirect("/dashboard", token=_new_session())

    # -- helpers

    def _cookie(self) -> Optional[str]:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE:
                return value
        return None

    def _authed(self) -> bool:
        return _valid_session(self._cookie())

    def _cookie_header(self, token: str = "", clear: bool = False) -> str:
        # Secure unless explicitly told we're on plain http (local development).
        secure = "" if config.get_bool("DASHBOARD_INSECURE_COOKIE") else " Secure;"
        if clear:
            return f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax;{secure}"
        return (
            f"{SESSION_COOKIE}={token}; Path=/; Max-Age={SESSION_TTL_SECONDS}; "
            f"HttpOnly; SameSite=Lax;{secure}"
        )

    def _redirect(self, to: str, token: str = "", clear: bool = False) -> None:
        self.send_response(303)
        self.send_header("Location", to)
        if token or clear:
            self.send_header("Set-Cookie", self._cookie_header(token, clear))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, status: int, data: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:  # silence stderr access logging
        pass


def serve(port: int, dry_run: bool = False) -> None:
    global DRY_RUN
    DRY_RUN = dry_run
    config.load_dotenv()
    if not dry_run:
        _password()  # fail fast and loudly rather than serve client data openly
    print(f'{{"msg": "dashboard listening", "port": {port}, "dry_run": {str(dry_run).lower()}}}')
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only automations dashboard")
    parser.add_argument("--dry-run", action="store_true",
                        help="Serve sample data; no .env or password required.")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    config.load_dotenv()
    if args.dry_run:
        os.environ.setdefault("DASHBOARD_PASSWORD", "dryrun")
        os.environ.setdefault("DASHBOARD_INSECURE_COOKIE", "1")
    serve(args.port or int(config.get("DASHBOARD_PORT", "8080")), args.dry_run)


if __name__ == "__main__":
    main()
