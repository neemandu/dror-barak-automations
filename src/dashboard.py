"""Dashboard — one page where Dror sees everything the automations did.

Read-only by design: it shows the run-log, grouped into subjects (leads, contracts,
campaign reports, ...), with links out to the real artefacts in Drive / ClickUp. Nothing can be triggered from here, so the page cannot cause an action —
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
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, quote, urlparse

from . import clients_pages, questionnaire_admin, ui
from .lib import config, run_log, subjects, text_style

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


_STATUS = {
    "ok": ("check", "ok", "הצליח"),
    "error": ("x", "err", "נכשל"),
    "skipped": ("minus", "skip", "דילוג"),
}

#: Lucide icons for the subjects (the emoji in ``subjects`` stay for the email).
_SUBJECT_ICONS = {"leads": "user-plus", "clickup": "clipboard", "quotes": "pen", "meta": "gauge",
                  "whatsapp": "message", "drive": "folder", "ai": "sparkles", "system": "activity",
                  "other": "info"}

CSS = """
.log-card { overflow: hidden; margin-bottom: 14px; }
.log-card > summary { list-style: none; cursor: pointer; display: flex; align-items: center; gap: 10px;
  padding: 12px 18px; user-select: none; transition: background var(--d1); }
.log-card > summary::-webkit-details-marker { display: none; }
.log-card > summary:hover { background: var(--surface-hover); }
.log-card[open] > summary { border-bottom: 1px solid var(--border); }
.log-card .chev { margin-inline-start: auto; color: var(--fg-subtle); transition: transform var(--d2) var(--ease); }
.log-card:not([open]) .chev { transform: rotate(90deg); }
.subj-icon { width: 30px; height: 30px; border-radius: 9px; display: grid; place-items: center;
  background: var(--surface-active); color: var(--fg-2); }
.log-card.is-alert { border-color: var(--danger-border); }
.log-card.is-alert .subj-icon { background: var(--danger-soft); color: var(--danger-ink); }
.log-card.is-alert > summary .card-title { color: var(--danger-ink); }
.log-row { display: grid; grid-template-columns: auto 1fr auto; gap: 12px; align-items: start;
  padding: 12px 18px; border-bottom: 1px solid var(--border-soft); transition: background var(--d1); }
.log-row:last-child { border-bottom: 0; }
.log-row:hover { background: var(--surface-2); }
.log-dot { width: 24px; height: 24px; border-radius: 50%; display: grid; place-items: center; margin-top: 1px; }
.log-dot svg { width: 13px; height: 13px; stroke-width: 2.6; }
.log-dot.ok { background: var(--success-soft); color: var(--success-ink); }
.log-dot.err { background: var(--danger-soft); color: var(--danger-ink); }
.log-dot.skip { background: var(--warn-soft); color: var(--warn-ink); }
.log-title { font-weight: 600; color: var(--fg); display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.log-meta { margin-top: 3px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; color: var(--fg-muted); font-size: 13px; }
.log-detail { color: var(--fg-muted); font-size: 13px; margin-top: 3px; overflow-wrap: anywhere; }
.log-row.err .log-detail { color: var(--danger-ink); }
.log-links { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.log-time { color: var(--fg-subtle); font-size: 12.5px; white-space: nowrap; margin-top: 2px; }
.chip { display: inline-flex; align-items: center; gap: 5px; height: 22px; padding: 0 8px; border-radius: 6px;
  background: var(--surface-active); color: var(--fg-2); font-size: 12px; font-weight: 500; }
.filters { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 18px; }
.filters .with-icon { flex: 1 1 260px; }
.filters .select { width: auto; min-width: 150px; }
.filters .picker-inline { min-width: 200px; }
.login-wrap { min-height: 100vh; display: grid; place-items: center; padding: 24px 16px; position: relative; overflow: hidden; }
.login-wrap::before, .login-wrap::after { content: ""; position: absolute; width: 520px; height: 520px; border-radius: 50%;
  filter: blur(90px); opacity: .22; pointer-events: none; }
.login-wrap::before { background: #00c2e0; top: -180px; right: -140px; }
.login-wrap::after { background: #2f7de1; bottom: -200px; left: -160px; }
.login { position: relative; width: 100%; max-width: 380px; padding: 32px 28px 28px; }
.login .brand-mark { width: 44px; height: 44px; border-radius: 13px; margin-bottom: 18px; }
.login .brand-mark svg { width: 22px; height: 22px; }
.login h1 { margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -.01em; }
.login p { margin: 6px 0 22px; color: var(--fg-muted); font-size: 14px; }
.login .pw { position: relative; }
.login .pw .input { height: 44px; padding-inline-end: 44px; font-size: 15px; }
.login .pw button { position: absolute; inset-inline-end: 4px; top: 4px; }
.login .alert { margin-bottom: 14px; }
.login .btn-primary { margin-top: 14px; }
.login-foot { margin-top: 18px; text-align: center; font-size: 12.5px; color: var(--fg-subtle); display: flex;
  align-items: center; justify-content: center; gap: 6px; }
@media (max-width: 720px) { .log-row { grid-template-columns: auto 1fr; } .log-time { grid-column: 2; margin-top: 0; }
  .filters .select, .filters .picker-inline { flex: 1 1 40%; min-width: 0; max-width: none; } .filters .btn[type=submit] { display: none; } }
"""


def _page(title: str, body: str) -> bytes:
    """A bare page in the product's look (login, not found)."""
    return ui.document(title, body, kind="app", css=CSS).encode("utf-8")


def _login_page(error: str = "", base: str = "") -> bytes:
    """``base`` is the path prefix the page lives under — "" locally, "/dev" behind
    API Gateway — so every link and form action lands on the same deployment."""
    note = (f'<div class="alert alert-danger shake" role="alert">{ui.icon("alert")}<span>{_esc(error)}</span></div>'
            if error else "")
    body = f"""<div class="login-wrap"><form class="card login reveal" method="post" action="{base}/login" data-busy>
      {ui.BRAND_MARK}<h1>כניסה ללוח הבקרה</h1><p>האוטומציות של דרור ברק</p>{note}
      <label class="field"><span class="label">סיסמה</span><span class="pw">
        <input class="input" type="password" name="password" id="pw" autofocus autocomplete="current-password" required>
        <button type="button" class="btn btn-ghost btn-sm btn-icon" id="reveal" data-tip="הצג סיסמה">{ui.icon("eye", 16)}</button>
      </span></label>
      <button type="submit" class="btn btn-primary btn-lg btn-block"><span>כניסה</span>{ui.icon("arrow-left", 17)}</button>
      <div class="login-foot">{ui.icon("lock", 13)}<span>חיבור מאובטח · נשאר מחובר 12 שעות</span></div>
    </form></div>"""
    script = r"""
document.getElementById('reveal').addEventListener('click', function () {
  var pw = document.getElementById('pw'), show = pw.type === 'password';
  pw.type = show ? 'text' : 'password';
  this.innerHTML = UI.icon(show ? 'eye-off' : 'eye'); this.setAttribute('data-tip', show ? 'הסתר סיסמה' : 'הצג סיסמה');
  pw.focus();
});"""
    return ui.document("כניסה · לוח בקרה", body, kind="app", css=CSS, script=script, base=base).encode("utf-8")


def _row(entry: dict[str, Any], i: int = 0, base: str = "") -> str:
    ico, cls, status_label = _STATUS.get(str(entry.get("status")), ("info", "skip", ""))
    title = f"<span>{_esc(subjects.label_for(entry))}</span>"
    if entry.get("dry_run"):
        title += '<span class="badge badge-outline">הרצת ניסיון</span>'
    meta = []
    who = entry.get("client_id") or ""
    if subjects.is_lead(entry):
        name = str(entry.get("name") or "").strip()
        phone = subjects.phone_display(who)
        meta.append(f'<span class="badge badge-brand">{ui.icon("user-plus", 12)}<span>ליד</span></span>'
                    + (f'<span class="chip"><span>{_esc(name)}</span></span>' if name else "")
                    + (f'<span class="chip num"><bdi>{_esc(phone)}</bdi></span>' if phone else ""))
        who = ""
    # The agent's entries carry a ClickUp task id, not a client: its name is the
    # detail line and the task is linked, so a chip would only show a code.
    if who and entry.get("automation") != "clickup_to_claude":
        meta.append(f'<a class="chip" href="{base}/clients/{quote(str(who))}" data-client="{_esc(who)}">{ui.icon("users", 12)}'
                    f'<span>{_esc(who)}</span></a>')
    detail = entry.get("detail")
    # A detail that is just a URL is rendered as the link below, not as text.
    # Old entries predate the house style; show them in it.
    detail_html = (f'<div class="log-detail"><bdi>{_esc(text_style.humanize(str(detail)))}</bdi></div>'
                   if detail and not str(detail).startswith("http") else "")
    links = "".join(
        f'<a class="btn btn-sm" href="{_esc(url)}" target="_blank" rel="noopener noreferrer">'
        f'<span>{_esc(label)}</span>{ui.icon("external", 13)}</a>'
        for label, url in subjects.links_for(entry))
    links_html = f'<div class="log-links">{links}</div>' if links else ""
    return (f'<div class="log-row {cls}" title="{_esc(entry.get("action"))}">'
            f'<span class="log-dot {cls}" aria-label="{status_label}">{ui.icon(ico, 14)}</span>'
            f'<div><div class="log-title">{title}</div><div class="log-meta">{"".join(meta)}</div>'
            f'{detail_html}{links_html}</div>'
            f'<span class="log-time">{ui.when(entry.get("ts"), str(subjects.parse_ts(entry) or ""))}</span></div>')


def _query(q: dict[str, str], **change: str) -> str:
    merged = {**q, **change}
    return "&".join(f"{k}={quote(str(v))}" for k, v in merged.items() if v)


def _dashboard_page(entries: list[dict[str, Any]], q: dict[str, str], base: str = "") -> bytes:
    counts = subjects.counts(entries)
    days = q.get("days", "7")
    ranges = "".join(
        f'<a class="{"is-active" if days == str(d) else ""}" href="{base}/dashboard?{_query(q, days=str(d))}">{label}</a>'
        for d, label in ((1, "היום"), (7, "7 ימים"), (30, "30 יום"), (365, "הכל")))
    head = ui.page_head("פעילות", "כל מה שהאוטומציות עשו. לצפייה בלבד: שום דבר לא מופעל מכאן.",
                        f'<div class="segmented" role="tablist" aria-label="טווח זמן">{ranges}</div>')
    stats = (ui.stat("פעולות", counts["total"], ico="activity", tone="brand", i=0)
             + ui.stat("הצליחו", counts["ok"], ico="check-circle", tone="ok", i=1)
             + ui.stat("שגיאות", counts["error"], ico="x-circle", tone="err", i=2, hot=counts["error"] > 0)
             + ui.stat("דילוגים", counts["skipped"], ico="minus-circle", tone="warn", i=3))

    subject_opts = '<option value="" data-icon="activity">כל הנושאים</option>' + "".join(
        f'<option value="{s.key}" data-icon="{_SUBJECT_ICONS.get(s.key, "info")}"{" selected" if q.get("subject") == s.key else ""}>'
        f'{_esc(s.label)}</option>' for s in subjects.SUBJECTS.values())
    # Every known client, not just those in the current filter — otherwise picking
    # one client would remove every other option and strand you there.
    # The agent's entries name ClickUp tasks, not clients: they are not filter options.
    all_entries = [e for e in (_SAMPLE if DRY_RUN else run_log.read_all())
                   if e.get("automation") != "clickup_to_claude" and not subjects.is_lead(e)]
    client_opts = '<option value="" data-icon="users">כל הלקוחות</option>' + "".join(
        f'<option value="{_esc(c)}" data-client="{_esc(c)}" data-avatar="{_esc(ui.initials(c))}"'
        f'{" selected" if q.get("client") == c else ""}>{_esc(c)}</option>'
        for c in subjects.client_ids(all_entries))
    filters = f"""<form class="filters reveal" method="get" action="{base}/dashboard" style="--i:4">
      <input type="hidden" name="days" value="{_esc(days)}">
      <label class="with-icon">{ui.icon("search", 16)}<input class="input" type="search" name="q" data-search
        placeholder="חיפוש בפעילות" value="{_esc(q.get('q', ''))}" aria-label="חיפוש"><span class="kbd">/</span></label>
      <select class="select" name="subject" data-autosubmit data-picker="inline" aria-label="נושא">{subject_opts}</select>
      <select class="select" name="client" id="clientsel" data-autosubmit data-picker="search inline" aria-label="לקוח">{client_opts}</select>
      <button type="submit" class="btn">סינון</button>
    </form>"""

    sections = ""
    # Failures first — being in the dark about breakages is the problem this solves.
    failed = subjects.failures(entries)
    if failed:
        rows = "".join(_row(e, base=base) for e in failed[:20])
        sections += (f'<details class="card log-card is-alert reveal" open style="--i:5"><summary>'
                     f'<span class="subj-icon">{ui.icon("alert", 16)}</span><h2 class="card-title">דורש טיפול</h2>'
                     f'<span class="badge badge-err num">{len(failed)}</span>'
                     f'{ui.icon("chevron-down", 16, cls="chev")}</summary>{rows}</details>')
    for n, (subject, group) in enumerate(subjects.group_by_subject(entries)):
        rows = "".join(_row(e, base=base) for e in sorted(group, key=lambda e: str(e.get("ts")), reverse=True))
        sections += (f'<details class="card log-card reveal" open style="--i:{6 + n}"><summary>'
                     f'<span class="subj-icon">{ui.icon(_SUBJECT_ICONS.get(subject.key, "info"), 16)}</span>'
                     f'<h2 class="card-title">{_esc(subject.label)}</h2>'
                     f'<span class="badge num">{len(group)}</span>'
                     f'{ui.icon("chevron-down", 16, cls="chev")}</summary>{rows}</details>')
    if not entries:
        sections = ('<div class="card">' + ui.empty(
            "אין פעילות בטווח הזה.", "כשאוטומציה תרוץ, היא תופיע כאן. אפשר להרחיב את טווח הזמן או לנקות את הסינון.",
            ico="activity", action=f'<a class="btn btn-sm" href="{base}/dashboard?days=365">{ui.icon("rotate", 14)}<span>הצג הכל</span></a>')
            + "</div>")

    script = r"""
// Client ids are ClickUp task ids; show names once ClickUp answers (cached for the tab).
(function () {
  function initials(name) { return name.split(/\s+/).filter(Boolean).slice(0, 2).map(function (w) { return w[0]; }).join(''); }
  function apply(map) {
    document.querySelectorAll('[data-client]').forEach(function (n) {
      var name = map[n.getAttribute('data-client')];
      if (n.tagName === 'OPTION') {
        // Not a client ClickUp knows (a deleted one, a test task): out of the list.
        if (!name) { if (!n.selected) n.remove(); return; }
        n.textContent = name; n.dataset.avatar = initials(name); return;
      }
      if (name) { var t = n.querySelector('span'); if (t) t.textContent = name; }
    });
    var sel = document.getElementById('clientsel'); if (sel && sel._picker) sel._picker.refresh();
  }
  var cached = null; try { cached = JSON.parse(sessionStorage.getItem('clients') || 'null'); } catch (e) {}
  if (cached && Date.now() - cached.at < 600000) { apply(cached.map); return; }
  if (!document.querySelector('[data-client]')) return;
  UI.api('/clients').then(function (d) {
    var map = {}; (d.clients || []).forEach(function (c) { map[c.id] = c.name; });
    try { sessionStorage.setItem('clients', JSON.stringify({at: Date.now(), map: map})); } catch (e) {}
    apply(map);
  });
})();"""
    body = head + f'<div class="stats">{stats}</div>' + filters + sections
    return ui.app_page(base, "dashboard", "לוח בקרה · דרור ברק", body, script=script, css=CSS, spa=True).encode("utf-8")


LEADS_CSS = """
.lead-cell { display: flex; align-items: center; gap: 10px; }
.lead-av { width: 34px; height: 34px; border-radius: 50%; flex: none; display: grid; place-items: center; color: #fff;
  font-size: 12.5px; font-weight: 700; background: linear-gradient(135deg, #00c2e0, #2f7de1); }
.lead-av.anon { background: var(--surface-active); color: var(--fg-subtle); }
.lead-name { font-weight: 600; color: var(--fg); }
.lead-name.anon { color: var(--fg-muted); font-weight: 500; }
.lead-phone { font-size: 12.5px; color: var(--fg-muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.lead-sub { margin-top: 2px; font-size: 12px; color: var(--fg-subtle); }
.lead-acts { display: flex; gap: 4px; justify-content: flex-end; }
.leads-note { margin: -8px 0 18px; }
"""


def _il_day(ts: Any) -> str:
    local = ui.local_time(ts)
    return local[:10] if local else ""


def leads_from(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per person (phone) from the Smoove entries, newest first."""
    leads: dict[str, dict[str, Any]] = {}
    for e in sorted((e for e in entries if subjects.is_lead(e)), key=lambda e: str(e.get("ts"))):
        phone = str(e.get("client_id") or "")
        if not phone:
            continue
        lead = leads.setdefault(phone, {"phone": phone, "name": "", "lists": [], "first": e.get("ts"),
                                        "count": 0, "sent": 0})
        lead["count"] += 1
        lead["last"], lead["status"], lead["action"] = e.get("ts"), e.get("status"), e.get("action")
        lead["sent"] += e.get("action") == "flow_sent" and e.get("status") == "ok"
        if e.get("name"):
            lead["name"] = str(e["name"]).strip()
        if e.get("msg") and str(e["msg"]) not in lead["lists"]:
            lead["lists"].append(str(e["msg"]))
        if e.get("msg") and e.get("list_name"):
            _LIST_NAMES[str(e["msg"])] = str(e["list_name"])
    return sorted(leads.values(), key=lambda l: str(l["last"]), reverse=True)


#: A Smoove list's name (its ManyChat Flow's), as logged with its leads.
_LIST_NAMES: dict[str, str] = {}


def _list_label(msg: str) -> str:
    return _LIST_NAMES.get(msg) or (f"רשימה {msg}" if msg.isdigit() else msg)


def _leads_page(entries: list[dict[str, Any]], base: str = "") -> bytes:
    leads = leads_from(entries)
    today = _il_day(datetime.now(timezone.utc).isoformat())
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    # Both the counters and the period filter go by the latest signup; "today" is
    # the calendar day in Israel, not the last 24 hours.
    new_today = sum(1 for l in leads if _il_day(l["last"]) == today)
    new_week = sum(1 for l in leads if str(l["last"]) >= week_ago)
    sent = sum(l["sent"] for l in leads)
    stats = (ui.stat("נרשמו היום", new_today, ico="user-plus", tone="brand", i=0)
             + ui.stat("7 ימים אחרונים", new_week, ico="calendar", i=1)
             + ui.stat("סה״כ לידים", len(leads), ico="users", i=2)
             + ui.stat("הודעות וואטסאפ שנשלחו", sent, ico="message", tone="ok", i=3))
    lists = sorted({m for l in leads for m in l["lists"]})
    list_opts = '<option value="" data-icon="inbox">כל הרשימות</option>' + "".join(
        f'<option value="{_esc(m)}" data-icon="send">{_esc(_list_label(m))}</option>' for m in lists)
    rows = ""
    for l in leads:
        name, phone = l["name"], subjects.phone_display(l["phone"])
        digits = l["phone"].lstrip("+")
        ok = l.get("action") == "flow_sent" and l.get("status") == "ok"
        status = ('<span class="badge badge-ok badge-dot">וואטסאפ נשלח</span>' if ok else
                  f'<span class="badge badge-warn badge-dot">{_esc(subjects.label_for(l))}</span>')
        again = f'<span class="badge badge-outline num">{l["count"]} הרשמות</span>' if l["count"] > 1 else ""
        avatar = (f'<span class="lead-av">{_esc(ui.initials(name))}</span>' if name
                  else f'<span class="lead-av anon">{ui.icon("user-plus", 15)}</span>')
        who = (f'<div class="lead-name">{_esc(name)}</div>' if name else '<div class="lead-name anon">ללא שם</div>')
        search = f"{name} {l['phone']} {phone} {phone.replace('-', '')}".lower()
        last_ms = int(datetime.fromisoformat(str(l["last"]).replace("Z", "+00:00")).timestamp() * 1000) if l.get("last") else 0
        rows += (f'<tr data-last="{last_ms}" data-day="{_il_day(l.get("last"))}" data-lists="{_esc(" ".join(l["lists"]))}" data-q="{_esc(search)}">'
                 f'<td data-v="{_esc(name or "תתת")}"><div class="lead-cell">{avatar}<div>{who}<div class="lead-phone"><bdi>{_esc(phone)}</bdi></div>'
                 f'<div class="lead-sub show-sm">{_esc(" · ".join(_list_label(m) for m in l["lists"]))} · '
                 f'{ui.when(l.get("last"), "-")} · {"וואטסאפ נשלח" if ok else _esc(subjects.label_for(l))}</div></div></div></td>'
                 f'<td class="hide-sm" data-v="{_esc(" ".join(_list_label(m) for m in l["lists"]))}">'
                 f'{"".join(f"<span class=badge>{_esc(_list_label(m))}</span> " for m in l["lists"])}</td>'
                 f'<td class="muted hide-sm" data-v="{last_ms}">{ui.when(l.get("last"), "-")}</td>'
                 f'<td class="hide-sm" data-v="{"וואטסאפ נשלח" if ok else _esc(subjects.label_for(l))}">{status} {again}</td>'
                 f'<td><div class="lead-acts">'
                 f'<a class="btn btn-sm btn-icon" href="https://wa.me/{_esc(digits)}" target="_blank" rel="noopener" data-tip="פתיחה בוואטסאפ">{ui.icon("message", 15)}</a>'
                 f'<a class="btn btn-sm btn-icon" href="tel:{_esc(l["phone"])}" data-tip="חיוג">{ui.icon("phone", 15)}</a>'
                 f'<button class="btn btn-sm btn-icon" type="button" data-copy="{_esc(phone)}" data-tip="העתקת המספר">{ui.icon("copy", 15)}</button>'
                 f'</div></td></tr>')
    periods = "".join(f'<button type="button" data-days="{d}" class="{"is-active" if d == 0 else ""}">{label}</button>'
                      for d, label in ((0, "הכל"), (1, "היום"), (7, "7 ימים"), (30, "30 יום")))
    head = ui.page_head("לידים", "מי שנרשם דרך Smoove וקיבל הודעת וואטסאפ. הם עדיין לא ב-ClickUp: "
                        "האם ומתי להכניס אותם לשם זו החלטה של דרור.")
    note = ""
    if rows:
        table = (f"""<div class="toolbar reveal" style="--i:4"><div class="segmented" id="period">{periods}</div>
          <select class="select" id="listf" data-picker="inline" aria-label="רשימה">{list_opts}</select>
          <label class="with-icon grow">{ui.icon("search", 16)}<input class="input" type="search" id="find" data-search
            placeholder="חיפוש לפי שם או טלפון" aria-label="חיפוש"><span class="kbd">/</span></label></div>
          <div class="table-wrap reveal" style="--i:5"><table class="table" data-sortable><thead><tr><th data-sort>ליד</th>
          <th class="hide-sm" data-sort>רשימה</th>
          <th class="hide-sm" data-sort="num" data-first="descending" aria-sort="descending">נרשם</th>
          <th class="hide-sm" data-sort>סטטוס</th><th></th></tr></thead><tbody>{rows}</tbody></table>
          <div id="none" style="display:none">{ui.empty("אין לידים שמתאימים", "נסה טווח זמן אחר או חיפוש אחר.", ico="search")}</div></div>""")
    else:
        table = '<div class="card">' + ui.empty(
            "עדיין אין לידים", "כשמישהו נרשם דרך Smoove ומקבל הודעת וואטסאפ, הוא יופיע כאן.", ico="user-plus") + "</div>"
    script = r"""
var days = 0, list = '', find = document.getElementById('find'), TODAY = document.body.dataset.today;
function apply() {
  var q = (find ? find.value : '').trim().toLowerCase().replace(/-/g, ''), since = days > 1 ? Date.now() - days * 86400000 : 0, n = 0;
  document.querySelectorAll('tr[data-last]').forEach(function (tr) {
    var ok = (days === 1 ? tr.dataset.day === TODAY : +tr.dataset.last >= since) && (!list || (' ' + tr.dataset.lists + ' ').indexOf(' ' + list + ' ') >= 0) &&
             (!q || tr.dataset.q.replace(/-/g, '').indexOf(q) >= 0);
    tr.hidden = !ok; if (ok) n++; });
  var none = document.getElementById('none'); if (none) none.style.display = n ? 'none' : 'block';
}
var per = document.getElementById('period');
if (per) per.addEventListener('click', function (e) { var b = e.target.closest('button'); if (!b) return;
  per.querySelectorAll('button').forEach(function (x) { x.classList.toggle('is-active', x === b); }); days = +b.dataset.days; apply(); });
var lf = document.getElementById('listf'); if (lf) lf.addEventListener('change', function () { list = lf.value; apply(); });
if (find) find.addEventListener('input', apply);
document.querySelector('main.page').addEventListener('click', function (e) { var b = e.target.closest('[data-copy]'); if (b) UI.copy(b.dataset.copy, b); });
"""
    script = f"document.body.dataset.today = {json.dumps(today)};" + script
    return ui.app_page(base, "leads", "לידים · דרור ברק", head + note + f'<div class="stats">{stats}</div>' + table,
                       script=script, css=CSS + LEADS_CSS, spa=True, fill=True).encode("utf-8")


def _load_leads() -> list[dict[str, Any]]:
    return _SAMPLE if DRY_RUN else run_log.read_all()


def client_screens(path: str, base: str, *, dry_run: bool = False,
                   query: Optional[dict[str, str]] = None) -> Optional[bytes]:
    """/clients, /clients/<id>, /documents (see :mod:`src.clients_pages`), /contracts and
    /contracts/<id> (:mod:`src.contracts_pages`); None when not found."""
    from urllib.parse import unquote

    from . import contracts_pages

    entries = _load_leads()
    if path == "/contracts":
        return contracts_pages.contracts_page(entries, base, dry_run=dry_run).encode("utf-8")
    if path.startswith("/contracts/"):
        client_id = unquote(path[len("/contracts/"):]).strip("/")
        page = (contracts_pages.contract_page(entries, base, client_id, dry_run=dry_run,
                                              new=bool((query or {}).get("new"))) if client_id else None)
        return page.encode("utf-8") if page else None
    if path == "/documents":
        return clients_pages.documents_page(entries, base, dry_run=dry_run).encode("utf-8")
    if path == "/clients":
        return clients_pages.clients_page(entries, base, dry_run=dry_run).encode("utf-8")
    client_id = unquote(path[len("/clients/"):]).strip("/")
    page = clients_pages.client_page(entries, base, client_id, dry_run=dry_run) if client_id else None
    return page.encode("utf-8") if page else None


def _not_found(base: str = "") -> bytes:
    return _page("לא נמצא", '<main class="page">' + ui.empty(
        "הדף לא נמצא", "ייתכן שהקישור ישן.", ico="search",
        action=f'<a class="btn btn-primary" href="{base}/dashboard">חזרה ללוח הבקרה</a>') + "</main>")


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
    {"ts": "2026-07-15T10:30:00Z", "automation": "send_quote", "action": "quote_sent",
     "status": "ok", "client_id": "מכללת אלפא",
     "detail": "https://sign.drorbrk.co.il/sign?t=abc12345"},
    {"ts": "2026-07-15T11:05:00Z", "automation": "sign", "action": "signed",
     "status": "ok", "client_id": "מכללת בטא",
     "detail": "https://drive.google.com/file/d/xyz/view"},
    {"ts": "2026-07-15T12:20:00Z", "automation": "campaign_summary", "action": "campaign_report_built",
     "status": "error", "client_id": "מכללת אלפא", "detail": "Meta API: token expired"},
    {"ts": "2026-07-15T08:05:00Z", "automation": "smoove_to_manychat", "action": "flow_sent", "status": "ok",
     "client_id": "+972501234567", "name": "דנה", "msg": "889128", "created": True,
     "detail": "ליד חדש, נשלחה הודעת וואטסאפ (רשימה 889128)"},
    {"ts": "2026-07-15T09:40:00Z", "automation": "smoove_to_manychat", "action": "flow_sent", "status": "ok",
     "client_id": "+972547654321", "msg": "1142673", "created": True,
     "detail": "ליד חדש, נשלחה הודעת וואטסאפ (רשימה 1142673)"},
    {"ts": "2026-07-14T16:00:00Z", "automation": "strategy_bot", "action": "strategy_ready", "status": "ok",
     "client_id": "מכללת אלפא", "url": "https://docs.google.com/document/d/strategy-v1",
     "detail": "לפי שאלון מ-2026-07-12, 2 ערוצים נותחו"},
    {"ts": "2026-07-15T16:30:00Z", "automation": "strategy_bot", "action": "strategy_ready", "status": "ok",
     "client_id": "מכללת אלפא", "url": "https://docs.google.com/document/d/strategy-v2",
     "detail": "לפי שאלון מ-2026-07-15, 2 ערוצים נותחו"},
    {"ts": "2026-07-01T08:00:00Z", "automation": "campaign_summary", "action": "campaign_summary_ready", "status": "ok",
     "client_id": "מכללת אלפא", "url": "https://drive.google.com/file/d/june-report/view",
     "detail": "יוני 2026 · 6,335 ₪ · 105 לידים"},
    {"ts": "2026-07-12T10:00:00Z", "automation": "questionnaire", "action": "questionnaire_answered", "status": "ok",
     "client_id": "מכללת אלפא", "url": "https://docs.google.com/document/d/answers",
     "detail": "שאלון הכנה לבניית אסטרטגיה"},
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
        if route.path in ("/clients", "/documents", "/contracts") or route.path.startswith(("/clients/", "/contracts/")):
            if not self._authed():
                return self._redirect("/login")
            page = client_screens(route.path, "", dry_run=DRY_RUN,
                                  query={k: v[0] for k, v in parse_qs(route.query).items() if v})
            return self._send(200, page) if page else self._send(404, _not_found())
        if route.path == "/leads":
            if not self._authed():
                return self._redirect("/login")
            return self._send(200, _leads_page(_load_leads()))
        if route.path == "/login":
            return self._send(200, _login_page())
        if route.path == "/logout":
            _sessions.pop(self._cookie() or "", None)
            return self._redirect("/login", clear=True)
        if route.path == "/healthz":
            return self._send(200, b"ok", "text/plain")
        if route.path == "/admin" or route.path.startswith("/admin/"):
            return self._admin("GET", route)
        self._send(404, _not_found())

    def _admin(self, method: str, route: Any) -> None:
        if not self._authed():
            if route.path.startswith("/admin/api/"):
                return self._send(401, b'{"errors":["login"]}', "application/json")
            return self._redirect("/login")
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        query = {k: v[0] for k, v in parse_qs(route.query).items() if v}
        headers = {k.lower(): v for k, v in self.headers.items()}
        resp = questionnaire_admin.handle(method, route.path, query, body, headers, dry_run=DRY_RUN)
        if resp.status in (301, 302, 303):
            return self._redirect(resp.location)
        extra = {"Content-Disposition": resp.disposition} if resp.disposition else {}
        self._send(resp.status, resp.body.encode("utf-8"), resp.content_type, extra)

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path)
        if route.path.startswith("/admin/"):
            return self._admin("POST", route)
        if route.path != "/login":
            return self._send(404, _not_found())

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

    def _send(self, status: int, data: bytes, ctype: str = "text/html; charset=utf-8",
              extra: Optional[dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
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
