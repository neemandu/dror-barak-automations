"""The חוזים screens: where every client's contract stands, and what it will say.

* ``/contracts``: each client's contract (not sent, waiting for a signature, signed,
  or a signature whose filing failed), with the dates and reminders, and Dror's own
  signature, drawn once here and placed in the provider's box of every contract.
* ``/contracts/<id>``: that client's contract exactly as it will go out (their
  prices, Dror's signature, what the client will fill in), with a checklist of what
  is missing before pressing שלח הצעת מחיר in ClickUp; once signed, the document
  exactly as signed, with its audit record and the PDF.

Nothing here sends a contract: sending stays a ClickUp button, Dror's decision.
The one write is Dror's signature (``/admin/api/signature``), content like the
questionnaire editor's. Sources: ClickUp (cached a minute), the run-log (sent,
reminders, signed) and the contract store (signatures and their filing).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

from . import clients_pages, ui
from .lib import contract, contract_store, crm_fields, signing

_esc = ui.esc

#: A contract's state: label, badge class, sort rank.
STATES = {
    "failed": ("התיוק נכשל", "badge-err", 0),
    "filing": ("נחתם, בתיוק", "badge-brand", 1),
    "waiting": ("ממתין לחתימה", "badge-warn", 2),
    "signed": ("נחתם", "badge-ok", 3),
    "outside": ("נחתם מחוץ למערכת", "badge-outline", 4),
    "none": ("טרם נשלח", "", 5),
}
FILING_GRACE_S = 15 * 60  # a filing still running is not yet a failed one

CSS = """
.ct-top { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: 16px; margin-bottom: 18px; }
.sig-area { padding: 4px 18px 18px; display: grid; gap: 12px; }
.sig-show { display: grid; place-items: center; height: 118px; border-radius: 12px; background: #fff;
  border: 1px solid var(--border); }
.sig-show img { max-height: 92px; max-width: 86%; }
.sig-pad { position: relative; height: 150px; border-radius: 12px; background: #fff; border: 1.5px dashed var(--border-strong);
  overflow: hidden; transition: border-color var(--d2); }
.sig-pad.has-ink { border-style: solid; border-color: var(--brand); }
.sig-pad canvas { display: block; width: 100%; height: 100%; touch-action: none; cursor: crosshair; }
.sig-pad .hint { position: absolute; inset: 0; display: grid; place-items: center; color: #98a2b3; font-size: 14px;
  pointer-events: none; transition: opacity var(--d2); }
.sig-pad.has-ink .hint { opacity: 0; }
.sig-pad .line { position: absolute; left: 24px; right: 24px; bottom: 34px; border-top: 1px dashed #d0d5dd; pointer-events: none; }
.sig-acts { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.sig-acts .spacer { flex: 1; }
.checks { list-style: none; margin: 0; padding: 6px 18px 16px; display: grid; gap: 2px; }
.checks li { display: flex; gap: 10px; align-items: flex-start; padding: 9px 0; border-bottom: 1px solid var(--border-soft); font-size: 13.5px; }
.checks li:last-child { border-bottom: 0; }
.checks .mk { width: 22px; height: 22px; border-radius: 50%; flex: none; display: grid; place-items: center; margin-top: 1px; }
.checks .mk svg { width: 12px; height: 12px; stroke-width: 2.8; }
.checks .ok .mk { background: var(--success-soft); color: var(--success-ink); }
.checks .warn .mk { background: var(--warn-soft); color: var(--warn-ink); }
.checks .err .mk { background: var(--danger-soft); color: var(--danger-ink); }
.checks .info .mk { background: var(--surface-active); color: var(--fg-muted); }
.checks b { display: block; font-weight: 600; color: var(--fg); }
.checks span.t { color: var(--fg-muted); }
.ct-grid { display: grid; grid-template-columns: minmax(0, 1fr) 330px; gap: 16px; align-items: start; }
.ct-side { display: grid; gap: 16px; position: sticky; top: calc(var(--sticky-top, 0px) + 16px); }
.paper { background: #fff; color: #1d2939; border-radius: 16px; padding: 44px 52px; border: 1px solid var(--border);
  box-shadow: 0 1px 2px rgba(16, 24, 40, .05), 0 18px 40px -24px rgba(16, 24, 40, .3); }
.paper .ask { background: #fff4d6; color: #93370d; }
.paper-label { display: flex; align-items: center; gap: 8px; margin: 0 0 10px; font-size: 13px; color: var(--fg-muted); }
.small-facts { margin: 0; padding: 4px 18px 14px; }
.small-facts div { display: flex; justify-content: space-between; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--border-soft); font-size: 13px; }
.small-facts div:last-child { border-bottom: 0; }
.small-facts dt { color: var(--fg-muted); }
.small-facts dd { margin: 0; font-weight: 500; text-align: left; overflow-wrap: anywhere; }
.how { padding: 4px 18px 16px; font-size: 13.5px; color: var(--fg-muted); line-height: 1.7; }
.ct-card-body { padding: 14px 18px 16px; display: grid; gap: 10px; }
@media (max-width: 1100px) { .ct-grid { grid-template-columns: minmax(0, 1fr); } .ct-side { position: static; } }
@media (max-width: 900px) { .ct-top { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 640px) { .paper { padding: 24px 18px; border-radius: 14px; } }
"""

# The signature pad: smoothed strokes, cropped to the ink before saving so it
# sits in the contract's box at a readable size.
SIGN_JS = r"""
var box = document.getElementById('sig-card');
if (box) (function () {
  var pad = box.querySelector('#sig-pad'), cv = pad.querySelector('canvas'), ctx = cv.getContext('2d');
  var drawn = false, drawing = false, last = null, mid = null;
  function mode(m) { box.dataset.mode = m; box.querySelectorAll('[data-show]').forEach(function (n) {
    n.hidden = n.getAttribute('data-show').split(' ').indexOf(m) < 0; }); if (m === 'draw') size(); }
  function size() { var r = window.devicePixelRatio || 1; cv.width = cv.clientWidth * r; cv.height = cv.clientHeight * r;
    ctx.setTransform(r, 0, 0, r, 0, 0); ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.strokeStyle = '#101828';
    drawn = false; pad.classList.remove('has-ink'); save.disabled = true; }
  function at(e) { var b = cv.getBoundingClientRect(); return {x: e.clientX - b.left, y: e.clientY - b.top, t: Date.now()}; }
  cv.addEventListener('pointerdown', function (e) { drawing = true; cv.setPointerCapture(e.pointerId); last = at(e); mid = last; e.preventDefault(); });
  cv.addEventListener('pointermove', function (e) {
    if (!drawing) return; var p = at(e), m = {x: (last.x + p.x) / 2, y: (last.y + p.y) / 2};
    var speed = Math.hypot(p.x - last.x, p.y - last.y) / Math.max(1, p.t - last.t);
    ctx.lineWidth = Math.max(1.5, Math.min(3.4, 3.6 - speed * 1.2));
    ctx.beginPath(); ctx.moveTo(mid.x, mid.y); ctx.quadraticCurveTo(last.x, last.y, m.x, m.y); ctx.stroke();
    last = p; mid = m; if (!drawn) { drawn = true; pad.classList.add('has-ink'); save.disabled = false; } e.preventDefault(); });
  ['pointerup', 'pointercancel'].forEach(function (n) { cv.addEventListener(n, function () { drawing = false; }); });
  function trimmed() {
    var w = cv.width, h = cv.height, d = ctx.getImageData(0, 0, w, h).data, x0 = w, y0 = h, x1 = -1, y1 = -1;
    for (var y = 0; y < h; y++) for (var x = 0; x < w; x++) if (d[(y * w + x) * 4 + 3] > 8) {
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
    if (x1 < 0) return null;
    var m = 12 * (window.devicePixelRatio || 1); x0 = Math.max(0, x0 - m); y0 = Math.max(0, y0 - m);
    x1 = Math.min(w, x1 + m); y1 = Math.min(h, y1 + m);
    var out = document.createElement('canvas'); out.width = x1 - x0; out.height = y1 - y0;
    out.getContext('2d').drawImage(cv, x0, y0, out.width, out.height, 0, 0, out.width, out.height);
    return out.toDataURL('image/png');
  }
  var save = box.querySelector('#sig-save');
  box.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-act]'); if (!b) return;
    var act = b.getAttribute('data-act');
    if (act === 'new') mode('draw');
    if (act === 'clear') size();
    if (act === 'cancel') mode(box.querySelector('.sig-show img') ? 'show' : 'empty');
    if (act === 'save') { var png = trimmed(); if (!png) return; UI.busy(b, true);
      UI.api('/signature', {png: png}).then(function (d) { UI.busy(b, false);
        if (d._status !== 200) { UI.toast((d.errors || ['השמירה נכשלה'])[0], {kind: 'error'}); return; }
        box.querySelector('.sig-show').innerHTML = '<img alt="החתימה שלך" src="' + png + '">';
        box.querySelector('#sig-state').className = 'badge badge-ok badge-dot'; box.querySelector('#sig-state').textContent = 'מוגדרת';
        mode('show'); UI.toast('החתימה נשמרה. היא תופיע בכל חוזה שיישלח מעכשיו.'); }); }
    if (act === 'remove') UI.confirm({title: 'להסיר את החתימה?', text: 'חוזים שיישלחו מעכשיו יגיעו עם תיבת חתימה ריקה שלך. חוזים שכבר נחתמו לא משתנים.',
      ok: 'הסרה', danger: true}).then(function (yes) { if (!yes) return;
      UI.api('/signature', {clear: true}).then(function (d) { if (d._status !== 200) { UI.toast('ההסרה נכשלה', {kind: 'error'}); return; }
        box.querySelector('.sig-show').innerHTML = ''; box.querySelector('#sig-state').className = 'badge badge-warn badge-dot';
        box.querySelector('#sig-state').textContent = 'חסרה'; mode('empty'); UI.toast('החתימה הוסרה'); }); });
  });
  mode(box.dataset.mode);
})();
"""


# ------------------------------------------------------------------ data


def _age_s(iso: Any) -> float:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (datetime.now(timezone.utc) - (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))).total_seconds()


def _records() -> dict[str, dict[str, Any]]:
    """Live signatures by client (a superseded one no longer answers for the contract)."""
    try:
        recs = contract_store.list_signed()
    except Exception:  # noqa: BLE001 - the screen still shows the run-log's view
        return {}
    return {str(r["client_id"]): r for r in recs if r.get("status") != contract_store.SUPERSEDED}


def state_for(client: dict[str, Any], entries: list[dict[str, Any]], rec: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Where one client's contract stands, from the signature store, then the run-log, then ClickUp."""
    cid = str(client.get("id") or "")
    mine = [e for e in entries if str(e.get("client_id") or "") == cid]
    sent = max((str(e.get("ts")) for e in mine if e.get("action") == "quote_sent"), default="")
    reminders = sum(1 for e in mine if e.get("action") == "reminder_sent"
                    and e.get("automation") == "sign_reminders" and str(e.get("ts")) > sent) if sent else 0
    logged = max((e for e in mine if e.get("action") == "signed" and e.get("status", "ok") == "ok"),
                 key=lambda e: str(e.get("ts")), default=None)
    out: dict[str, Any] = {"sent_at": sent, "reminders": reminders, "signed_at": "", "link": "", "error": ""}
    if rec:
        out.update(signed_at=(rec.get("audit") or {}).get("signed_at") or rec.get("received_at"),
                   link=rec.get("link") or "", copy_to=rec.get("copy_sent_to") or "", error=rec.get("error") or "")
        if rec.get("status") == contract_store.FILED:
            out["state"] = "signed"
        else:
            out["state"] = "failed" if _age_s(rec.get("received_at")) > FILING_GRACE_S else "filing"
    elif logged and str(logged.get("ts")) >= sent:
        out.update(state="signed", signed_at=logged.get("ts"), link=logged.get("url") or "")
    elif sent:
        out["state"] = "waiting"
    elif client.get("sub_status") in (crm_fields.SUB_SIGNED, crm_fields.SUB_IN_WORK) or client.get("signed_contract_url"):
        out.update(state="outside", link=client.get("signed_contract_url") or "")
    else:
        out["state"] = "none"
    return out


def _badge(state: str) -> str:
    label, cls, _ = STATES[state]
    return f'<span class="badge {cls} badge-dot">{_esc(label)}</span>'


def _days(iso: Any) -> int:
    return int(_age_s(iso) // 86400)


# --------------------------------------------------------------- the list


def _signature_card() -> str:
    rec = contract_store.provider_signature()
    img = (f'<img alt="החתימה שלך" src="data:image/png;base64,{rec["png"]}">' if rec else "")
    state = ('<span class="badge badge-ok badge-dot" id="sig-state">מוגדרת</span>' if rec
             else '<span class="badge badge-warn badge-dot" id="sig-state">חסרה</span>')
    return f"""<section class="card reveal" id="sig-card" data-mode="{"show" if rec else "empty"}" style="--i:1">
  <div class="card-head"><h2 class="card-title">החתימה שלך</h2>{state}</div>
  <div class="sig-area">
    <div class="sig-show" data-show="show">{img}</div>
    <div data-show="empty">{ui.empty("עדיין אין חתימה", "בלי חתימה, התיבה של נותן השירות בחוזה נשארת ריקה.", ico="pen")}</div>
    <div class="sig-pad" id="sig-pad" data-show="draw"><canvas aria-label="תיבת חתימה"></canvas><span class="line"></span>
      <span class="hint">חתמו כאן, עם העכבר או האצבע</span></div>
    <div class="sig-acts">
      <button type="button" class="btn btn-primary" data-act="new" data-show="empty">{ui.icon("pen", 15)}<span>הוספת חתימה</span></button>
      <button type="button" class="btn" data-act="new" data-show="show">{ui.icon("pen", 15)}<span>החלפה</span></button>
      <button type="button" class="btn btn-ghost" data-act="remove" data-show="show">{ui.icon("trash", 15)}<span>הסרה</span></button>
      <button type="button" class="btn btn-primary" id="sig-save" data-act="save" data-show="draw" disabled>{ui.icon("check", 15)}<span>שמירה</span></button>
      <button type="button" class="btn" data-act="clear" data-show="draw">{ui.icon("rotate", 15)}<span>ניקוי</span></button>
      <span class="spacer"></span>
      <button type="button" class="btn btn-ghost" data-act="cancel" data-show="draw">ביטול</button>
    </div>
    <p class="small muted" style="margin:0">החתימה נכנסת לתיבה של נותן השירות בכל חוזה שנשלח מעכשיו. חוזים שכבר נחתמו לא משתנים.</p>
  </div></section>"""


def _setup_card(clients: list[dict[str, Any]], dry_run: bool) -> str:
    missing = contract.missing_provider()
    split = any(c.get("price_strategy") is not None or c.get("price_campaigns") is not None for c in clients)
    if not dry_run:
        try:  # the fields themselves, so a list where no price is filled in yet still counts
            from .lib.clients.crm import CrmClient

            resolved = CrmClient().fields()
            split = "price_strategy" in resolved and "price_campaigns" in resolved
        except Exception:  # noqa: BLE001 - fall back to what the clients carry
            pass
    rows = [
        ("ok", "פרטי נותן השירות ופרטי הבנק", "מוגדרים") if not missing else
        ("err", "חסרים פרטי נותן השירות", "בלעדיהם אי אפשר להפיק חוזה: " + ", ".join(missing)),
        ("ok", "מחיר לכל שירות ב-ClickUp", "מחיר אסטרטגיה ומחיר קמפיינים, כל אחד בשורה משלו") if split else
        ("warn", "ב-ClickUp יש מחיר חודשי אחד",
         "בחוזה הוא מופיע כשורת האסטרטגיה בלבד. כדי לתמחר גם קמפיינים, מוסיפים ברשימת הלקוחות ב-ClickUp "
         "שני שדות מסוג Currency: ״מחיר אסטרטגיה״ ו״מחיר קמפיינים״."),
        ("info", "שליחה", "מהמשימה ב-ClickUp, בכפתור ״שלח הצעת מחיר״. הלקוח מקבל מייל עם קישור אישי, "
                          "ותזכורות אחרי יומיים ואחרי ארבעה ימים."),
    ]
    return (f'<section class="card reveal" style="--i:2"><div class="card-head"><h2 class="card-title">הגדרות החוזה</h2></div>'
            f'{_checks(rows)}</section>')


def _checks(rows: list[tuple[str, str, str]]) -> str:
    marks = {"ok": "check", "warn": "alert", "err": "x", "info": "info"}
    return '<ul class="checks">' + "".join(
        f'<li class="{k}"><span class="mk">{ui.icon(marks[k], 12)}</span><div><b>{_esc(title)}</b>'
        f'<span class="t">{_esc(text)}</span></div></li>' for k, title, text in rows) + "</ul>"


def contracts_page(entries: list[dict[str, Any]], base: str = "", *, dry_run: bool = False) -> str:
    try:
        clients, error = clients_pages.all_clients(dry_run), ""
    except Exception as exc:  # noqa: BLE001
        clients, error = [], f"לא הצלחתי לטעון לקוחות מ-ClickUp: {exc}"
    records = _records()
    states = [(c, state_for(c, entries, records.get(str(c["id"])))) for c in clients]
    count = {k: sum(1 for _c, s in states if s["state"] == k) for k in STATES}
    stats = (ui.stat("ממתינים לחתימה", count["waiting"], ico="hourglass", tone="warn", i=3)
             + ui.stat("נחתמו", count["signed"] + count["outside"], ico="check-circle", tone="ok", i=4)
             + ui.stat("טרם נשלחו", count["none"], ico="send", i=5)
             + (ui.stat("תיוק שנכשל", count["failed"], ico="alert", tone="err", i=6, hot=True) if count["failed"] else ""))
    rows = ""
    for c, s in sorted(states, key=lambda cs: (STATES[cs[1]["state"]][2], -_age_s(cs[1]["sent_at"] or "2000-01-01"))):
        cid, name = str(c["id"]), str(c.get("name") or c["id"])
        href = f"{base}/contracts/{quote(cid)}"
        when_sent = ui.when(s["sent_at"], "-")
        rem = (f'{s["reminders"]} תזכורות' if s["reminders"] > 1 else "תזכורת אחת" if s["reminders"] == 1 else "")
        extra = (f'<div class="small muted">לפני {_days(s["sent_at"])} ימים{" · " + rem if rem else ""}</div>'
                 if s["state"] == "waiting" and s["sent_at"] else "")
        pdf = (f'<a class="btn btn-sm btn-icon" href="{_esc(s["link"])}" target="_blank" rel="noopener" data-tip="ההסכם החתום">'
               f'{ui.icon("file", 15)}</a>' if s["link"] else "")
        rows += (f'<tr data-href="{_esc(href)}" data-state="{s["state"]}" data-q="{_esc(name.lower())}">'
                 f'<td data-v="{_esc(name)}"><a class="person-link" href="{_esc(href)}"><span class="avatar">{_esc(ui.initials(name))}</span>'
                 f'<span>{_esc(name)}</span></a></td>'
                 f'<td data-v="{STATES[s["state"]][2]}">{_badge(s["state"])}{extra}</td>'
                 f'<td class="hide-sm muted" data-v="{clients_pages._ms(s["sent_at"])}">{when_sent}</td>'
                 f'<td class="hide-sm muted" data-v="{clients_pages._ms(s["signed_at"])}">{ui.when(s["signed_at"], "-")}</td>'
                 f'<td style="width:1%"><div class="doc-acts">{pdf}{ui.icon("chevron-left", 16, cls="row-go")}</div></td></tr>')
    segs = f'<button class="is-active" data-f="">הכל <span class="count">{len(states)}</span></button>' + "".join(
        f'<button data-f="{k}">{label} <span class="count">{count[k]}</span></button>'
        for k, (label, _cls, _r) in STATES.items() if count[k])
    head = ui.page_head("חוזים", "ההסכמים של הלקוחות: מה נשלח, מה נחתם ומה עוד ממתין. "
                                 "לחיצה על לקוח מציגה את החוזה שלו בדיוק כפי שיישלח.")
    note = (f'<div class="alert alert-warn" style="margin-bottom:14px">{ui.icon("alert")}<span>{_esc(error)}</span></div>'
            if error else "")
    top = f'<div class="ct-top">{_signature_card()}{_setup_card(clients, dry_run)}</div>'
    if rows:
        table = (f"""<div class="toolbar reveal" style="--i:7"><div class="segmented" id="seg">{segs}</div>
          <label class="with-icon grow">{ui.icon("search", 16)}<input class="input" type="search" id="find" data-search
            placeholder="חיפוש לקוח" aria-label="חיפוש"><span class="kbd">/</span></label></div>
          <div class="table-wrap reveal" style="--i:8"><table class="table" data-sortable><thead><tr><th data-sort>לקוח</th>
            <th data-sort="num">מצב</th><th class="hide-sm" data-sort="num" data-first="descending">נשלח</th>
            <th class="hide-sm" data-sort="num" data-first="descending">נחתם</th><th></th></tr></thead>
            <tbody>{rows}</tbody></table>
            <div id="none" style="display:none">{ui.empty("אין חוזים שמתאימים", "נסה סינון אחר או חיפוש אחר.", ico="search")}</div></div>""")
    else:
        table = '<div class="card">' + ui.empty("עדיין אין לקוחות", "לקוחות שנפתחים ב-ClickUp יופיעו כאן.", ico="users") + "</div>"
    script = SIGN_JS + r"""
var state = '', find = document.getElementById('find');
function apply() {
  var q = (find ? find.value : '').trim().toLowerCase(), n = 0;
  document.querySelectorAll('tr[data-state]').forEach(function (tr) {
    var ok = (!state || tr.dataset.state === state) && (!q || tr.dataset.q.indexOf(q) >= 0); tr.hidden = !ok; if (ok) n++; });
  var none = document.getElementById('none'); if (none) none.style.display = n ? 'none' : 'block';
}
var seg = document.getElementById('seg');
if (seg) seg.addEventListener('click', function (e) { var b = e.target.closest('button'); if (!b) return;
  seg.querySelectorAll('button').forEach(function (x) { x.classList.toggle('is-active', x === b); }); state = b.dataset.f; apply(); });
if (find) find.addEventListener('input', apply);
"""
    return ui.app_page(base, "contracts", "חוזים · דרור ברק",
                       head + note + top + f'<div class="stats">{stats}</div>' + table,
                       script=script, css=clients_pages.CSS + CSS, spa=True)


# ------------------------------------------------------------ one contract


def _preview_body(client: dict[str, Any]) -> tuple[str, str]:
    """The contract as it would go out now, and an error if it cannot be produced."""
    from .sign_page import ASK_CLIENT

    try:
        fields = contract.fields_from_client(client)
    except contract.ContractError as exc:
        return "", f"המחיר ב-ClickUp אינו מספר: {exc}"
    shown = dict(fields)
    for key, _label, _kind in ASK_CLIENT:
        if not shown.get(key):
            shown[key] = "ימולא על ידי הלקוח"
    try:
        body = contract.render(shown, signatures={
            "provider_signature": contract_store.provider_signature_img(), "client_signature": ""})
    except contract.ContractError:
        return "", "חסרים פרטי נותן השירות: " + ", ".join(contract.missing_provider())
    return body.replace(">ימולא על ידי הלקוח<", ' data-ask="1">ימולא על ידי הלקוח<'), ""


def preflight(client: dict[str, Any]) -> list[tuple[str, str, str]]:
    """What is missing before this client's contract can go out, in Dror's words."""
    rows: list[tuple[str, str, str]] = []
    try:
        p = contract.prices(client)
    except contract.ContractError:
        p = {"total": 0, "split": False}
    if p["total"] <= 0:
        rows.append(("err", "אין מחיר", "בלי מחיר ב-ClickUp, ״שלח הצעת מחיר״ לא ישלח את החוזה."))
    elif p["split"]:
        parts = [f'אסטרטגיה {p["strategy"]:,.0f} ₪' if p["has_strategy"] else "",
                 f'קמפיינים {p["campaigns"]:,.0f} ₪' if p["has_campaigns"] else ""]
        rows.append(("ok", "מחיר", " · ".join(x for x in parts if x) + f' (לפני מע״מ, לחודש)'))
    else:
        rows.append(("warn", f'מחיר אחד: {p["total"]:,.0f} ₪',
                     "בחוזה הוא שורת האסטרטגיה, בלי קמפיינים. לתמחור נפרד: ״מחיר אסטרטגיה״ ו״מחיר קמפיינים״ ב-ClickUp."))
    email = str(client.get("email") or "").strip()
    rows.append(("ok", "מייל", f"הקישור יישלח ל-{email}") if email else
                ("warn", "אין מייל ב-ClickUp", "הקישור לחתימה יתווסף כתגובה במשימה, ואת השליחה ללקוח עושים ידנית."))
    rows.append(("ok", "החתימה שלך", "מופיעה בתיבה של נותן השירות") if contract_store.provider_signature() else
                ("warn", "אין חתימה שלך", "התיבה של נותן השירות תישאר ריקה. מוסיפים אותה במסך החוזים."))
    missing = contract.missing_provider()
    if missing:
        rows.append(("err", "חסרים פרטי נותן השירות", ", ".join(missing)))
    rows.append(("info", "הלקוח ישלים בעצמו", "ת.ז / ח.פ וכתובת, בדף החתימה"
                 + ("" if email and client.get("phone") else ", וגם מייל וטלפון")))
    return rows


def contract_page(entries: list[dict[str, Any]], base: str, client_id: str, *, dry_run: bool = False) -> Optional[str]:
    client = clients_pages._find_client(client_id, dry_run)
    if not client:
        return None
    name = str(client.get("name") or client_id)
    rec = _records().get(client_id)
    s = state_for(client, entries, rec)
    back = f'<a class="back" href="{base}/contracts">{ui.icon("arrow-right", 15)}<span>כל החוזים</span></a>'
    actions = (f'<a class="btn" href="{base}/clients/{quote(client_id)}">{ui.icon("users")}<span>כרטיס לקוח</span></a>'
               + (f'<a class="btn" href="{_esc(client["url"])}" target="_blank" rel="noopener">{ui.icon("clipboard")}<span>ב-ClickUp</span></a>'
                  if client.get("url") else ""))
    head = ui.page_head(f"החוזה של {name}", "", actions, extra=f" {_badge(s['state'])}")

    if rec:  # the document exactly as signed
        audit = rec.get("audit") or {}
        body, label = str(rec.get("body") or ""), "ההסכם כפי שנחתם"
        facts = [("נחתם", signing.local_time(audit.get("signed_at", ""))), ("כתובת IP", audit.get("ip") or "לא נרשמה"),
                 ("טביעת אצבע", str(audit.get("contract_sha256", ""))[:16] + "…"),
                 ("עותק ללקוח", rec.get("copy_sent_to") or ("טרם נשלח" if rec.get("status") != contract_store.FILED else "לא נשלח (אין מייל)"))]
        facts_html = "".join(f"<div><dt>{_esc(k)}</dt><dd><bdi>{_esc(v)}</bdi></dd></div>" for k, v in facts)
        pdf = (f'<a class="btn btn-primary" href="{_esc(s["link"])}" target="_blank" rel="noopener">{ui.icon("file", 15)}'
               '<span>ה-PDF החתום</span></a>' if s["link"] else "")
        err = (f'<div class="alert alert-danger">{ui.icon("alert")}<span>התיוק נכשל: {_esc(s["error"] or "לא הסתיים")}. '
               'החתימה שמורה, והמערכת תנסה שוב אוטומטית מחר בבוקר.</span></div>' if s["state"] == "failed" else
               f'<div class="alert alert-info">{ui.icon("clock")}<span>החתימה התקבלה וה-PDF בהכנה. זה לוקח עד דקה.</span></div>'
               if s["state"] == "filing" else "")
        side = (f'<section class="card reveal" style="--i:2"><div class="card-head"><h2 class="card-title">החתימה</h2></div>'
                f'<div class="ct-card-body">{err}<dl class="small-facts" style="padding:0">{facts_html}</dl>{pdf}</div></section>')
    else:
        body, problem = _preview_body(client)
        label = "תצוגה מקדימה: כך הלקוח יראה את החוזה"
        status = ""
        if s["state"] == "waiting":
            rem = f', {s["reminders"]} תזכורות' if s["reminders"] else ""
            status = (f'<div class="alert alert-warn" style="margin:0 18px 12px">{ui.icon("hourglass")}<span>נשלח {_esc(signing.local_time(s["sent_at"]))}'
                      f'{rem}. ממתין לחתימה.</span></div>')
        side = (f'<section class="card reveal" style="--i:2"><div class="card-head"><h2 class="card-title">לפני השליחה</h2></div>'
                f'{status}{_checks(preflight(client))}'
                f'<div class="how">שולחים מהמשימה ב-ClickUp, בכפתור ״שלח הצעת מחיר״. אם משנים שם מחיר או מייל, '
                f'החוזה כאן מתעדכן תוך דקה.</div></section>')
        if problem:
            body = ""
            label = ""
            side = (f'<div class="alert alert-danger reveal">{ui.icon("alert")}<span>{_esc(problem)}</span></div>' + side)
    paper = (f'<div class="reveal" style="--i:1"><p class="paper-label">{ui.icon("eye", 14)}<span>{_esc(label)}</span></p>'
             f'<article class="paper">{body}</article></div>' if body else
             f'<div class="card">{ui.empty("אי אפשר להציג את החוזה", "יש לתקן את מה שמופיע בצד.", ico="file")}</div>')
    page = (back + head + f'<div class="ct-grid"><div>{paper}</div><aside class="ct-side">{side}</aside></div>')
    css = clients_pages.CSS + CSS + contract.CONTRACT_CSS + ".paper [data-ask] { background: #eef4ff; color: #3538cd; font-weight: 500; }"
    return ui.app_page(base, "contracts", f"החוזה של {name} · חוזים", page, css=css, spa=True)


# ------------------------------------------------------- on the client card


def client_card_section(client: dict[str, Any], entries: list[dict[str, Any]], base: str) -> str:
    cid = str(client.get("id") or "")
    s = state_for(client, entries, _records().get(cid))
    lines = []
    if s["sent_at"]:
        lines.append(f'נשלח {ui.when(s["sent_at"], "-")}' + (f' · {s["reminders"]} תזכורות' if s["reminders"] else ""))
    if s["signed_at"]:
        lines.append(f'נחתם {ui.when(s["signed_at"], "-")}')
    pdf = (f'<a class="btn btn-sm" href="{_esc(s["link"])}" target="_blank" rel="noopener">{ui.icon("file", 14)}<span>PDF</span></a>'
           if s["link"] else "")
    return (f'<section class="card reveal" style="--i:2"><div class="card-head"><h2 class="card-title">חוזה</h2></div>'
            f'<div class="qbox"><div>{_badge(s["state"])}</div>'
            + "".join(f'<div class="small muted">{x}</div>' for x in lines)
            + f'<div class="sig-acts"><a class="btn btn-sm" href="{base}/contracts/{quote(cid)}">{ui.icon("eye", 14)}'
              f'<span>{"לחוזה החתום" if s["state"] in ("signed", "filing", "failed") else "תצוגה מקדימה"}</span></a>{pdf}</div>'
              "</div></section>")


# ------------------------------------------------------------- the one write


def save_signature(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """``{"png": data-url}`` sets Dror's signature, ``{"clear": true}`` removes it."""
    if body.get("clear"):
        contract_store.clear_provider_signature()
        return 200, {"ok": True}
    try:
        png = signing.decode_signature(str(body.get("png") or ""))
    except signing.SigningError:
        return 422, {"errors": ["החתימה ריקה או לא נקלטה. אפשר לנסות שוב."]}
    contract_store.set_provider_signature(png)
    return 200, {"ok": True}
