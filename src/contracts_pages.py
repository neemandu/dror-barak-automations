"""The חוזים screens: where every client's contract stands, and what it will say.

* ``/contracts``: each client's contract (not sent, waiting for a signature, signed,
  or a signature whose filing failed), with the dates and reminders, and Dror's own
  signature, drawn once here and placed in the provider's box of every contract.
* ``/contracts/<id>``: that client's contract exactly as it will go out (their
  prices, Dror's signature, what the client will fill in), with a checklist of what
  is missing before pressing שלח חוזה ללקוח במייל in ClickUp; once signed, the document
  exactly as signed, with its audit record and the PDF.

Each row of the list carries the actions its state allows (send or a WhatsApp link;
resend or copy the waiting contract's link, which is not a send; the PDF or a new
contract; ClickUp), on hover on a desktop and behind a "⋯" menu on a touch screen.

Sending: the contract page has the same "send" the ClickUp button has (it runs
:func:`src.automations.send_quote.send`), after a confirmation that names who it
goes to and at what price, and a second, louder one for a client who already
signed; or it makes the link without emailing it, for Dror to send himself. Dror
decided to have it here (2026-09-24); nothing else on the dashboard runs an
automation. The other write is Dror's signature (``/admin/api/signature``). Sources: ClickUp (cached a minute), the run-log (sent,
reminders, signed) and the contract store (signatures and their filing).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

from . import clients_pages, ui
from .lib import contract, contract_edit, contract_store, crm_fields, signing

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
#: States in which a client has signed something: a new send is a new contract.
SIGNED_STATES = ("signed", "outside", "filing", "failed")

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
/* row actions: on a desktop they take the date's place on hover (the row does not
   widen or jump); on a phone or a touch screen, the "⋯" opens them as a menu */
.acts-cell { position: relative; min-width: 124px; }
.acts-cell .when-v { transition: opacity var(--d2); }
.row-acts { position: absolute; inset-inline-start: 4px; top: 50%; display: flex; gap: 2px; opacity: 0; pointer-events: none;
  transform: translate(0, -50%) scale(.96); transition: opacity var(--d2) var(--ease), transform var(--d2) var(--ease); }
.row-acts .btn { width: 32px; }
.btn.is-off, .menu-item.is-off { opacity: .42; cursor: not-allowed; }
.go-cell { width: 1%; white-space: nowrap; }
.row-more { display: none; }
@media (hover: hover) and (min-width: 721px) {
  .table tr:hover .row-acts, .table tr:focus-within .row-acts { opacity: 1; pointer-events: auto; transform: translate(0, -50%); }
  .table tr:hover .acts-cell .when-v, .table tr:focus-within .acts-cell .when-v { opacity: 0; }
}
@media (hover: none), (max-width: 720px) { .row-more { display: inline-flex; } .go-cell .row-go { display: none; } }
.act-shade { position: fixed; inset: 0; z-index: 90; }
.table tr[data-href] { -webkit-tap-highlight-color: transparent; }
.act-menu { z-index: 91; animation: menu-in var(--d2) var(--ease); }
.send-card .btn-block { width: 100%; justify-content: center; }
.send-card .why-not { font-size: 13px; color: var(--warn-ink); display: flex; gap: 6px; align-items: flex-start; }
.send-card .why-not .icon { margin-top: 2px; flex: none; }
.linkbox { display: grid; gap: 10px; }
.linkbox .copyrow { display: flex; gap: 8px; }
.linkbox .input { font-family: var(--mono); font-size: 12.5px; direction: ltr; text-align: left; min-width: 0; }
.linkbox .acts { display: flex; gap: 8px; flex-wrap: wrap; }
.linkbox-ok { display: flex; align-items: center; gap: 8px; color: var(--success-ink); font-weight: 600; font-size: 14px; }
/* one column: what to do (send, what is missing) before the contract, not after nine pages of it */
@media (max-width: 1100px) { .ct-grid { grid-template-columns: minmax(0, 1fr); } .ct-side { position: static; order: -1; } }
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


# Sending, shared by the contract page and the list's row actions: the confirmation
# (who, which address, what price; louder for a client who signed), the send, and a
# window with the link (copy, open, WhatsApp to the client's number).
CT_JS = r"""
window.CT = {
  send: function (d, mode, btn, onLink) {
    var signed = d.signed === '1';
    var what = mode === 'email'
      ? 'יישלח מייל ל-' + d.email + ' עם קישור אישי לחתימה על החוזה (' + d.price + ').'
      : 'ייווצר קישור אישי לחתימה על החוזה (' + d.price + '), ואותו שולחים ידנית, למשל בוואטסאפ.';
    var text = (signed ? 'הלקוח כבר חתם על חוזה. זה יפתח חוזה חדש לחתימה, והחוזה החתום הקודם נשאר ב-Drive. ' : '') + what +
      ' הסטטוס ב-ClickUp יעבור ל״נשלחה הצעת מחיר״, ואם הלקוח לא יחתום תישלח תזכורת אחת במייל אחרי 3 ימים' +
      ' (את הנוסח והתאריך אפשר לשנות במשימה ״תזכורת חתימה״ ב-ClickUp).';
    UI.confirm({title: (mode === 'email' ? 'לשלוח את החוזה ל' : 'ליצור קישור לחתימה עבור ') + d.name + '?', text: text,
                ok: mode === 'email' ? 'שליחה' : 'יצירת קישור', icon: 'send', danger: signed}).then(function (yes) {
      if (!yes) return; UI.busy(btn, true);
      UI.api('/contracts/' + encodeURIComponent(d.client) + '/send', {mode: mode, new_contract: signed}).then(function (r) {
        UI.busy(btn, false);
        if (r._status !== 200) { UI.toast((r.errors || ['השליחה נכשלה'])[0], {kind: 'error'}); return; }
        if (mode === 'email' && r.delivered) {
          UI.toast('החוזה נשלח ל-' + r.to); setTimeout(UI.reload, 900); return; }
        if (mode === 'email') UI.toast('המייל לא יצא. הקישור מוכן, אפשר לשלוח אותו ידנית.', {kind: 'error'});
        (onLink || CT.linkWindow)(r.url, d); UI.forgetPages();
      });
    });
  },
  // The link to a contract already waiting: no new send, no status change, no reset reminders.
  copy: function (d, btn) {
    UI.busy(btn, true);
    UI.api('/contracts/' + encodeURIComponent(d.client) + '/link', {}).then(function (r) {
      UI.busy(btn, false);
      if (r._status !== 200) { UI.toast((r.errors || ['לא הצלחתי ליצור קישור'])[0], {kind: 'error'}); return; }
      CT.linkWindow(r.url, d);
    });
  },
  fill: function (box, url, d) {
    box.hidden = false; box.innerHTML =
      '<div class="linkbox-ok">' + UI.icon('check-circle') + '<span>הקישור לחתימה מוכן</span></div>' +
      '<div class="copyrow"><input class="input" readonly aria-label="קישור"><button type="button" class="btn" data-link="copy">' +
      UI.icon('copy') + '<span>העתקה</span></button></div>' +
      '<div class="acts"><a class="btn btn-sm" target="_blank" rel="noopener" data-link="open">' + UI.icon('external') +
      '<span>פתיחה</span></a><a class="btn btn-sm" target="_blank" rel="noopener" data-link="wa">' + UI.icon('message') +
      '<span>שליחה בוואטסאפ</span></a></div>';
    var input = box.querySelector('input'); input.value = url;
    input.addEventListener('focus', function () { input.select(); });
    box.querySelector('[data-link=copy]').onclick = function () { UI.copy(url, this); };
    box.querySelector('[data-link=open]').href = url;
    var text = (d.first ? 'היי ' + d.first + ', ' : '') + 'ההסכם מוכן לחתימה דיגיטלית: ' + url;
    box.querySelector('[data-link=wa]').href = 'https://wa.me/' + (d.phone || '') + '?text=' + encodeURIComponent(text);
    box.classList.remove('is-new'); void box.offsetWidth; box.classList.add('is-new');
  },
  linkWindow: function (url, d) {
    var dlg = document.createElement('dialog'); dlg.className = 'modal';
    dlg.innerHTML = '<form method="dialog"><div class="modal-body"><div class="modal-icon">' + UI.icon('link') + '</div>' +
      '<h2 class="modal-title"></h2><div class="linkbox" style="margin-top:14px"></div></div>' +
      '<div class="modal-foot"><button value="cancel" class="btn">סגירה</button></div></form>';
    dlg.querySelector('.modal-title').textContent = 'הקישור לחתימה של ' + d.name;
    CT.fill(dlg.querySelector('.linkbox'), url, d);
    document.body.appendChild(dlg);
    dlg.addEventListener('close', function () { setTimeout(function () { dlg.remove(); }, 50); });
    dlg.showModal();
  }
};
"""

# The contract page: its send card, the link shown in the card itself.
PAGE_JS = r"""
var card = document.getElementById('send-card');
if (card) card.addEventListener('click', function (e) {
  var b = e.target.closest('button[data-mode]'); if (!b || b.disabled) return;
  CT.send(card.dataset, b.dataset.mode, b, function (url, d) { CT.fill(document.getElementById('linkbox'), url, d); });
});
"""

# The list: row actions (hover on a desktop, the "⋯" menu on a phone or a touch screen).
LIST_JS = r"""
var main = document.querySelector('main.page');
function rowAct(b, d) {
  var act = b.getAttribute('data-act');
  if (b.classList.contains('is-off')) { UI.toast(b.getAttribute('data-tip'), {kind: 'error'}); return; }
  if (act === 'email' || act === 'link') CT.send(d, act, b);
  if (act === 'copy') CT.copy(d, b);
}
function openMore(btn) {
  var row = btn.closest('tr'), acts = row.querySelector('.row-acts'), r = btn.getBoundingClientRect();
  var shade = document.createElement('div'); shade.className = 'act-shade';
  var list = document.createElement('div'); list.className = 'menu-list act-menu'; list.setAttribute('role', 'menu');
  acts.querySelectorAll('[data-act]').forEach(function (a) {
    var item = document.createElement(a.tagName === 'A' ? 'a' : 'button');
    item.className = 'menu-item' + (a.classList.contains('is-off') ? ' is-off' : ''); item.setAttribute('role', 'menuitem');
    if (a.tagName === 'A') { item.href = a.href; if (a.target) { item.target = a.target; item.rel = 'noopener'; } }
    else item.type = 'button';
    item.innerHTML = a.innerHTML + '<span></span>'; item.lastChild.textContent = a.getAttribute('data-label');
    item.addEventListener('click', function (e) { close();
      if (a.tagName !== 'A') { e.preventDefault(); rowAct(a, row.dataset); } });
    list.appendChild(item);
  });
  function close() { shade.remove(); list.remove(); window.removeEventListener('scroll', close); }
  shade.addEventListener('click', close);
  window.addEventListener('scroll', close, {passive: true});  // removed again on close
  document.body.appendChild(shade); document.body.appendChild(list);
  // placed by hand (the menu's own rule anchors it to a parent, which a table would clip)
  list.style.inset = 'auto'; list.style.position = 'fixed';
  var top = r.bottom + 6; if (top + list.offsetHeight > innerHeight - 8) top = Math.max(8, r.top - list.offsetHeight - 6);
  var left = Math.min(Math.max(8, r.left), innerWidth - list.offsetWidth - 8);
  list.style.top = top + 'px'; list.style.left = left + 'px';
  var first = list.querySelector('.menu-item'); if (first) first.focus();
  list.addEventListener('keydown', function (e) { if (e.key === 'Escape') { close(); btn.focus(); } });
}
if (main) main.addEventListener('click', function (e) {
  var b = e.target.closest('[data-act]'); if (!b || !main.contains(b)) return;
  if (b.getAttribute('data-act') === 'more') { e.preventDefault(); openMore(b); return; }
  if (b.tagName === 'A') return;
  e.preventDefault(); rowAct(b, b.closest('tr').dataset);
});
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


def _blocker(client: dict[str, Any], missing_provider: list[str]) -> str:
    """Why this client's contract cannot go out at all ("" when it can)."""
    try:
        priced = contract.prices(client)["total"] > 0
    except contract.ContractError:
        priced = False
    if not priced:
        return "אי אפשר לשלוח: אין מחיר ב-ClickUp"
    return "אי אפשר לשלוח: חסרים פרטי נותן השירות" if missing_provider else ""


def _send_data(client: dict[str, Any], s: dict[str, Any]) -> str:
    """What the confirmation says (who, where, what price), as data- attributes."""
    cid = str(client.get("id") or "")
    name = str(client.get("name") or cid)
    phone = "".join(ch for ch in str(client.get("phone") or "") if ch.isdigit())
    phone = "972" + phone[1:] if phone.startswith("0") else phone
    first = str(client.get("first_name") or name.split(" ")[0])
    return (f'data-client="{_esc(cid)}" data-name="{_esc(name)}" data-first="{_esc(first)}" '
            f'data-email="{_esc(str(client.get("email") or "").strip())}" data-phone="{_esc(phone)}" '
            f'data-price="{_esc(_price_line(client))}" data-signed="{"1" if s["state"] in SIGNED_STATES else ""}"')


def _row_actions(client: dict[str, Any], s: dict[str, Any], base: str, missing_provider: list[str]) -> str:
    """The row's actions, by where its contract stands."""
    cid = str(client.get("id") or "")
    page = f"{base}/contracts/{quote(cid)}"

    def btn(act: str, icon: str, label: str, off: str = "") -> str:
        state = ' aria-disabled="true"' if off else ""
        return (f'<button type="button" class="btn btn-ghost btn-sm btn-icon{" is-off" if off else ""}" data-act="{act}" '
                f'data-label="{_esc(label)}" data-tip="{_esc(off or label)}" aria-label="{_esc(label)}"{state}>'
                f'{ui.icon(icon, 15)}</button>')

    def link(href: str, icon: str, label: str, *, out: bool = False) -> str:
        target = ' target="_blank" rel="noopener"' if out else ""
        return (f'<a class="btn btn-ghost btn-sm btn-icon" href="{_esc(href)}"{target} data-act="go" '
                f'data-label="{_esc(label)}" data-tip="{_esc(label)}" aria-label="{_esc(label)}">{ui.icon(icon, 15)}</a>')

    block = _blocker(client, missing_provider)
    no_mail = "" if str(client.get("email") or "").strip() else "אין מייל ב-ClickUp. אפשר ליצור קישור ולשלוח ידנית."
    clickup = link(client["url"], "external", "עריכה ב-ClickUp", out=True) if client.get("url") else ""
    state = s["state"]
    if state == "none":
        return (btn("email", "send", "שליחה במייל", block or no_mail)
                + btn("link", "message", "קישור לשליחה בוואטסאפ", block) + clickup)
    if state == "waiting":
        return (btn("email", "send", "שליחה חוזרת במייל", block or no_mail)
                + btn("copy", "copy", "העתקת הקישור לחוזה") + clickup)
    if state in ("signed", "outside"):
        pdf = link(s["link"], "file", "ההסכם החתום", out=True) if s["link"] else ""
        return pdf + link(f"{page}?new=1", "plus", "חוזה חדש") + clickup
    note = "פתיחה (התיוק נכשל, המערכת תנסה שוב בבוקר)" if state == "failed" else "פתיחה"
    return link(page, "eye", note) + clickup


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
        ("info", "שליחה", "מהמשימה ב-ClickUp, בכפתור ״שלח חוזה ללקוח במייל״. הלקוח מקבל מייל עם קישור אישי, "
                          "ותזכורת אחת אחרי 3 ימים אם לא חתם."),
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
    rows, missing = "", contract.missing_provider()
    for c, s in sorted(states, key=lambda cs: (STATES[cs[1]["state"]][2], -_age_s(cs[1]["sent_at"] or "2000-01-01"))):
        cid, name = str(c["id"]), str(c.get("name") or c["id"])
        href = f"{base}/contracts/{quote(cid)}"
        when_sent = ui.when(s["sent_at"], "-")
        rem = (f'{s["reminders"]} תזכורות' if s["reminders"] > 1 else "תזכורת אחת" if s["reminders"] == 1 else "")
        extra = (f'<div class="small muted">לפני {_days(s["sent_at"])} ימים{" · " + rem if rem else ""}</div>'
                 if s["state"] == "waiting" and s["sent_at"] else "")
        rows += (f'<tr data-href="{_esc(href)}" data-state="{s["state"]}" data-q="{_esc(name.lower())}" {_send_data(c, s)}>'
                 f'<td data-v="{_esc(name)}"><a class="person-link" href="{_esc(href)}"><span class="avatar">{_esc(ui.initials(name))}</span>'
                 f'<span>{_esc(name)}</span></a></td>'
                 f'<td data-v="{STATES[s["state"]][2]}">{_badge(s["state"])}{extra}</td>'
                 f'<td class="hide-sm muted" data-v="{clients_pages._ms(s["sent_at"])}">{when_sent}</td>'
                 f'<td class="hide-sm muted acts-cell" data-v="{clients_pages._ms(s["signed_at"])}">'
                 f'<span class="when-v">{ui.when(s["signed_at"], "-")}</span>'
                 f'<div class="row-acts">{_row_actions(c, s, base, missing)}</div></td>'
                 f'<td class="go-cell"><button type="button" class="btn btn-ghost btn-sm btn-icon row-more" data-act="more" '
                 f'aria-label="פעולות" aria-haspopup="menu">{ui.icon("more", 16)}</button>'
                 f'{ui.icon("chevron-left", 16, cls="row-go")}</td></tr>')
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
    script = SIGN_JS + CT_JS + LIST_JS + r"""
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


def _preview_body(client: dict[str, Any], template: Optional[str] = None) -> tuple[str, str]:
    """The contract as it would go out now, and an error if it cannot be produced.
    ``template``: the client's own version, its changed blocks marked (preview only)."""
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
        body = contract.render(shown, template=template, signatures={
            "provider_signature": contract_store.provider_signature_img(), "client_signature": ""})
    except contract.ContractError as exc:
        missing = contract.missing_provider()
        return "", ("חסרים פרטי נותן השירות: " + ", ".join(missing)) if missing else str(exc)
    return body.replace(">ימולא על ידי הלקוח<", ' data-ask="1">ימולא על ידי הלקוח<'), ""


def preflight(client: dict[str, Any]) -> list[tuple[str, str, str]]:
    """What is missing before this client's contract can go out, in Dror's words."""
    rows: list[tuple[str, str, str]] = []
    try:
        p = contract.prices(client)
    except contract.ContractError:
        p = {"total": 0, "split": False}
    if p["total"] <= 0:
        rows.append(("err", "אין מחיר", "בלי מחיר ב-ClickUp, ״שלח חוזה ללקוח במייל״ לא ישלח את החוזה."))
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


def _price_line(client: dict[str, Any]) -> str:
    try:
        p = contract.prices(client)
    except contract.ContractError:
        return ""
    parts = ([f'אסטרטגיה {p["strategy"]:,.0f} ₪'] if p["has_strategy"] and p["strategy"] > 0 else []) + \
            ([f'קמפיינים {p["campaigns"]:,.0f} ₪'] if p["has_campaigns"] else [])
    return " + ".join(parts) + " לחודש, לפני מע״מ"


def _send_card(client: dict[str, Any], s: dict[str, Any], *, again: bool) -> str:
    """Send the contract (the ClickUp button's action), or make the link to send by hand."""
    cid = str(client.get("id") or "")
    name = str(client.get("name") or cid)
    email = str(client.get("email") or "").strip()
    phone = "".join(ch for ch in str(client.get("phone") or "") if ch.isdigit())
    if phone.startswith("0"):
        phone = "972" + phone[1:]
    blockers = [t for k, t, _x in preflight(client) if k == "err"]
    why = ""
    if blockers:
        why = "אי אפשר לשלוח: " + ", ".join(blockers) + "."
    elif not email:
        why = "אין מייל ב-ClickUp, אז אפשר רק ליצור קישור ולשלוח אותו ידנית."
    label = "שליחה חוזרת במייל" if s["state"] == "waiting" and not again else "שליחה במייל"
    first = str(client.get("first_name") or name.split(" ")[0])
    return (f'<section class="card send-card reveal" id="send-card" style="--i:2" data-client="{_esc(cid)}" '
            f'data-name="{_esc(name)}" data-first="{_esc(first)}" data-email="{_esc(email)}" data-phone="{_esc(phone)}" '
            f'data-price="{_esc(_price_line(client))}" data-signed="{"1" if again else ""}">'
            f'<div class="card-head"><h2 class="card-title">{"חוזה חדש ללקוח" if again else "שליחה ללקוח"}</h2></div>'
            f'<div class="ct-card-body">'
            + (f'<div class="why-not">{ui.icon("alert", 14)}<span>{_esc(why)}</span></div>' if why else "")
            + f'<button type="button" class="btn btn-primary btn-block" data-mode="email"'
              f'{" disabled" if blockers or not email else ""}>{ui.icon("send", 15)}<span>{label}</span></button>'
            + f'<button type="button" class="btn btn-block" data-mode="link"{" disabled" if blockers else ""}>'
              f'{ui.icon("link", 15)}<span>יצירת קישור לשליחה ידנית</span></button>'
            + '<p class="small muted" style="margin:0">כמו הכפתור ״שלח חוזה ללקוח במייל״ ב-ClickUp: הסטטוס עובר ל״נשלחה הצעת '
              'מחיר״, ולקוח שלא חותם מקבל תזכורת אחת אחרי 3 ימים.</p>'
            + '<div class="linkbox" id="linkbox" hidden></div></div></section>')


def contract_page(entries: list[dict[str, Any]], base: str, client_id: str, *, dry_run: bool = False,
                  new: bool = False, edit: bool = False) -> Optional[str]:
    client = clients_pages._find_client(client_id, dry_run)
    if not client:
        return None
    if edit:
        return editor_page(client, base)
    name = str(client.get("name") or client_id)
    signed_rec = _records().get(client_id)
    rec = None if new else signed_rec
    s = state_for(client, entries, signed_rec)
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
        again = (f'<a class="btn" href="{base}/contracts/{quote(client_id)}?new=1">{ui.icon("send", 15)}'
                 '<span>שליחת חוזה חדש</span></a>' if s["state"] == "signed" else "")
        side = (f'<section class="card reveal" style="--i:2"><div class="card-head"><h2 class="card-title">החתימה</h2></div>'
                f'<div class="ct-card-body">{err}<dl class="small-facts" style="padding:0">{facts_html}</dl>'
                f'<div class="sig-acts">{pdf}{again}</div></div></section>')
    else:
        version = contract_store.draft(client_id)
        shown = (contract_edit.marked(version["template"], contract_edit.compare(version["template"])["marks"])
                 if version else None)
        body, problem = _preview_body(client, shown)
        label = ("תצוגה מקדימה של הנוסח שערכת ללקוח (השינויים מסומנים)" if version
                 else "תצוגה מקדימה: כך הלקוח יראה את החוזה")
        status = ""
        if s["state"] == "waiting":
            rem = (f', {s["reminders"]} תזכורות' if s["reminders"] > 1 else ", נשלחה תזכורת" if s["reminders"] else "")
            status = (f'<div class="alert alert-warn" style="margin:0 18px 12px">{ui.icon("hourglass")}<span>נשלח {_esc(signing.local_time(s["sent_at"]))}'
                      f'{rem}. ממתין לחתימה.</span></div>')
        already = s["state"] in SIGNED_STATES
        if already:
            when = signing.local_time(s["signed_at"]) if s["signed_at"] else ""
            to_signed = (f' <a href="{base}/contracts/{quote(client_id)}">לחוזה החתום</a>' if signed_rec else
                         f' <a href="{_esc(s["link"])}" target="_blank" rel="noopener">לחוזה החתום</a>' if s["link"] else "")
            status = (f'<div class="alert alert-warn" style="margin:0 18px 12px">{ui.icon("alert")}<span>הלקוח כבר חתם'
                      f'{" " + _esc(when) if when else ""}. שליחה כאן היא חוזה חדש.{to_signed}</span></div>')
        side = (_send_card(client, s, again=already) + _version_card(client, s, base, version)
                + f'<section class="card reveal" style="--i:4"><div class="card-head"><h2 class="card-title">לפני השליחה</h2></div>'
                f'{status}{_checks(preflight(client))}'
                f'<div class="how">אפשר לשלוח גם מהמשימה ב-ClickUp, בכפתור ״שלח חוזה ללקוח במייל״. אם משנים שם מחיר או מייל, '
                f'החוזה כאן מתעדכן תוך דקה.</div></section>')
        if problem:
            body = ""
            label = ""
            side = (f'<div class="alert alert-danger reveal">{ui.icon("alert")}<span>{_esc(problem)}</span></div>' + side)
    paper = (f'<div class="reveal" style="--i:1"><p class="paper-label">{ui.icon("eye", 14)}<span>{_esc(label)}</span></p>'
             f'<article class="paper">{body}</article></div>' if body else
             f'<div class="card">{ui.empty("אי אפשר להציג את החוזה", "יש לתקן את מה שמופיע בצד.", ico="file")}</div>')
    page = (back + head + f'<div class="ct-grid"><div>{paper}</div><aside class="ct-side">{side}</aside></div>')
    css = (clients_pages.CSS + CSS + contract.CONTRACT_CSS + EDIT_CSS
           + ".paper [data-ask] { background: #eef4ff; color: #3538cd; font-weight: 500; }")
    return ui.app_page(base, "contracts", f"החוזה של {name} · חוזים", page, css=css, script=CT_JS + PAGE_JS + VERSION_JS, spa=True)


# ------------------------------------------------------- on the client card


def client_card_section(client: dict[str, Any], entries: list[dict[str, Any]], base: str) -> str:
    cid = str(client.get("id") or "")
    s = state_for(client, entries, _records().get(cid))
    lines = []
    if s["sent_at"]:
        rem = f'{s["reminders"]} תזכורות' if s["reminders"] > 1 else "נשלחה תזכורת" if s["reminders"] else ""
        lines.append(f'נשלח {ui.when(s["sent_at"], "-")}' + (f" · {rem}" if rem else ""))
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


def send_contract(client_id: str, body: dict[str, Any], *, dry_run: bool = False) -> tuple[int, dict[str, Any]]:
    """The contract page's send: ``{"mode": "email"|"link", "new_contract": bool}``.

    Refuses what the ClickUp button would send broken (no price, no provider
    details), an email to nobody, a new contract to a client who signed unless
    the page said so, and a second press within a minute.
    """
    from .automations import send_quote
    from .lib import idempotency

    mode = "link" if body.get("mode") == "link" else "email"
    client = clients_pages._find_client(client_id, dry_run)
    if not client:
        return 404, {"errors": ["הלקוח לא נמצא"]}
    blockers = [t for k, t, _x in preflight(client) if k == "err"]
    if blockers:
        return 422, {"errors": ["אי אפשר לשלוח: " + ", ".join(blockers)]}
    if mode == "email" and not str(client.get("email") or "").strip():
        return 422, {"errors": ["אין מייל ב-ClickUp. אפשר ליצור קישור ולשלוח אותו ידנית."]}
    # Decided exactly as the page decided what to show: the same state.
    from . import dashboard

    entries = dashboard._SAMPLE if dry_run else dashboard.run_log.read_all()
    signed = state_for(client, entries, _records().get(client_id))["state"] in SIGNED_STATES
    if signed and not body.get("new_contract"):
        return 409, {"errors": ["הלקוח כבר חתם. חוזה חדש שולחים מ״שליחת חוזה חדש״ בדף החוזה."]}
    once = f"dashboard_send:{client_id}"
    if not idempotency.claim(once, ttl=60):
        return 429, {"errors": ["החוזה נשלח לפני רגע. אפשר לשלוח שוב בעוד דקה."]}
    try:
        # The page asked first (louder for a signed client, above), so this send is meant.
        out = send_quote.send(client_id, dry_run=dry_run, email=mode == "email", source="מהדשבורד",
                              new_contract=True)
    except Exception as exc:  # noqa: BLE001 - said on the page, logged by send_quote
        idempotency.release(once)
        return 502, {"errors": [f"השליחה נכשלה: {exc}"]}
    clients_pages._CACHE.update(at=0.0, clients=None)  # the status on ClickUp just changed
    return 200, {"ok": True, "url": out["url"], "to": out.get("to", ""), "delivered": bool(out.get("delivered"))}


def contract_link(client_id: str, *, dry_run: bool = False) -> tuple[int, dict[str, Any]]:
    """The link to a contract already waiting for its signature, for Dror to send
    himself. Not a send: no status change, no reminder reset, nothing in the log."""
    from . import dashboard

    client = clients_pages._find_client(client_id, dry_run)
    if not client:
        return 404, {"errors": ["הלקוח לא נמצא"]}
    entries = dashboard._SAMPLE if dry_run else dashboard.run_log.read_all()
    if state_for(client, entries, _records().get(client_id))["state"] != "waiting":
        return 409, {"errors": ["אין חוזה שממתין לחתימה. קודם שולחים אותו."]}
    return 200, {"url": signing.sign_url(client_id)}


# ------------------------------------------------------- a client's own version

EDIT_CSS = """
.paper .ce-mark { position: relative; border-radius: 6px; margin: 0 -10px; padding: 0 10px;
  box-shadow: inset -3px 0 0 var(--ce-c); background: color-mix(in srgb, var(--ce-c) 7%, transparent); }
.paper .ce-edited { --ce-c: #f79009; }
.paper .ce-new { --ce-c: #12b76a; }
.paper .ce-mark::before { content: attr(data-mark); position: absolute; inset-inline-end: 6px; top: 4px; font-size: 11px;
  font-weight: 600; color: var(--ce-c); }
.ce-paper { padding: 34px 40px; }
.ce-item { position: relative; border-radius: 8px; margin: 0 -14px; padding: 2px 14px; border: 1px solid transparent;
  transition: background var(--d2), border-color var(--d2); }
.ce-item:hover, .ce-item:focus-within, .ce-item.is-editing { background: #f8fafc; border-color: #e5e8ed; }
.ce-item.is-locked .ce-body { opacity: .78; }
.ce-item.ce-edited { box-shadow: inset -3px 0 0 #f79009; }
.ce-item.ce-new { box-shadow: inset -3px 0 0 #12b76a; }
.ce-item.is-empty .ce-body { color: #98a2b3; font-style: italic; padding: 10px 0; }
.ce-tools { position: absolute; inset-inline-end: 6px; top: 4px; z-index: 2; display: flex; gap: 2px; padding: 2px; border-radius: 9px;
  background: #fff; border: 1px solid #e5e8ed; box-shadow: 0 4px 12px -4px rgba(16, 24, 40, .18);
  opacity: 0; pointer-events: none; transition: opacity var(--d2); }
.ce-item:hover > .ce-tools, .ce-item:focus-within > .ce-tools { opacity: 1; pointer-events: auto; }
.ce-item.is-editing > .ce-tools { display: none; }
.ce-tools .btn { --h: 28px; width: 28px; padding: 0; justify-content: center; color: #475467; }
.ce-tools .btn:hover { color: #101828; }
.ce-tools .btn[data-ce=del]:hover { color: #d92d20; }
.ce-tag { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; color: #667085; background: #f2f4f7;
  border-radius: 99px; padding: 1px 9px; margin: 2px 0 0; }
.ce-tag .icon { width: 11px; height: 11px; }
.ce-chip { background: #eef4ff; color: #3538cd; border-radius: 5px; padding: 0 5px; font-weight: 500; }
.ce-edit { display: grid; gap: 8px; padding: 8px 0 10px; }
.ce-edit .textarea { font: inherit; font-size: 14.5px; line-height: 1.75; min-height: 60px; background: #fff; color: #1d2939; }
.ce-edit .input { font-size: 16px; font-weight: 700; background: #fff; color: #1d2939; }
.ce-edit .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.ce-edit .hint { font-size: 12px; color: #667085; }
.ce-add { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin: 4px 0 8px; padding: 8px; border-radius: 10px;
  background: #f2f4f7; animation: menu-in var(--d2) var(--ease); }
.ce-add .lbl { font-size: 12.5px; color: #667085; margin-inline-end: 4px; }
.ce-item.ce-hr { padding-top: 4px; padding-bottom: 4px; }
.ce-gone { animation: ce-out var(--d3) var(--ease) forwards; }
@keyframes ce-out { to { opacity: 0; transform: scale(.98); } }
.version-card .summary { font-size: 13.5px; color: var(--fg-muted); }
.version-card .acts { display: flex; gap: 8px; flex-wrap: wrap; }
.ce-side .steps { margin: 0; padding: 0 18px 4px 0; font-size: 13.5px; color: var(--fg-muted); line-height: 1.8; }
.ce-side .steps .icon { width: 13px; height: 13px; vertical-align: -2px; }
.ce-side .acts { display: grid; gap: 8px; }
.ce-side .dirty { font-size: 12.5px; color: var(--warn-ink); display: none; align-items: center; gap: 6px; }
.ce-side.is-dirty .dirty { display: flex; }
/* touch: a tap picks a block and shows its tools under it */
@media (hover: none) {
  .ce-tools { display: none; }
  .ce-item.is-active > .ce-tools { display: flex; position: static; opacity: 1; pointer-events: auto; width: max-content;
    margin: 4px auto 8px 0; box-shadow: none; }
  .ce-item.is-active { background: #f8fafc; border-color: #e5e8ed; }
}
.ce-savebar { position: fixed; z-index: 60; inset-inline: 12px; bottom: 12px; display: none; align-items: center; gap: 10px;
  padding: 8px 8px 8px 14px; border-radius: 14px; background: #101828; color: #fff; font-size: 13.5px; box-shadow: var(--sh-xl); }
.ce-savebar span { flex: 1; }
@media (max-width: 1100px) { .ce-savebar.is-on { display: flex; } .ce-paper { margin-bottom: 70px; } }
@media (max-width: 640px) { .ce-paper { padding: 20px 16px; } .ce-item { margin: 0 -8px; padding: 2px 8px; } }
@media (prefers-reduced-motion: reduce) { .ce-gone, .ce-add { animation: none; } }
"""

# The version card on the contract page: reset to the standard.
VERSION_JS = r"""
var vcard = document.getElementById('version-card');
if (vcard) vcard.addEventListener('click', function (e) {
  var b = e.target.closest('button[data-ver=reset]'); if (!b) return;
  UI.confirm({title: 'לחזור לנוסח הרגיל?', text: 'השינויים שעשית בחוזה של הלקוח הזה יימחקו, והוא יקבל את הנוסח הרגיל. חוזה שכבר נשלח או נחתם לא משתנה.',
              ok: 'חזרה לנוסח הרגיל', danger: true, icon: 'rotate'}).then(function (yes) {
    if (!yes) return; UI.busy(b, true);
    UI.api('/contracts/' + encodeURIComponent(vcard.dataset.client) + '/version', {reset: true}).then(function (r) {
      UI.busy(b, false);
      if (r._status !== 200) { UI.toast((r.errors || ['לא הצלחתי'])[0], {kind: 'error'}); return; }
      UI.toast('החוזה חזר לנוסח הרגיל'); UI.reload();
    });
  });
});
"""

# The editor: edit, delete or add blocks, then save the whole version at once.
EDIT_JS = r"""
var ed = document.getElementById('ce'), side = document.getElementById('ce-side');
if (ed && side) (function () {
  var main = document.querySelector('main.page'), dirty = false, cid = ed.dataset.client;
  var bar = document.getElementById('ce-savebar');
  function setDirty() { dirty = true; side.classList.add('is-dirty'); if (bar) bar.classList.add('is-on'); }
  window.onbeforeunload = function () { return document.getElementById('ce') && dirty ? '' : undefined; };
  // a block keeps its number on screen; a new one gets its number when saved
  function num(it, blank) { return it.dataset.prefix || blank; }
  function lines(t) { return t.split('\n').map(function (x) { return x.trim(); }).filter(Boolean); }
  function show(it) {
    var kind = it.dataset.kind, text = it.dataset.text || '', body = it.querySelector('.ce-body');
    body.innerHTML = ''; it.classList.toggle('is-empty', !text.trim() && kind !== 'hr');
    if (kind === 'hr') { body.appendChild(document.createElement('hr')); return; }
    if (!text.trim()) { body.textContent = kind === 'h2' ? 'כותרת הסעיף' : 'לחיצה על העיפרון לכתיבה'; return; }
    var ls = lines(text), node;
    if (kind === 'ul' || kind === 'ol') { node = document.createElement(kind);
      ls.forEach(function (l) { var li = document.createElement('li'); li.textContent = l; node.appendChild(li); }); }
    else if (kind === 'h2' || kind === 'h3') { node = document.createElement(kind);
      node.textContent = (it.dataset.num === '1' ? num(it, kind === 'h2' ? '#.' : '#.#') + ' ' : '') + ls.join(' '); }
    else { node = document.createElement('p'); if (it.dataset.num === '1') node.appendChild(document.createTextNode(num(it, '#.#') + ' '));
      ls.forEach(function (l, i) { if (i) node.appendChild(document.createElement('br')); node.appendChild(document.createTextNode(l)); }); }
    body.appendChild(node);
  }
  function close(it, keep) {
    var box = it.querySelector('.ce-edit'); if (!box) return true;
    var field = box.querySelector('.input,.textarea'), v = field.value;
    if (keep) {
      if (!v.trim()) { UI.toast('הקטע ריק. כדי להסיר אותו מוחקים אותו', {kind: 'error'}); field.focus(); return false; }
      if (v.indexOf('{{') >= 0 || v.indexOf('}}') >= 0) { UI.toast('אי אפשר להשתמש בסוגריים מסולסלים כפולים', {kind: 'error'}); return false; }
      if (v !== it.dataset.text) { it.dataset.text = v; if (!it.dataset.isnew) { it.dataset.edited = '1'; it.classList.add('ce-edited'); } setDirty(); }
    }
    box.remove(); it.classList.remove('is-editing'); it.querySelector('.ce-body').hidden = false; show(it);
    return true;
  }
  function open(it) {
    var other = ed.querySelector('.ce-item.is-editing'); if (other && other !== it && !close(other, true)) return;
    if (it.classList.contains('is-editing')) return;
    var kind = it.dataset.kind, head = kind === 'h2' || kind === 'h3';
    var box = document.createElement('div'); box.className = 'ce-edit';
    var field = document.createElement(head ? 'input' : 'textarea'); field.className = head ? 'input' : 'textarea';
    field.value = it.dataset.text || ''; field.setAttribute('aria-label', 'טקסט הקטע');
    if (!head) field.rows = Math.min(14, Math.max(3, field.value.split('\n').length + 1));
    var row = document.createElement('div'); row.className = 'row';
    row.innerHTML = '<button type="button" class="btn btn-primary btn-sm" data-ce="ok">' + UI.icon('check') + '<span>אישור</span></button>' +
      '<button type="button" class="btn btn-sm" data-ce="cancel">ביטול</button><span class="hint"></span>';
    row.querySelector('.hint').textContent = (kind === 'ul' || kind === 'ol') ? 'כל שורה היא סעיף ברשימה' :
      (it.dataset.num === '1' ? 'המספור מתעדכן לבד' : '');
    box.appendChild(field); box.appendChild(row);
    it.querySelector('.ce-body').hidden = true; it.appendChild(box); it.classList.add('is-editing');
    field.focus(); if (field.setSelectionRange) field.setSelectionRange(field.value.length, field.value.length);
    field.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { e.preventDefault(); close(it, false); }
      if (e.key === 'Enter' && (head || e.ctrlKey || e.metaKey)) { e.preventDefault(); close(it, true); }
    });
  }
  function item(kind, text, num) {
    var it = document.createElement('div'); it.className = 'ce-item ce-new' + (kind === 'hr' ? ' ce-hr' : '');
    it.dataset.kind = kind; it.dataset.text = text || ''; it.dataset.isnew = '1'; if (num) it.dataset.num = '1';
    it.innerHTML = '<div class="ce-body"></div><div class="ce-tools">' +
      (kind === 'hr' ? '' : '<button type="button" class="btn btn-ghost" data-ce="edit" data-tip="עריכה" aria-label="עריכה">' + UI.icon('pen') + '</button>') +
      '<button type="button" class="btn btn-ghost" data-ce="del" data-tip="מחיקה" aria-label="מחיקה">' + UI.icon('trash') + '</button>' +
      '<button type="button" class="btn btn-ghost" data-ce="add" data-tip="הוספה אחרי" aria-label="הוספה אחרי">' + UI.icon('plus') + '</button></div>';
    show(it); return it;
  }
  // A clause is its heading and what follows, up to its separator.
  function clauseOf(it) {
    var out = [it], n = it.nextElementSibling;
    while (n && n.classList.contains('ce-item') && n.dataset.kind !== 'h2') { out.push(n); if (n.dataset.kind === 'hr') break; n = n.nextElementSibling; }
    return out;
  }
  function del(it) {
    var group = it.dataset.kind === 'h2' && it.dataset.num === '1' ? clauseOf(it) : [it];
    if (group.some(function (x) { return x.classList.contains('is-locked'); })) {
      UI.toast('בסעיף הזה יש פרטים מ-ClickUp (שמות או מחירים), ולכן אי אפשר למחוק אותו כולו. אפשר למחוק פסקאות בודדות.', {kind: 'error', duration: 5000}); return; }
    var anchor = group[group.length - 1].nextElementSibling;
    group.forEach(function (x) { x.classList.add('ce-gone'); });
    setTimeout(function () { group.forEach(function (x) { x.remove(); x.classList.remove('ce-gone'); }); }, 180);
    setDirty();
    UI.toast(group.length > 1 ? 'הסעיף נמחק' : 'הקטע נמחק', {action: {label: 'ביטול', run: function () {
      group.forEach(function (x) { x.classList.remove('ce-gone'); ed.insertBefore(x, anchor); }); }}});
  }
  function addMenu(it) {
    var was = ed.querySelector('.ce-add'); if (was) { var owner = was.previousElementSibling; was.remove(); if (owner === it) return; }
    var m = document.createElement('div'); m.className = 'ce-add';
    m.innerHTML = '<span class="lbl">להוסיף כאן:</span>' +
      '<button type="button" class="btn btn-sm" data-new="p">' + UI.icon('align') + '<span>פסקה</span></button>' +
      '<button type="button" class="btn btn-sm" data-new="ul">' + UI.icon('check-square') + '<span>רשימה</span></button>' +
      '<button type="button" class="btn btn-sm" data-new="clause">' + UI.icon('type') + '<span>סעיף חדש</span></button>' +
      '<button type="button" class="btn btn-ghost btn-sm" data-new="x" aria-label="סגירה">' + UI.icon('x') + '</button>';
    it.after(m);
  }
  function add(m, kind) {
    var after = m.previousElementSibling; m.remove(); if (kind === 'x') return;
    var made;
    if (kind === 'clause') {
      // a clause ends with its separator: after one, the new clause opens straight away
      var h = item('h2', '', true), p = item('p', ''), hr = item('hr');
      if (after.dataset.kind === 'hr') { after.after(h); h.after(p); p.after(hr); }
      else { after.after(hr); hr.after(h); h.after(p); }
      made = h;
    } else {
      var num = kind === 'p' && after.dataset.kind === 'p' && after.dataset.num === '1';
      made = item(kind, '', num); after.after(made);
    }
    setDirty(); open(made);
  }
  main.addEventListener('click', function (e) {
    var t = e.target.closest('[data-ce],[data-new]');
    if (!t) {  // a tap on a block picks it (its tools show on a touch screen)
      var pick = e.target.closest('#ce .ce-item');
      ed.querySelectorAll('.ce-item.is-active').forEach(function (x) { if (x !== pick) x.classList.remove('is-active'); });
      if (pick && !pick.classList.contains('is-editing')) pick.classList.toggle('is-active');
      return;
    }
    if (!main.contains(t)) return;
    var it = t.closest('.ce-item');
    if (t.dataset.new) { add(t.closest('.ce-add'), t.dataset.new); return; }
    var act = t.dataset.ce;
    if (act === 'edit') open(it);
    else if (act === 'ok') close(it, true);
    else if (act === 'cancel') close(it, false);
    else if (act === 'del') del(it);
    else if (act === 'add') addMenu(it);
    else if (act === 'save') save(t);
    else if (act === 'leave') leave();
    else if (act === 'reset') reset(t);
  });
  ed.addEventListener('dblclick', function (e) {
    var it = e.target.closest('.ce-item'); if (it && it.querySelector('[data-ce=edit]') && !it.classList.contains('is-editing')) open(it); });
  function payload() {
    var items = [], bad = '';
    ed.querySelectorAll('.ce-item').forEach(function (it) {
      if (it.dataset.isnew) {
        if (it.dataset.kind !== 'hr' && !(it.dataset.text || '').trim()) bad = 'יש קטע חדש שעוד לא נכתב. כותבים אותו או מוחקים.';
        items.push({'new': it.dataset.kind, text: it.dataset.text || '', num: it.dataset.num === '1'});
      } else {
        var x = {i: +it.dataset.i}; if (it.dataset.edited) x.text = it.dataset.text; items.push(x);
      }
    });
    return bad ? {error: bad} : {items: items};
  }
  function save(btn) {
    var openNow = ed.querySelector('.ce-item.is-editing'); if (openNow && !close(openNow, true)) return;
    var p = payload(); if (p.error) { UI.toast(p.error, {kind: 'error'}); return; }
    UI.busy(btn, true);
    UI.api('/contracts/' + encodeURIComponent(cid) + '/version', {base: ed.dataset.base, items: p.items}).then(function (r) {
      UI.busy(btn, false);
      if (r._status !== 200) { UI.toast((r.errors || ['השמירה נכשלה'])[0], {kind: 'error', duration: 6000}); return; }
      dirty = false; if (bar) bar.classList.remove('is-on'); UI.toast(r.standard ? 'אין שינויים: הלקוח יקבל את הנוסח הרגיל' : 'הנוסח נשמר. הוא יישלח ללקוח בשליחה הבאה');
      UI.forgetPages(); UI.go(side.dataset.back);
    });
  }
  function leave() {
    if (!dirty) { UI.go(side.dataset.back); return; }
    UI.confirm({title: 'לצאת בלי לשמור?', text: 'השינויים שלא נשמרו יימחקו.', ok: 'יציאה בלי שמירה', danger: true, icon: 'alert'})
      .then(function (yes) { if (yes) { dirty = false; UI.go(side.dataset.back); } });
  }
  function reset(btn) {
    UI.confirm({title: 'לחזור לנוסח הרגיל?', text: 'כל השינויים בחוזה של הלקוח הזה יימחקו, והוא יקבל את הנוסח הרגיל. חוזה שכבר נשלח או נחתם לא משתנה.',
                ok: 'חזרה לנוסח הרגיל', danger: true, icon: 'rotate'}).then(function (yes) {
      if (!yes) return; UI.busy(btn, true);
      UI.api('/contracts/' + encodeURIComponent(cid) + '/version', {reset: true}).then(function (r) {
        UI.busy(btn, false);
        if (r._status !== 200) { UI.toast((r.errors || ['לא הצלחתי'])[0], {kind: 'error'}); return; }
        dirty = false; UI.toast('החוזה חזר לנוסח הרגיל'); UI.forgetPages(); UI.go(side.dataset.back);
      });
    });
  }
})();
"""


def _version_card(client: dict[str, Any], s: dict[str, Any], base: str, version: Optional[dict[str, Any]]) -> str:
    """Which version this client gets, the way to edit it, and whether the client holds an older one."""
    cid = str(client.get("id") or "")
    notes = []
    if version:
        badge = '<span class="badge badge-brand badge-dot">נוסח מותאם</span>'
        what = (f"שינויים בנוסח של הלקוח הזה: {_esc(version.get('summary') or '')}. "
                f"נשמר {ui.when(version.get('updated_at'), '-')}.")
        if version.get("base") != contract_edit.sha(contract_edit.standard()):
            notes.append(("info", "הנוסח הרגיל השתנה מאז שערכת. הנוסח של הלקוח הזה לא מתעדכן לבד."))
    else:
        badge = '<span class="badge">נוסח רגיל</span>'
        what = "הלקוח יקבל את הנוסח הרגיל. אפשר לערוך, להוסיף או למחוק סעיפים רק בחוזה שלו, לפני השליחה."
    if s["state"] == "waiting":
        sent = contract_store.sent(cid)
        if (sent or {}).get("template") != (version or {}).get("template"):
            notes.append(("warn", "הלקוח קיבל נוסח אחר ממה שמוצג כאן. כדי שיקבל את הנוסח הזה, שולחים שוב."))
    alerts = "".join(f'<div class="alert alert-{k}" style="margin:0">{ui.icon("alert" if k == "warn" else "info")}'
                     f'<span>{_esc(t)}</span></div>' for k, t in notes)
    reset = (f'<button type="button" class="btn btn-ghost" data-ver="reset">{ui.icon("rotate", 15)}'
             f'<span>חזרה לנוסח הרגיל</span></button>' if version else "")
    return (f'<section class="card version-card reveal" id="version-card" style="--i:3" data-client="{_esc(cid)}">'
            f'<div class="card-head"><h2 class="card-title">נוסח החוזה</h2>{badge}</div>'
            f'<div class="ct-card-body"><p class="summary" style="margin:0">{what}</p>{alerts}<div class="acts">'
            f'<a class="btn" href="{base}/contracts/{quote(cid)}?edit=1">{ui.icon("pen", 15)}<span>עריכת החוזה</span></a>'
            f'{reset}</div></div></section>')


def editor_page(client: dict[str, Any], base: str) -> str:
    """The contract as blocks Dror can edit, delete or add to; values from ClickUp stay locked."""
    cid = str(client.get("id") or "")
    name = str(client.get("name") or cid)
    version = contract_store.draft(cid)
    template = version["template"] if version else contract_edit.standard()
    try:
        fields = contract.fields_from_client(client)
    except contract.ContractError:
        fields = {}
    marks = contract_edit.compare(template)["marks"] if version else []
    rows = []
    for b in contract_edit.editor_blocks(template, fields):
        i = b["i"]
        if b.get("hidden"):
            rows.append(f'<div class="ce-item" data-i="{i}" data-kind="marker" hidden></div>')
            continue
        mark = marks[i] if i < len(marks) else ""
        cls = ("ce-item" + (" is-locked" if b["locked"] else "") + (" ce-hr" if b["kind"] == "hr" else "")
               + (f" ce-{mark}" if mark and not b["locked"] else ""))
        data = f'data-i="{i}" data-kind="{_esc(b["kind"])}"'
        if "text" in b:
            data += f' data-text="{_esc(b["text"])}"' + (f' data-num="1" data-prefix="{_esc(b["prefix"])}"' if b.get("num") else "")
        tools = ""
        if not b["locked"]:
            if b["kind"] in contract_edit.EDITABLE:
                tools += (f'<button type="button" class="btn btn-ghost" data-ce="edit" data-tip="עריכה" aria-label="עריכה">'
                          f'{ui.icon("pen", 15)}</button>')
            if b["kind"] != "hr":
                tip = "מחיקת הסעיף כולו" if b["kind"] == "h2" and b.get("num") else "מחיקה"
                tools += (f'<button type="button" class="btn btn-ghost" data-ce="del" data-tip="{tip}" aria-label="{tip}">'
                          f'{ui.icon("trash", 15)}</button>')
        tools += (f'<button type="button" class="btn btn-ghost" data-ce="add" data-tip="הוספה אחרי" aria-label="הוספה אחרי">'
                  f'{ui.icon("plus", 15)}</button>')
        tags = ""
        if b["locked"] and "ce-chip" in b["html"]:
            tags += f'<span class="ce-tag">{ui.icon("lock", 11)}<span>מ-ClickUp ומדף החתימה, נערך שם</span></span> '
        if b.get("section"):
            tags += f'<span class="ce-tag">{ui.icon("info", 11)}<span>{_esc(b["section"])}</span></span>'
        rows.append(f'<div class="{cls}" {data}><div class="ce-body">{b["html"]}</div>'
                    f'<div class="ce-tools">{tools}</div>{f"<div>{tags}</div>" if tags else ""}</div>')
    back_url = f"{base}/contracts/{quote(cid)}"
    paper = (f'<article class="paper ce-paper reveal" style="--i:1"><div class="contract" dir="rtl" lang="he" id="ce" '
             f'data-client="{_esc(cid)}" data-base="{contract_edit.sha(template)}">{"".join(rows)}</div></article>')
    reset = (f'<button type="button" class="btn btn-ghost btn-block" data-ce="reset">{ui.icon("rotate", 15)}'
             f'<span>חזרה לנוסח הרגיל</span></button>' if version else "")
    side = (f'<section class="card ce-side reveal" id="ce-side" style="--i:2" data-back="{_esc(back_url)}">'
            f'<div class="card-head"><h2 class="card-title">עריכה לחוזה של {_esc(name)} בלבד</h2></div>'
            f'<div class="ct-card-body"><ol class="steps">'
            f'<li>מעבירים את העכבר על קטע: {ui.icon("pen", 13)} עריכה, {ui.icon("trash", 13)} מחיקה, '
            f'{ui.icon("plus", 13)} הוספה אחריו.</li>'
            f'<li>מחיקת כותרת של סעיף מוחקת את כל הסעיף. המספור וההפניות מתעדכנים לבד.</li>'
            f'<li>שמות, מחירים ופרטי הלקוח נעולים: הם מגיעים מ-ClickUp ומדף החתימה.</li>'
            f'<li>שומרים, בודקים בתצוגה המקדימה, ושולחים כרגיל: מכאן או מהכפתור ב-ClickUp.</li></ol>'
            f'<div class="acts"><button type="button" class="btn btn-primary btn-block" data-ce="save">{ui.icon("check", 15)}'
            f'<span>שמירת הנוסח</span></button>'
            f'<button type="button" class="btn btn-block" data-ce="leave">ביטול</button>{reset}</div>'
            f'<div class="dirty">{ui.icon("alert", 13)}<span>יש שינויים שעוד לא נשמרו</span></div>'
            f'<p class="small muted" style="margin:0">הנוסח הרגיל לשאר הלקוחות לא משתנה. חוזה שכבר נשלח לא משתנה עד ששולחים שוב.</p>'
            f'</div></section>')
    back = f'<a class="back" href="{back_url}">{ui.icon("arrow-right", 15)}<span>לתצוגה המקדימה</span></a>'
    head = ui.page_head(f"עריכת החוזה של {name}", "")
    savebar = (f'<div class="ce-savebar" id="ce-savebar"><span>יש שינויים שעוד לא נשמרו</span>'
               f'<button type="button" class="btn btn-primary btn-sm" data-ce="save">{ui.icon("check", 14)}'
               f'<span>שמירה</span></button></div>')
    page = (back + head + f'<div class="ct-grid"><div>{paper}</div><aside class="ct-side">{side}</aside></div>'
            + savebar)
    css = clients_pages.CSS + CSS + contract.CONTRACT_CSS + EDIT_CSS
    return ui.app_page(base, "contracts", f"עריכת החוזה של {name} · חוזים", page, css=css, script=EDIT_JS, spa=True)


def save_version(client_id: str, body: dict[str, Any], *, dry_run: bool = False) -> tuple[int, dict[str, Any]]:
    """The editor's save (``{"base", "items"}``), or ``{"reset": true}``: back to the standard."""
    from .automations.base import Automation

    client = clients_pages._find_client(client_id, dry_run)
    if not client:
        return 404, {"errors": ["הלקוח לא נמצא"]}
    auto = Automation("contract_edit", dry_run=dry_run)
    version = contract_store.draft(client_id)
    if body.get("reset"):
        if version:
            contract_store.clear_draft(client_id)
            auto.log_action("contract_reset", client_id=client_id)
        return 200, {"ok": True, "standard": True}
    current = version["template"] if version else contract_edit.standard()
    try:
        new = contract_edit.apply(current, body.get("items") or [], str(body.get("base") or ""))
    except contract_edit.Invalid as exc:
        return 422, {"errors": [str(exc)]}
    # Still a contract this client can be sent: a reference to a deleted clause, in
    # a part this client sees, is said now rather than at sending. What the edit
    # did not cause (no price, Dror's details) the page and the send already say.
    try:
        fields = contract.fields_from_client(client)
    except contract.ContractError:
        fields = None
    if fields is not None and not contract.missing_provider():
        shown = {**fields, **{k: v or "-" for k, v in fields.items() if k in contract.REQUIRED_CLIENT_FIELDS}}
        try:
            contract.render(shown, template=new, signatures={"provider_signature": "", "client_signature": ""})
        except contract.ContractError as exc:
            return 422, {"errors": [str(exc)]}
    changes = contract_edit.compare(new)
    if not changes["changes"]:
        if version:
            contract_store.clear_draft(client_id)
            auto.log_action("contract_reset", client_id=client_id, detail="כל השינויים בוטלו")
        return 200, {"ok": True, "standard": True}
    summary = contract_edit.summary_text(changes)
    contract_store.save_draft(client_id, new, base=contract_edit.sha(contract_edit.standard()),
                              summary=summary, changes=changes["changes"])
    auto.log_action("contract_edited", client_id=client_id, detail=summary)
    return 200, {"ok": True, "summary": summary}
