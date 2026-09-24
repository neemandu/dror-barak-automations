"""The לקוחות and מסמכים screens: everything the system knows about a client, in one place.

Dror asked where the reports live. Each one went to Drive, to a comment on the
ClickUp task and to an email, and showed in the activity as one line among many;
finding "the August report for X" meant searching. So:

* ``/documents``: every document the automations made (strategies, campaign
  reports, prep reports, questionnaire answers, signed contracts, the agent's
  Docs), filterable by kind and client, newest first, the latest of each kind per
  client marked.
* ``/clients``: the clients in ClickUp and where each one stands.
* ``/clients/<id>``: one client's card: status and funnel stage, contact and
  links, their documents, their questionnaire, and what ran for them.

Read-only like the rest of the dashboard: nothing here runs an automation. The
sources already exist: ClickUp (the client record, cached a minute per Lambda
container), the run-log (documents and activity) and the questionnaire store.
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

from . import ui
from .lib import crm_fields, questionnaire_store, subjects

_esc = ui.esc

#: The run-log actions that produce a document: action -> (kind, label, icon).
DOC_TYPES: dict[str, tuple[str, str, str]] = {
    "strategy_ready": ("strategy", "אסטרטגיה שיווקית", "sparkles"),
    "campaign_summary_ready": ("campaign", "דוח קמפיינים חודשי", "gauge"),
    "prep_report_ready": ("prep", "דוח הכנה לרשתות", "globe"),
    "questionnaire_answered": ("answers", "תשובות לשאלון", "clipboard"),
    "signed": ("contract", "הסכם חתום", "pen"),
    "drive_doc_created": ("agent", "מסמך ש-Claude יצר", "file"),
}
KIND_NAMES = {"strategy": "אסטרטגיות", "campaign": "דוחות קמפיינים", "prep": "דוחות הכנה",
              "answers": "תשובות לשאלון", "contract": "הסכמים", "agent": "מ-Claude"}

STATUS_BADGES = {
    crm_fields.STATUS_LEAD: ("ליד", "badge-brand"),
    crm_fields.STATUS_ACTIVE: ("לקוח פעיל", "badge-ok"),
    crm_fields.STATUS_PAUSED: ("מושהה", "badge-warn"),
    crm_fields.STATUS_FINISHED: ("הסתיים", ""),
}
STAGE_NAMES = {
    crm_fields.SUB_INITIAL_MEETING: "פגישה ראשונה",
    crm_fields.SUB_QUESTIONNAIRE_SENT: "נשלח שאלון",
    crm_fields.SUB_QUOTE_SENT: "הצעה נשלחה",
    crm_fields.SUB_SIGNED: "חתם",
    crm_fields.SUB_IN_WORK: "בעבודה",
}
#: How far along the funnel each stage is (the card's stepper).
_RANK = {crm_fields.SUB_INITIAL_MEETING: 0, crm_fields.SUB_QUESTIONNAIRE_SENT: 0,
         crm_fields.SUB_QUOTE_SENT: 1, crm_fields.SUB_SIGNED: 2, crm_fields.SUB_IN_WORK: 4}
STEPS = ("פגישה ראשונה", "הצעה נשלחה", "חתם", "שאלון מולא", "בעבודה")

CSS = """
.doc-cell { display: flex; align-items: center; gap: 12px; min-width: 0; }
.doc-ic { width: 36px; height: 36px; border-radius: 10px; flex: none; display: grid; place-items: center;
  background: var(--surface-2); border: 1px solid var(--border); color: var(--fg-2); }
.doc-ic.k-strategy { background: var(--brand-soft); border-color: transparent; color: var(--brand-ink); }
.doc-ic.k-campaign { background: var(--success-soft); border-color: transparent; color: var(--success-ink); }
.doc-ic.k-prep { background: var(--warn-soft); border-color: transparent; color: var(--warn-ink); }
.doc-title { font-weight: 600; color: var(--fg); }
.doc-sub { font-size: 12.5px; color: var(--fg-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 380px; }
.doc-acts { display: flex; gap: 4px; justify-content: flex-end; }
.person-link { display: inline-flex; align-items: center; gap: 8px; color: var(--fg); font-weight: 500; text-decoration: none !important; }
.person-link:hover span:last-child { color: var(--brand); }
.avatar { width: 32px; height: 32px; border-radius: 50%; flex: none; display: grid; place-items: center; font-size: 12.5px;
  font-weight: 700; color: #fff; background: linear-gradient(135deg, #00c2e0, #2f7de1); }
.avatar.sm { width: 26px; height: 26px; font-size: 11px; }
.avatar.xl { width: 64px; height: 64px; font-size: 22px; box-shadow: 0 10px 22px -10px rgba(47, 125, 225, .75); }
.hero { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; margin-bottom: 22px; }
.hero h1 { margin: 0; font-size: 26px; font-weight: 800; letter-spacing: -.015em; }
.hero-badges { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }
.hero-meta { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-top: 8px; font-size: 13.5px; color: var(--fg-muted); }
.hero-meta span { display: inline-flex; align-items: center; gap: 6px; }
.stepper { display: grid; grid-template-columns: repeat(5, 1fr); padding: 18px 20px; margin-bottom: 18px; }
.step { position: relative; display: flex; flex-direction: column; align-items: center; gap: 8px; text-align: center;
  font-size: 13px; font-weight: 500; color: var(--fg-muted); }
/* each step's line runs to the one before it, on its right (RTL) */
.step::before { content: ""; position: absolute; top: 14px; left: 50%; width: 100%; height: 2px; background: var(--border); z-index: 0; }
.step:first-child::before { display: none; }
.step.done::before, .step.now::before, .step.wait::before { background: var(--success); }
.step .dot { position: relative; z-index: 1; width: 30px; height: 30px; border-radius: 50%; display: grid; place-items: center;
  background: var(--surface); border: 2px solid var(--border); color: var(--fg-subtle); font-size: 12px; font-weight: 700; }
.step.done { color: var(--fg-2); }
.step.done .dot { background: var(--success); border-color: var(--success); color: #fff; }
.step.done .dot svg { width: 15px; height: 15px; stroke-width: 3; }
.step.now { color: var(--fg); font-weight: 600; }
.step.now .dot { border-color: var(--brand); color: var(--brand); box-shadow: 0 0 0 5px var(--ring-soft); }
.step.wait { color: var(--warn-ink); font-weight: 600; }
.step.wait .dot { border-color: var(--warn); color: var(--warn-ink); background: var(--warn-soft); }
.cc-grid { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 16px; align-items: start; }
.cc-main, .cc-side { display: grid; gap: 16px; min-width: 0; }
.cc-list .doc-row { display: flex; align-items: center; gap: 12px; padding: 12px 18px; border-bottom: 1px solid var(--border-soft);
  transition: background var(--d1); }
.cc-list .doc-row:last-child { border-bottom: 0; }
.cc-list .doc-row:hover { background: var(--surface-2); }
.cc-list .doc-row .grow { flex: 1; min-width: 0; }
.facts { margin: 0; padding: 6px 18px 14px; display: grid; gap: 0; }
.facts div { display: flex; justify-content: space-between; gap: 12px; padding: 9px 0; border-bottom: 1px solid var(--border-soft); font-size: 13.5px; }
.facts div:last-child { border-bottom: 0; }
.facts dt { color: var(--fg-muted); }
.facts dd { margin: 0; color: var(--fg); font-weight: 500; text-align: left; overflow-wrap: anywhere; }
.qbox { padding: 16px 18px; display: grid; gap: 10px; }
.day { padding: 10px 18px 4px; font-size: 12px; font-weight: 600; color: var(--fg-subtle); }
.tl-row { display: flex; gap: 10px; align-items: flex-start; padding: 8px 18px; }
.tl-row .log-dot { margin-top: 2px; }
.tl-row .grow { flex: 1; min-width: 0; }
.tl-title { font-weight: 500; color: var(--fg); }
.tl-detail { font-size: 12.5px; color: var(--fg-muted); overflow-wrap: anywhere; }
.tl-time { font-size: 12px; color: var(--fg-subtle); white-space: nowrap; }
.log-dot { width: 22px; height: 22px; border-radius: 50%; flex: none; display: grid; place-items: center; }
.log-dot svg { width: 12px; height: 12px; stroke-width: 2.6; }
.log-dot.ok { background: var(--success-soft); color: var(--success-ink); }
.log-dot.err { background: var(--danger-soft); color: var(--danger-ink); }
.log-dot.skip { background: var(--warn-soft); color: var(--warn-ink); }
.more-note { padding: 10px 18px 14px; font-size: 12.5px; color: var(--fg-subtle); }
@media (max-width: 1100px) { .cc-grid { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 720px) {
  .stepper { grid-template-columns: repeat(5, minmax(56px, 1fr)); overflow-x: auto; padding: 14px 10px; }
  .step { font-size: 11.5px; }
  .doc-sub { max-width: 170px; }
  .cc-list .doc-row { padding: 12px 14px; gap: 10px; }
  .cc-list .doc-row > .badge { display: none; }
  .doc-acts .btn span { display: none; }
  .doc-acts .btn:not(.btn-icon) { width: 30px; padding: 0; }
  .hero h1 { font-size: 22px; }
}
"""

# ------------------------------------------------------------------- data

_CACHE: dict[str, Any] = {"at": 0.0, "clients": None}
CACHE_SECONDS = 60


def all_clients(dry_run: bool = False) -> list[dict[str, Any]]:
    """The clients in ClickUp (one paged call), kept a minute: the list, the card
    and the documents all ask, and a warm Lambda should not ask ClickUp thrice."""
    if dry_run:
        from .lib.clients.crm import _fixture_client
        from .questionnaire_admin import _clients

        return [{**_fixture_client(c["id"]), "name": c["name"],
                 "status": c["status"] if c["status"] in STATUS_BADGES else crm_fields.STATUS_ACTIVE}
                for c in _clients(True)]
    if _CACHE["clients"] is not None and time.time() - _CACHE["at"] < CACHE_SECONDS:
        return _CACHE["clients"]
    from .lib.clients.crm import CrmClient

    clients = CrmClient()._all_clients()
    _CACHE.update(at=time.time(), clients=clients)
    return clients


def documents_from(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every document the run-log links to, newest first; ``latest`` marks the
    newest of each kind per client (a strategy built twice has an older version)."""
    docs = []
    for e in entries:
        kind = DOC_TYPES.get(str(e.get("action") or ""))
        if not kind or e.get("status") != "ok":
            continue
        links = subjects.links_for(e)
        url = str(e.get("url") or (links[0][1] if links else ""))
        if not url:
            continue
        docs.append({"kind": kind[0], "label": kind[1], "icon": kind[2], "url": url, "ts": e.get("ts"),
                     "client_id": str(e.get("client_id") or ""), "detail": str(e.get("detail") or ""),
                     "automation": e.get("automation"), "dry_run": bool(e.get("dry_run"))})
    docs.sort(key=lambda d: str(d["ts"]), reverse=True)
    seen: set[tuple[str, str]] = set()
    for d in docs:
        key = (d["client_id"], d["kind"])
        d["latest"] = key not in seen
        seen.add(key)
    return docs


def _ms(iso: Any) -> int:
    try:
        return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return 0


def _names(dry_run: bool) -> tuple[dict[str, str], str]:
    try:
        return {str(c["id"]): str(c.get("name") or c["id"]) for c in all_clients(dry_run)}, ""
    except Exception as exc:  # noqa: BLE001 - the page still shows what the log knows
        return {}, f"לא הצלחתי לטעון לקוחות מ-ClickUp: {exc}"


def _responses_by_client() -> dict[str, dict[str, Any]]:
    """Each client's questionnaire: the answered one if there is one, else the newest sent."""
    out: dict[str, dict[str, Any]] = {}
    for r in sorted(questionnaire_store.list_responses(), key=lambda r: str(r.get("sent_at") or "")):
        cid = str(r.get("client_id") or "")
        if cid and not (out.get(cid, {}).get("status") == "answered" and r.get("status") != "answered"):
            out[cid] = r
    return out


# ---------------------------------------------------------------- pieces


def _person(base: str, client_id: str, names: dict[str, str], *, automation: Any = None) -> str:
    if client_id in names:
        name = names[client_id]
        return (f'<a class="person-link" href="{base}/clients/{quote(client_id)}"><span class="avatar sm">'
                f'{_esc(ui.initials(name))}</span><span>{_esc(name)}</span></a>')
    if automation == "clickup_to_claude" and client_id:
        return (f'<a class="chip" href="https://app.clickup.com/t/{quote(client_id)}" target="_blank" rel="noopener">'
                f'{ui.icon("clipboard", 12)}<span>משימה ב-ClickUp</span></a>')
    return f'<span class="subtle">{_esc(client_id) or "-"}</span>'


def _doc_actions(d: dict[str, Any]) -> str:
    return (f'<div class="doc-acts"><a class="btn btn-sm" href="{_esc(d["url"])}" target="_blank" rel="noopener">'
            f'<span>פתיחה</span>{ui.icon("external", 13)}</a>'
            f'<button class="btn btn-sm btn-icon" type="button" data-copy="{_esc(d["url"])}" data-tip="העתקת הקישור">'
            f'{ui.icon("copy", 14)}</button></div>')


def _doc_head(d: dict[str, Any], *, grow: bool = False) -> str:
    sub = d["detail"] if not d["detail"].startswith("http") else ""
    return (f'<div class="doc-cell{" grow" if grow else ""}"><span class="doc-ic k-{d["kind"]}">{ui.icon(d["icon"], 17)}</span>'
            f'<div style="min-width:0"><div class="doc-title">{_esc(d["label"])}</div>'
            + (f'<div class="doc-sub" title="{_esc(sub)}">{_esc(sub)}</div>' if sub else "") + "</div></div>")


def _version(d: dict[str, Any]) -> str:
    return ('<span class="badge badge-ok">העדכני</span>' if d["latest"]
            else '<span class="badge badge-outline">גרסה קודמת</span>')


COPY_JS = r"""
document.querySelector('main.page').addEventListener('click', function (e) {
  var b = e.target.closest('[data-copy]'); if (b) UI.copy(b.dataset.copy, b); });
"""

# ---------------------------------------------------------------- documents


def documents_page(entries: list[dict[str, Any]], base: str = "", *, dry_run: bool = False) -> str:
    docs = documents_from(entries)
    names, error = _names(dry_run)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    kinds = Counter(d["kind"] for d in docs)
    stats = (ui.stat("מסמכים", len(docs), ico="file", tone="brand", i=0)
             + ui.stat("החודש", sum(1 for d in docs if str(d["ts"]).startswith(month)), ico="calendar", i=1)
             + ui.stat("אסטרטגיות", kinds["strategy"], ico="sparkles", i=2)
             + ui.stat("דוחות קמפיינים", kinds["campaign"], ico="gauge", tone="ok", i=3))
    rows = ""
    for d in docs:
        who = names.get(d["client_id"], "")
        search = f'{d["label"]} {d["detail"]} {who}'.lower()
        rows += (f'<tr data-kind="{d["kind"]}" data-client="{_esc(d["client_id"])}" data-q="{_esc(search)}">'
                 f'<td data-v="{_esc(d["label"])}">{_doc_head(d)}</td>'
                 f'<td class="hide-sm" data-v="{_esc(who or d["client_id"])}">{_person(base, d["client_id"], names, automation=d["automation"])}</td>'
                 f'<td class="muted" data-v="{_ms(d["ts"])}">{ui.when(d["ts"], "-")}</td>'
                 f'<td class="hide-sm" data-v="{0 if d["latest"] else 1}">{_version(d)}</td>'
                 f'<td>{_doc_actions(d)}</td></tr>')
    segs = f'<button class="is-active" data-f="">הכל <span class="count">{len(docs)}</span></button>' + "".join(
        f'<button data-f="{k}">{KIND_NAMES[k]} <span class="count">{kinds[k]}</span></button>'
        for k in KIND_NAMES if kinds[k])
    clients_with_docs = sorted({d["client_id"] for d in docs if d["client_id"] in names}, key=lambda c: names[c])
    client_opts = '<option value="" data-icon="users">כל הלקוחות</option>' + "".join(
        f'<option value="{_esc(c)}" data-avatar="{_esc(ui.initials(names[c]))}">{_esc(names[c])}</option>'
        for c in clients_with_docs)
    head = ui.page_head("מסמכים", "כל מה שהמערכת הפיקה: אסטרטגיות, דוחות ותשובות. המסמכים עצמם בתיקיות הלקוחות ב-Drive; "
                        "כאן מוצאים ופותחים.")
    note = (f'<div class="alert alert-warn" style="margin-bottom:14px">{ui.icon("alert")}<span>{_esc(error)}</span></div>'
            if error else "")
    if rows:
        body = (f"""<div class="toolbar reveal" style="--i:4"><div class="segmented" id="seg">{segs}</div>
          <select class="select" id="clientf" data-picker="search inline" aria-label="לקוח">{client_opts}</select>
          <label class="with-icon grow">{ui.icon("search", 16)}<input class="input" type="search" id="find" data-search
            placeholder="חיפוש מסמך או לקוח" aria-label="חיפוש"><span class="kbd">/</span></label></div>
          <div class="table-wrap reveal" style="--i:5"><table class="table" data-sortable><thead><tr>
            <th data-sort>מסמך</th><th class="hide-sm" data-sort>לקוח</th>
            <th data-sort="num" data-first="descending" aria-sort="descending">נוצר</th>
            <th class="hide-sm" data-sort="num">גרסה</th><th></th></tr></thead><tbody>{rows}</tbody></table>
            <div id="none" style="display:none">{ui.empty("אין מסמכים שמתאימים", "נסה סוג אחר, לקוח אחר או חיפוש אחר.", ico="search")}</div></div>""")
    else:
        body = '<div class="card">' + ui.empty(
            "עדיין אין מסמכים", "אסטרטגיות, דוחות ותשובות לשאלון יופיעו כאן ברגע שהמערכת תפיק אותם.", ico="file") + "</div>"
    script = COPY_JS + r"""
var kind = '', client = '', find = document.getElementById('find');
function apply() {
  var q = (find ? find.value : '').trim().toLowerCase(), n = 0;
  document.querySelectorAll('tr[data-kind]').forEach(function (tr) {
    var ok = (!kind || tr.dataset.kind === kind) && (!client || tr.dataset.client === client) && (!q || tr.dataset.q.indexOf(q) >= 0);
    tr.hidden = !ok; if (ok) n++; });
  var none = document.getElementById('none'); if (none) none.style.display = n ? 'none' : 'block';
}
var seg = document.getElementById('seg');
if (seg) seg.addEventListener('click', function (e) { var b = e.target.closest('button'); if (!b) return;
  seg.querySelectorAll('button').forEach(function (x) { x.classList.toggle('is-active', x === b); }); kind = b.dataset.f; apply(); });
var cf = document.getElementById('clientf'); if (cf) cf.addEventListener('change', function () { client = cf.value; apply(); });
if (find) find.addEventListener('input', apply);
"""
    return ui.app_page(base, "documents", "מסמכים · דרור ברק", head + note + f'<div class="stats">{stats}</div>' + body,
                       script=script, css=CSS, spa=True, fill=True)


# ---------------------------------------------------------------- clients


def _status_badge(status: str) -> str:
    label, cls = STATUS_BADGES.get(status, (status or "-", ""))
    return f'<span class="badge {cls} badge-dot">{_esc(label)}</span>'


def clients_page(entries: list[dict[str, Any]], base: str = "", *, dry_run: bool = False) -> str:
    try:
        clients, error = all_clients(dry_run), ""
    except Exception as exc:  # noqa: BLE001
        clients, error = [], f"לא הצלחתי לטעון לקוחות מ-ClickUp: {exc}"
    docs = Counter(d["client_id"] for d in documents_from(entries))
    last: dict[str, str] = {}
    for e in entries:
        cid = str(e.get("client_id") or "")
        if cid:
            last[cid] = max(last.get(cid, ""), str(e.get("ts") or ""))
    answers = _responses_by_client()
    by_status = Counter(c.get("status") for c in clients)
    waiting_q = sum(1 for c in clients if answers.get(str(c["id"]), {}).get("status") not in (None, "answered"))
    stats = (ui.stat("לקוחות פעילים", by_status[crm_fields.STATUS_ACTIVE], ico="users", tone="ok", i=0)
             + ui.stat("לידים", by_status[crm_fields.STATUS_LEAD], ico="user-plus", tone="brand", i=1)
             + ui.stat("הצעות שממתינות לחתימה", sum(1 for c in clients if c.get("sub_status") == crm_fields.SUB_QUOTE_SENT),
                       ico="pen", tone="warn", i=2)
             + ui.stat("שאלונים שממתינים", waiting_q, ico="hourglass", i=3))
    rows = ""
    for c in sorted(clients, key=lambda c: last.get(str(c["id"]), ""), reverse=True):
        cid, name = str(c["id"]), str(c.get("name") or c["id"])
        href = f"{base}/clients/{quote(cid)}"
        r = answers.get(cid)
        q = ('<span class="badge badge-ok">מולא</span>' if r and r.get("status") == "answered"
             else '<span class="badge badge-warn">ממתין</span>' if r else '<span class="subtle">-</span>')
        stage = STAGE_NAMES.get(str(c.get("sub_status") or ""), str(c.get("sub_status") or "-"))
        rows += (f'<tr data-href="{_esc(href)}" data-status="{_esc(c.get("status") or "")}" data-q="{_esc(name.lower())}">'
                 f'<td data-v="{_esc(name)}"><a class="person-link" href="{_esc(href)}"><span class="avatar">{_esc(ui.initials(name))}</span>'
                 f'<span>{_esc(name)}</span></a></td>'
                 f'<td data-v="{_esc(STATUS_BADGES.get(c.get("status"), (c.get("status") or "",))[0])}">{_status_badge(str(c.get("status") or ""))}</td>'
                 f'<td class="hide-sm" data-v="{_RANK.get(str(c.get("sub_status") or ""), -1)}">{_esc(stage)}</td>'
                 f'<td class="hide-sm" data-v="{2 if r and r.get("status") == "answered" else 1 if r else 0}">{q}</td>'
                 f'<td class="hide-sm num" data-v="{docs[cid]}">{docs[cid] or "-"}</td>'
                 f'<td class="muted" data-v="{_ms(last.get(cid))}">{ui.when(last.get(cid), "-")}</td>'
                 f'<td style="width:1%">{ui.icon("chevron-left", 16, cls="row-go")}</td></tr>')
    segs = f'<button class="is-active" data-f="">הכל <span class="count">{len(clients)}</span></button>' + "".join(
        f'<button data-f="{s}">{label} <span class="count">{by_status[s]}</span></button>'
        for s, (label, _cls) in STATUS_BADGES.items() if by_status[s])
    head = ui.page_head("לקוחות", "הלקוחות ב-ClickUp ומה המערכת עשתה איתם. לחיצה על לקוח פותחת את הכרטיס שלו.")
    note = (f'<div class="alert alert-warn" style="margin-bottom:14px">{ui.icon("alert")}<span>{_esc(error)}</span></div>'
            if error else "")
    if rows:
        body = (f"""<div class="toolbar reveal" style="--i:4"><div class="segmented" id="seg">{segs}</div>
          <label class="with-icon grow">{ui.icon("search", 16)}<input class="input" type="search" id="find" data-search
            placeholder="חיפוש לקוח" aria-label="חיפוש"><span class="kbd">/</span></label></div>
          <div class="table-wrap reveal" style="--i:5"><table class="table" data-sortable><thead><tr><th data-sort>לקוח</th>
            <th data-sort>סטטוס</th><th class="hide-sm" data-sort="num">שלב</th><th class="hide-sm" data-sort="num">שאלון</th>
            <th class="hide-sm" data-sort="num" data-first="descending">מסמכים</th>
            <th data-sort="num" data-first="descending" aria-sort="descending">פעילות אחרונה</th><th></th></tr></thead>
            <tbody>{rows}</tbody></table>
            <div id="none" style="display:none">{ui.empty("אין לקוחות שמתאימים", "נסה סינון אחר או חיפוש אחר.", ico="search")}</div></div>""")
    else:
        body = '<div class="card">' + ui.empty("עדיין אין לקוחות", "לקוחות שנפתחים ב-ClickUp יופיעו כאן.", ico="users") + "</div>"
    script = r"""
var status = '', find = document.getElementById('find');
function apply() {
  var q = (find ? find.value : '').trim().toLowerCase(), n = 0;
  document.querySelectorAll('tr[data-status]').forEach(function (tr) {
    var ok = (!status || tr.dataset.status === status) && (!q || tr.dataset.q.indexOf(q) >= 0); tr.hidden = !ok; if (ok) n++; });
  var none = document.getElementById('none'); if (none) none.style.display = n ? 'none' : 'block';
}
var seg = document.getElementById('seg');
if (seg) seg.addEventListener('click', function (e) { var b = e.target.closest('button'); if (!b) return;
  seg.querySelectorAll('button').forEach(function (x) { x.classList.toggle('is-active', x === b); }); status = b.dataset.f; apply(); });
if (find) find.addEventListener('input', apply);
"""
    return ui.app_page(base, "clients", "לקוחות · דרור ברק", head + note + f'<div class="stats">{stats}</div>' + body,
                       script=script, css=CSS, spa=True, fill=True)


# ---------------------------------------------------------------- the card


def _find_client(client_id: str, dry_run: bool) -> Optional[dict[str, Any]]:
    try:
        for c in all_clients(dry_run):
            if str(c["id"]) == client_id:
                return c
    except Exception:  # noqa: BLE001 - fall through to a direct read
        pass
    from .lib.clients.crm import CrmClient

    try:
        return CrmClient(dry_run=dry_run).get_client(client_id)
    except Exception:  # noqa: BLE001 - an unknown id is a 404, not a crash
        return None


def _stepper(client: dict[str, Any], answered: bool) -> str:
    reached = _RANK.get(str(client.get("sub_status") or ""), -1)
    state = []
    for i in range(len(STEPS)):
        done = answered if i == 3 else i <= reached
        state.append("done" if done else "")
    current = next((i for i, s in enumerate(state) if not s), None)
    if current is not None:
        state[current] = "wait" if current == 3 and reached >= 2 else "now"
    items = "".join(
        f'<div class="step {s}"><span class="dot">{ui.icon("check") if s == "done" else i + 1}</span><span>{label}</span></div>'
        for i, (label, s) in enumerate(zip(STEPS, state)))
    return f'<div class="card stepper reveal" style="--i:1" aria-label="איפה הלקוח במשפך">{items}</div>'


def _timeline(entries: list[dict[str, Any]], limit: int = 40) -> str:
    if not entries:
        return ui.empty("עדיין אין פעילות", "כשאוטומציה תרוץ עבור הלקוח, היא תופיע כאן.", ico="activity")
    marks = {"ok": ("check", "ok"), "error": ("x", "err"), "skipped": ("minus", "skip")}
    out, day_seen = [], ""
    today = ui.local_time(datetime.now(timezone.utc).isoformat())[:10]
    for e in sorted(entries, key=lambda e: str(e.get("ts")), reverse=True)[:limit]:
        local = ui.local_time(e.get("ts"))
        day = local[:10]
        if day != day_seen:
            day_seen = day
            out.append(f'<div class="day">{"היום" if day == today else _esc(day)}</div>')
        ico, cls = marks.get(str(e.get("status")), ("info", "skip"))
        detail = str(e.get("detail") or "")
        links = "".join(f' <a href="{_esc(u)}" target="_blank" rel="noopener">{_esc(lbl)} ↗</a>'
                        for lbl, u in subjects.links_for(e))
        out.append(f'<div class="tl-row"><span class="log-dot {cls}">{ui.icon(ico, 12)}</span><div class="grow">'
                   f'<div class="tl-title">{_esc(subjects.label_for(e))}</div>'
                   + (f'<div class="tl-detail"><bdi>{_esc(detail)}</bdi></div>' if detail and not detail.startswith("http") else "")
                   + (f'<div class="tl-detail">{links}</div>' if links else "")
                   + f'</div><span class="tl-time num">{_esc(local[11:])}</span></div>')
    more = (f'<div class="more-note">מוצגות {limit} הפעולות האחרונות מתוך {len(entries)}.</div>'
            if len(entries) > limit else "")
    return "".join(out) + more


def client_page(entries: list[dict[str, Any]], base: str, client_id: str, *, dry_run: bool = False) -> Optional[str]:
    client = _find_client(client_id, dry_run)
    if not client:
        return None
    name = str(client.get("name") or client_id)
    mine = [e for e in entries if str(e.get("client_id") or "") == client_id]
    docs = [d for d in documents_from(entries) if d["client_id"] == client_id]
    r = _responses_by_client().get(client_id)
    answered = bool(r and r.get("status") == "answered")
    status = str(client.get("status") or "")
    stage = STAGE_NAMES.get(str(client.get("sub_status") or ""), "")

    phone, email = str(client.get("phone") or ""), str(client.get("email") or "")
    price = client.get("monthly_price")
    meta = []
    if client.get("service_type"):
        meta.append(f'<span>{ui.icon("sparkles", 14)}{_esc(client["service_type"])}</span>')
    if price not in (None, ""):
        meta.append(f'<span>{ui.icon("calendar", 14)}<span class="num">{_esc(price)} ₪ לחודש</span></span>')
    if email:
        meta.append(f'<span>{ui.icon("mail", 14)}<bdi>{_esc(email)}</bdi></span>')
    if phone:
        meta.append(f'<span>{ui.icon("phone", 14)}<bdi>{_esc(subjects.phone_display(phone))}</bdi></span>')
    actions = []
    if client.get("url"):
        actions.append(f'<a class="btn" href="{_esc(client["url"])}" target="_blank" rel="noopener">{ui.icon("clipboard")}<span>ב-ClickUp</span></a>')
    if client.get("drive_folder_url"):
        actions.append(f'<a class="btn" href="{_esc(client["drive_folder_url"])}" target="_blank" rel="noopener">{ui.icon("folder")}<span>תיקייה ב-Drive</span></a>')
    if phone:
        actions.append(f'<a class="btn btn-icon" href="https://wa.me/{_esc(phone.lstrip("+"))}" target="_blank" rel="noopener" data-tip="וואטסאפ">{ui.icon("message")}</a>')
    if email:
        actions.append(f'<a class="btn btn-icon" href="mailto:{_esc(email)}" data-tip="מייל">{ui.icon("mail")}</a>')
    badges = _status_badge(status) + (f'<span class="badge">{_esc(stage)}</span>' if stage else "")
    hero = (f'<a class="back" href="{base}/clients">{ui.icon("arrow-right", 15)}<span>כל הלקוחות</span></a>'
            f'<div class="hero reveal"><span class="avatar xl">{_esc(ui.initials(name))}</span>'
            f'<div style="min-width:0"><h1>{_esc(name)}</h1><div class="hero-badges">{badges}</div>'
            f'<div class="hero-meta">{"".join(meta)}</div></div>'
            f'<div class="page-actions">{"".join(actions)}</div></div>')

    doc_rows = "".join(
        f'<div class="doc-row">{_doc_head(d, grow=True)}'
        f'<span class="muted small hide-sm">{ui.when(d["ts"], "-")}</span>{_version(d)}{_doc_actions(d)}</div>'
        for d in docs)
    docs_card = (f'<section class="card reveal" style="--i:2"><div class="card-head"><h2 class="card-title">מסמכים</h2>'
                 f'<span class="badge num">{len(docs)}</span></div>'
                 + (f'<div class="cc-list">{doc_rows}</div>' if docs else
                    ui.empty("עדיין אין מסמכים", "אסטרטגיה, דוחות ותשובות לשאלון של הלקוח יופיעו כאן.", ico="file"))
                 + "</section>")
    activity = (f'<section class="card reveal" style="--i:3"><div class="card-head"><h2 class="card-title">פעילות</h2>'
                f'<span class="badge num">{len(mine)}</span></div>{_timeline(mine)}</section>')

    if r:
        qid = str(r.get("questionnaire_id") or "")
        state = ('<span class="badge badge-ok badge-dot">מולא</span>' if answered
                 else '<span class="badge badge-warn badge-dot">ממתין למילוי</span>')
        q_body = (f'<div class="qbox"><div>{state}</div>'
                  f'<div class="small muted">{_esc(r.get("questionnaire_title"))}</div>'
                  f'<div class="small muted">נשלח {ui.when(r.get("sent_at"), "-")}'
                  + (f' · מולא {ui.when(r.get("answered_at"), "-")}' if answered else "") + "</div>"
                  f'<a class="btn btn-sm" href="{base}/admin/responses/{quote(client_id)}/{quote(qid)}">'
                  f'{ui.icon("inbox", 14)}<span>{"לתשובות" if answered else "לפרטים ולקישור חדש"}</span></a></div>')
    else:
        q_body = ui.empty("עדיין לא נשלח שאלון", "השאלון יוצא אוטומטית אחרי חתימה.", ico="clipboard")
    questionnaire = (f'<section class="card reveal" style="--i:2"><div class="card-head"><h2 class="card-title">שאלון</h2></div>'
                     f'{q_body}</section>')

    def fact(label: str, value: Any, *, link: bool = False, missing: str = "") -> str:
        if value in (None, ""):
            return (f'<div><dt>{_esc(label)}</dt><dd><span class="badge badge-warn">{_esc(missing)}</span></dd></div>'
                    if missing else "")
        shown = (f'<a href="{_esc(value)}" target="_blank" rel="noopener">פתיחה ↗</a>' if link
                 else f"<bdi>{_esc(value)}</bdi>")
        return f"<div><dt>{_esc(label)}</dt><dd>{shown}</dd></div>"

    active = status == crm_fields.STATUS_ACTIVE
    facts = (fact("סוג שירות", client.get("service_type"))
             + fact("מחיר חודשי", f"{price} ₪" if price not in (None, "") else "")
             + fact("מחיר אסטרטגיה", f"{client['price_strategy']} ₪" if client.get("price_strategy") not in (None, "") else "")
             + fact("מחיר קמפיינים", f"{client['price_campaigns']} ₪" if client.get("price_campaigns") not in (None, "") else "")
             + fact("מייל", email, missing="חסר" if active else "")
             + fact("טלפון", subjects.phone_display(phone))
             + fact("חשבון מודעות Meta", client.get("meta_ad_account"),
                    missing="חסר: אין דוח חודשי" if active else "")
             + fact("תיקייה ב-Drive", client.get("drive_folder_url"), link=True)
             + fact("הסכם חתום", client.get("signed_contract_url"), link=True)
             + fact("הקלטות", client.get("recordings_path") if str(client.get("recordings_path") or "").startswith("http") else "",
                    link=True))
    details = (f'<section class="card reveal" style="--i:3"><div class="card-head"><h2 class="card-title">פרטים</h2></div>'
               f'<dl class="facts">{facts or "<div><dt>אין עדיין פרטים</dt><dd></dd></div>"}</dl></section>')

    from .contracts_pages import CSS as CONTRACT_CSS, client_card_section

    body = (hero + _stepper(client, answered)
            + f'<div class="cc-grid"><div class="cc-main">{docs_card}{activity}</div>'
              f'<aside class="cc-side">{client_card_section(client, entries, base)}{questionnaire}{details}</aside></div>')
    return ui.app_page(base, "clients", f"{name} · לקוחות", body, script=COPY_JS, css=CSS + CONTRACT_CSS, spa=True)
