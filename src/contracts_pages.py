"""The חוזים screens: where every client's contract stands, and what it will say.

* ``/contracts``: each client's contract (not sent, waiting for a signature, signed,
  or a signature whose filing failed), with the dates and reminders, and Dror's own
  signature, drawn once here and placed in the provider's box of every contract.
* ``/contracts/<id>``: that client's contract exactly as it will go out (their
  prices, Dror's signature, what the client will fill in), with a checklist of what
  is missing before pressing שלח הצעת מחיר in ClickUp; once signed, the document
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
      ' הסטטוס ב-ClickUp יעבור ל״נשלחה הצעת מחיר״, ואם הלקוח לא יחתום יישלחו תזכורות במייל.';
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
            + '<p class="small muted" style="margin:0">כמו הכפתור ״שלח הצעת מחיר״ ב-ClickUp: הסטטוס עובר ל״נשלחה הצעת '
              'מחיר״, ולקוח שלא חותם מקבל תזכורות אחרי יומיים ו-4 ימים.</p>'
            + '<div class="linkbox" id="linkbox" hidden></div></div></section>')


def contract_page(entries: list[dict[str, Any]], base: str, client_id: str, *, dry_run: bool = False,
                  new: bool = False) -> Optional[str]:
    client = clients_pages._find_client(client_id, dry_run)
    if not client:
        return None
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
        body, problem = _preview_body(client)
        label = "תצוגה מקדימה: כך הלקוח יראה את החוזה"
        status = ""
        if s["state"] == "waiting":
            rem = f', {s["reminders"]} תזכורות' if s["reminders"] else ""
            status = (f'<div class="alert alert-warn" style="margin:0 18px 12px">{ui.icon("hourglass")}<span>נשלח {_esc(signing.local_time(s["sent_at"]))}'
                      f'{rem}. ממתין לחתימה.</span></div>')
        already = s["state"] in SIGNED_STATES
        if already:
            when = signing.local_time(s["signed_at"]) if s["signed_at"] else ""
            to_signed = (f' <a href="{base}/contracts/{quote(client_id)}">לחוזה החתום</a>' if signed_rec else
                         f' <a href="{_esc(s["link"])}" target="_blank" rel="noopener">לחוזה החתום</a>' if s["link"] else "")
            status = (f'<div class="alert alert-warn" style="margin:0 18px 12px">{ui.icon("alert")}<span>הלקוח כבר חתם'
                      f'{" " + _esc(when) if when else ""}. שליחה כאן היא חוזה חדש.{to_signed}</span></div>')
        side = (_send_card(client, s, again=already)
                + f'<section class="card reveal" style="--i:3"><div class="card-head"><h2 class="card-title">לפני השליחה</h2></div>'
                f'{status}{_checks(preflight(client))}'
                f'<div class="how">אפשר לשלוח גם מהמשימה ב-ClickUp, בכפתור ״שלח הצעת מחיר״. אם משנים שם מחיר או מייל, '
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
    return ui.app_page(base, "contracts", f"החוזה של {name} · חוזים", page, css=css, script=CT_JS + PAGE_JS, spa=True)


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
        out = send_quote.send(client_id, dry_run=dry_run, email=mode == "email", source="מהדשבורד")
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
