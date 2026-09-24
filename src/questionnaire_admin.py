"""The questionnaire admin — Dror's editor, answers view and test links.

Served by the dashboard (locally by :mod:`src.dashboard`, on AWS by
:mod:`src.dashboard_lambda`) behind the same password, under ``/admin``:

  GET  /admin/questionnaires                  every questionnaire, with counts
  GET  /admin/questionnaires/<id>             the editor
  GET  /admin/questionnaires/<id>/preview     the form exactly as a client sees it
  GET  /admin/questionnaires/<id>/export.csv  every answer, one row per client
  GET  /admin/responses                       who was sent what, who answered
  GET  /admin/responses/<client>/<id>         one client's answers
  POST /admin/api/...                         JSON: create, save, set default,
                                              delete, make a link, list clients

The one write surface in an otherwise read-only dashboard, and deliberately
narrow: it edits questionnaire *content* and mints links. It never runs an
automation — no email is sent, nothing in ClickUp or Drive changes from here.

API calls must carry ``X-Requested-With: dashboard`` and a JSON body. With the
session cookie being ``SameSite=Lax``, that header is what a cross-site form
cannot forge.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple, Optional
from urllib.parse import quote

from . import ui
from .lib import questionnaire, questionnaire_store, signing
from .lib.logging_setup import get_logger

log = get_logger("admin", "questionnaire")


class Response(NamedTuple):
    status: int
    body: str
    content_type: str = "text/html; charset=utf-8"
    location: str = ""
    disposition: str = ""


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _json(status: int, data: Any) -> Response:
    return Response(status, json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8")


def _local(iso: Any) -> str:
    """ISO UTC → ``DD.MM.YYYY HH:MM`` in Israel time."""
    return ui.local_time(iso)


def _days_since(iso: Any) -> Optional[int]:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, (datetime.now(timezone.utc) - dt).days)


# ------------------------------------------------------------------- chrome

ADMIN_CSS = """
/* questionnaire list */
.qgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 14px; }
.qcard { display: flex; flex-direction: column; padding: 18px; gap: 14px; transition: box-shadow var(--d2), border-color var(--d2), transform var(--d2) var(--ease); }
.qcard:hover { box-shadow: var(--sh-md); border-color: var(--border-strong); transform: translateY(-1px); }
.qcard.is-default { border-color: color-mix(in srgb, var(--brand) 35%, var(--border)); }
.qcard-top { display: flex; gap: 12px; align-items: flex-start; }
.qcard-icon { width: 40px; height: 40px; border-radius: 11px; display: grid; place-items: center; flex: none;
  background: var(--surface-active); color: var(--fg-2); }
.qcard.is-default .qcard-icon { background: var(--brand-grad); color: #fff; box-shadow: 0 6px 14px -6px rgba(47, 125, 225, .7); }
.qcard-title { margin: 0; font-size: 15.5px; font-weight: 600; line-height: 1.35; }
.qcard-title a { color: var(--fg); }
.qcard-meta { margin-top: 3px; font-size: 13px; color: var(--fg-muted); display: flex; flex-wrap: wrap; gap: 4px 10px; }
.qcard-rate { display: grid; gap: 7px; }
.qcard-rate .row { display: flex; justify-content: space-between; font-size: 12.5px; color: var(--fg-muted); }
.qcard-foot { display: flex; align-items: center; gap: 6px; margin-top: auto; padding-top: 14px; border-top: 1px solid var(--border-soft); }
.qcard-foot .spacer { flex: 1; }
.qcard.is-leaving { animation: toast-out var(--d3) var(--ease) forwards; }
/* editor */
.ed-bar { position: sticky; top: var(--sticky-top, 60px); z-index: 30; margin: -30px -20px 22px; padding: 12px 20px;
  background: color-mix(in srgb, var(--bg) 86%, transparent); backdrop-filter: saturate(180%) blur(12px);
  -webkit-backdrop-filter: saturate(180%) blur(12px); border-bottom: 1px solid var(--border); }
.ed-bar-in { max-width: 880px; margin: 0 auto; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ed-bar h1 { margin: 0; font-size: 17px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 44vw; }
.ed-bar .spacer { flex: 1; }
.save-state { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; color: var(--fg-muted); white-space: nowrap; }
.save-state i { width: 7px; height: 7px; border-radius: 50%; background: var(--success); transition: background var(--d2); }
.save-state.is-dirty i { background: var(--warn); animation: pulse 1.6s infinite; }
.save-state.is-saving i { background: transparent; border: 2px solid var(--fg-subtle); border-inline-end-color: transparent;
  width: 11px; height: 11px; animation: spin .7s linear infinite; }
.ed-card { padding: 20px; margin-bottom: 16px; display: grid; gap: 16px; }
.ed-grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.inline-input { width: 100%; border: 1px solid transparent; border-radius: 8px; background: transparent; color: var(--fg);
  font: inherit; padding: 6px 9px; margin-inline: -9px; transition: border-color var(--d1), background var(--d1), box-shadow var(--d2); }
.inline-input:hover { background: var(--surface-hover); }
.inline-input:focus { outline: none; background: var(--surface); border-color: var(--brand); box-shadow: 0 0 0 4px var(--ring-soft); }
.inline-input.xl { font-size: 20px; font-weight: 700; letter-spacing: -.01em; }
.inline-input.lg { font-size: 16px; font-weight: 600; }
.inline-input.sm { font-size: 13.5px; color: var(--fg-muted); }
.ed-section { margin-bottom: 16px; }
.ed-section-head { display: flex; align-items: flex-start; gap: 10px; padding: 16px 18px 12px 12px; }
.ed-section-head .drag-handle { margin-top: 2px; }
.ed-num { width: 28px; height: 28px; border-radius: 8px; flex: none; display: grid; place-items: center; margin-top: 4px;
  background: var(--fg); color: var(--bg); font-size: 13px; font-weight: 700; }
.ed-section-titles { flex: 1; min-width: 0; display: grid; gap: 2px; }
.ed-tools { display: flex; gap: 2px; flex: none; }
.ed-section-body { padding: 4px 18px 18px; display: grid; gap: 10px; }
.ed-section.is-collapsed .ed-section-body { display: none; }
.ed-section .collapse .icon { transition: transform var(--d2) var(--ease); }
.ed-section.is-collapsed .collapse .icon { transform: rotate(90deg); }
.ed-q { border: 1px solid var(--border); border-radius: 12px; background: var(--surface-2); padding: 12px 14px;
  display: grid; gap: 10px; transition: border-color var(--d2), box-shadow var(--d2), background var(--d2); }
.ed-q:focus-within { border-color: var(--border-strong); background: var(--surface); box-shadow: var(--sh-md); }
.ed-q-main { display: grid; grid-template-columns: auto auto 1fr 210px auto; gap: 8px 10px; align-items: center; }
.ed-q-main .drag-handle { margin-inline-start: -6px; }
.kind-icon { width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; background: var(--surface);
  border: 1px solid var(--border); color: var(--fg-2); }
.ed-q .select { height: 34px; font-size: 13.5px; }
.ed-q-extra { display: grid; gap: 10px; padding-inline-start: 66px; }
.ed-q-foot { display: flex; align-items: center; gap: 8px; padding-inline-start: 66px; }
.ed-q-foot .spacer { flex: 1; }
.ed-key { font-family: var(--mono); font-size: 11px; color: var(--fg-subtle); direction: ltr; }
.ed-q .ed-tools { opacity: .55; transition: opacity var(--d2); }
.ed-q:hover .ed-tools, .ed-q:focus-within .ed-tools { opacity: 1; }
.opts { display: grid; gap: 6px; }
.opt { display: flex; align-items: center; gap: 8px; }
.opt .mark { width: 16px; height: 16px; flex: none; border: 1.5px solid var(--border-strong); border-radius: 50%; }
.opt .mark.sq { border-radius: 4px; }
.opt .input { height: 32px; font-size: 13.5px; }
.opt .btn { opacity: 0; transition: opacity var(--d1); }
.opt:hover .btn, .opt:focus-within .btn { opacity: 1; }
.add-row { display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%; height: 42px; border-radius: 12px;
  border: 1.5px dashed var(--border-strong); background: transparent; color: var(--fg-muted); font-size: 14px; font-weight: 500;
  cursor: pointer; transition: color var(--d1), border-color var(--d1), background var(--d1), transform var(--d1) var(--ease); }
.add-row:hover { color: var(--brand); border-color: var(--brand); background: var(--brand-soft); }
.add-row:active { transform: scale(.99); }
.add-row.big { height: 54px; border-radius: var(--r-lg); }
.is-new { animation: rise .4s var(--ease) both; }
/* responses */
.person { display: flex; align-items: center; gap: 10px; }
.avatar { width: 32px; height: 32px; border-radius: 50%; flex: none; display: grid; place-items: center; font-size: 12.5px; font-weight: 600;
  color: #fff; background: linear-gradient(135deg, #00c2e0, #2f7de1); }
.avatar.lg { width: 52px; height: 52px; font-size: 18px; box-shadow: 0 8px 18px -8px rgba(47, 125, 225, .7); }
.no-results { display: none; }
.linkbox { display: grid; gap: 10px; }
.linkbox .copyrow { display: flex; gap: 8px; }
.linkbox .input { font-family: var(--mono); font-size: 12.5px; direction: ltr; text-align: left; }
.linkbox .acts { display: flex; gap: 8px; flex-wrap: wrap; }
.linkbox-ok { display: flex; align-items: center; gap: 8px; color: var(--success-ink); font-weight: 600; font-size: 14px; }
/* one response */
.resp-head { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }
.resp-head h1 { margin: 0; font-size: 24px; font-weight: 700; letter-spacing: -.01em; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.resp-meta { margin-top: 4px; display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 13.5px; color: var(--fg-muted); }
.resp-meta span { display: inline-flex; align-items: center; gap: 5px; }
.answers-sec { margin-bottom: 14px; }
.answers-sec h2 { margin: 0; font-size: 14.5px; font-weight: 600; }
.qa { padding: 14px 18px; border-bottom: 1px solid var(--border-soft); display: grid; gap: 3px; }
.qa:last-child { border-bottom: 0; }
.qa dt { font-size: 13px; color: var(--fg-muted); font-weight: 500; }
.qa dd { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--fg); }
.qa dd.none { color: var(--fg-subtle); font-style: italic; }
.timeline { list-style: none; margin: 0; padding: 6px 18px 16px; }
.timeline li { position: relative; padding: 10px 26px 10px 0; font-size: 14px; }
.timeline li::before { content: ""; position: absolute; right: 5px; top: 16px; width: 9px; height: 9px; border-radius: 50%;
  background: var(--surface); border: 2px solid var(--brand); }
.timeline li:not(:last-child)::after { content: ""; position: absolute; right: 9px; top: 28px; bottom: -8px; width: 1.5px; background: var(--border); }
.timeline time { color: var(--fg-muted); font-size: 13px; margin-inline-start: 6px; }
@media (max-width: 720px) {
  .ed-bar { margin: -22px -16px 18px; padding: 10px 16px; }
  .ed-grid2 { grid-template-columns: 1fr; }
  .ed-q-main { grid-template-columns: auto auto 1fr; }
  .ed-q-main .picker, .ed-q-main .switch { grid-column: 3; }
  .ed-q-extra, .ed-q-foot { padding-inline-start: 0; }
  .qgrid { grid-template-columns: 1fr; }
  .hide-sm { display: none; }
}
"""


def _shell(base: str, active: str, title: str, body: str, *, script: str = "", narrow: bool = False,
           spa: bool = False, fill: bool = False) -> Response:
    return Response(200, ui.app_page(base, active, title, body, script=script, css=ADMIN_CSS, narrow=narrow,
                                     spa=spa, fill=fill))


def _data_tag(element_id: str, data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/json" id="{element_id}">{raw}</script>'


LINK_JS = r"""
// A made link: copy, open, or hand it to WhatsApp (sent by Dror himself, not by us).
function showLink(box, url, name) {
  box.hidden = false; box.innerHTML =
    '<div class="linkbox-ok">' + UI.icon('check-circle') + '<span>הקישור מוכן</span></div>' +
    '<div class="copyrow"><input class="input" readonly aria-label="קישור"><button type="button" class="btn" data-act="copy">' +
    UI.icon('copy') + '<span>העתקה</span></button></div>' +
    '<div class="acts"><a class="btn btn-sm" target="_blank" rel="noopener" data-act="open">' + UI.icon('external') +
    '<span>פתיחה</span></a><a class="btn btn-sm" target="_blank" rel="noopener" data-act="wa">' + UI.icon('message') +
    '<span>שליחה בוואטסאפ</span></a></div>';
  var input = box.querySelector('input'); input.value = url;
  input.addEventListener('focus', function () { input.select(); });
  box.querySelector('[data-act=copy]').onclick = function () { UI.copy(url, this); };
  box.querySelector('[data-act=open]').href = url;
  var text = (name ? 'היי ' + name + ', ' : '') + 'הנה הקישור לשאלון: ' + url;
  box.querySelector('[data-act=wa]').href = 'https://wa.me/?text=' + encodeURIComponent(text);
  box.classList.remove('is-new'); void box.offsetWidth; box.classList.add('is-new');
}
"""


# ------------------------------------------------------------------ pages


def _ms(iso: Any) -> int:
    """Epoch milliseconds, for sorting a table by a time (0 when there is none)."""
    try:
        return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return 0


def _counts(qid: str, responses: list[dict[str, Any]]) -> tuple[int, int]:
    mine = [r for r in responses if r.get("questionnaire_id") == qid]
    return len(mine), sum(1 for r in mine if r.get("status") == "answered")


def page_list(base: str) -> Response:
    defs = questionnaire_store.list_definitions()
    default = questionnaire_store.default_id()
    responses = questionnaire_store.list_responses()
    cards = ""
    for i, d in enumerate(sorted(defs, key=lambda d: d["id"] != default)):
        sent, answered = _counts(d["id"], responses)
        n = sum(len(s.get("questions") or []) for s in d.get("sections") or [])
        secs = len(d.get("sections") or [])
        is_def = d["id"] == default
        qid = quote(d["id"])
        rate = round(100 * answered / sent) if sent else 0
        badge = (f'<span class="badge badge-brand">{ui.icon("star", 11)}<span>נשלח ללקוחות</span></span>'
                 if is_def else "")
        menu = (f'<button class="menu-item" data-copy="{_esc(d["id"])}">{ui.icon("copy")}<span>שכפול</span></button>')
        if not is_def:
            menu += (f'<button class="menu-item" data-default="{_esc(d["id"])}">{ui.icon("star")}'
                     f'<span>הגדרה כנשלח ללקוחות</span></button><div class="menu-sep"></div>'
                     f'<button class="menu-item danger" data-delete="{_esc(d["id"])}" data-title="{_esc(d.get("title"))}">'
                     f'{ui.icon("trash")}<span>מחיקה</span></button>')
        cards += f"""<article class="card qcard reveal{' is-default' if is_def else ''}" style="--i:{i}">
          <div class="qcard-top"><span class="qcard-icon">{ui.icon("clipboard", 19)}</span>
            <div style="min-width:0"><h2 class="qcard-title"><a href="{base}/admin/questionnaires/{qid}">{_esc(d.get("title"))}</a></h2>
            <div class="qcard-meta"><span>{n} שאלות</span><span>{secs} חלקים</span>
              <span>עודכן {ui.when(d.get("updated_at"), "-")}</span></div></div>
            <span style="margin-inline-start:auto">{badge}</span></div>
          <div class="qcard-rate"><div class="row"><span>מולאו {answered} מתוך {sent}</span><span class="num">{rate}%</span></div>
            <div class="meter"><i style="--v:{rate}%"></i></div></div>
          <div class="qcard-foot">
            <a class="btn btn-primary btn-sm" href="{base}/admin/questionnaires/{qid}">{ui.icon("pen", 14)}<span>עריכה</span></a>
            <a class="btn btn-sm btn-icon" target="_blank" href="{base}/admin/questionnaires/{qid}/preview" data-tip="תצוגה מקדימה">{ui.icon("eye", 15)}</a>
            <a class="btn btn-sm btn-icon" href="{base}/admin/responses?q={qid}" data-tip="תשובות">{ui.icon("inbox", 15)}</a>
            <span class="spacer"></span>
            <details class="menu"><summary class="btn btn-ghost btn-sm btn-icon" aria-label="עוד">{ui.icon("more", 16)}</summary>
              <div class="menu-list">{menu}</div></details>
          </div></article>"""
    options = "".join(f'<option value="{_esc(d["id"])}" data-icon="copy" data-hint="עותק של השאלון הזה">{_esc(d.get("title"))}</option>'
                      for d in defs)
    body = (ui.page_head("שאלונים", 'השאלון המסומן "נשלח ללקוחות" יוצא אוטומטית אחרי חתימה. לקוח שכבר קיבל שאלון '
                         "ממשיך לראות את השאלון שקיבל.",
                         f'<button class="btn btn-primary" data-modal="newq">{ui.icon("plus")}<span>שאלון חדש</span></button>')
            + (f'<div class="qgrid">{cards}</div>' if cards else
               '<div class="card">' + ui.empty("עדיין אין שאלונים", ico="clipboard") + "</div>")
            + f"""<dialog class="modal" id="newq"><form id="newqform"><div class="modal-body">
              <div class="modal-icon">{ui.icon("plus", 20)}</div><h2 class="modal-title">שאלון חדש</h2>
              <p class="modal-text">תן לו שם. אפשר להתחיל מאפס או מעותק של שאלון קיים.</p>
              <div class="modal-fields"><label class="field"><span class="label">שם השאלון</span>
                <input class="input" id="newtitle" autofocus placeholder="למשל: שאלון היכרות לפני פגישה" required></label>
              <label class="field"><span class="label">להתחיל מ</span><select class="select" id="newfrom" data-picker aria-label="להתחיל מ">
                <option value="" data-icon="plus" data-hint="שלב אחד עם שאלה ראשונה">שאלון ריק</option>{options}</select></label></div></div>
              <div class="modal-foot"><button type="submit" class="btn btn-primary" id="create">{ui.icon("plus")}<span>יצירה</span></button>
              <button type="button" class="btn" data-close>ביטול</button></div></form></dialog>""")
    script = r"""
document.getElementById('newqform').addEventListener('submit', function (e) {
  e.preventDefault(); var b = document.getElementById('create'), t = document.getElementById('newtitle');
  if (!t.value.trim()) { t.classList.add('shake'); setTimeout(function () { t.classList.remove('shake'); }, 450); t.focus(); return; }
  UI.busy(b, true);
  UI.api('/questionnaires', {title: t.value, copy_from: document.getElementById('newfrom').value || null}).then(function (d) {
    if (d.id) location.href = BASE + '/admin/questionnaires/' + encodeURIComponent(d.id);
    else { UI.busy(b, false); UI.toast((d.errors || ['שגיאה']).join(' '), {kind: 'error'}); }
  });
});
document.querySelector('main.page').addEventListener('click', function (e) {
  var b = e.target.closest('button'); if (!b) return;
  if (b.dataset.copy) { UI.busy(b, true); UI.api('/questionnaires', {copy_from: b.dataset.copy}).then(function (d) {
    if (d.id) location.href = BASE + '/admin/questionnaires/' + encodeURIComponent(d.id); else UI.busy(b, false); }); }
  if (b.dataset.default) UI.api('/questionnaires/' + encodeURIComponent(b.dataset.default) + '/default').then(function (d) {
    if (d.ok) { UI.toast('מעכשיו זה השאלון שנשלח ללקוחות'); setTimeout(function () { location.reload(); }, 700); } });
  if (b.dataset.delete) UI.confirm({danger: true, title: 'למחוק את "' + b.dataset.title + '"?',
      text: 'השאלון יימחק. תשובות שכבר התקבלו נשמרות.', ok: 'מחיקה'}).then(function (yes) {
    if (!yes) return;
    UI.api('/questionnaires/' + encodeURIComponent(b.dataset.delete) + '/delete').then(function (d) {
      if (!d.ok) { UI.toast((d.errors || ['שגיאה']).join(' '), {kind: 'error'}); return; }
      var card = b.closest('.qcard'); card.classList.add('is-leaving');
      setTimeout(function () { card.remove(); }, 300); UI.toast('השאלון נמחק');
    });
  });
});
"""
    return _shell(base, "questionnaires", "שאלונים", body, script=script, spa=True)


EDITOR_JS = r"""
var data = JSON.parse(document.getElementById('qdata').textContent);
var meta = JSON.parse(document.getElementById('qmeta').textContent);
var defn = data, version = data.version || 0, dirty = false, fresh = null, moved = null;
var root = document.getElementById('editor'), collapsed = {};

function h(tag, attrs, kids) {
  var el = document.createElement(tag);
  Object.keys(attrs || {}).forEach(function (k) {
    if (k === 'text') el.textContent = attrs[k];
    else if (k === 'html') el.innerHTML = attrs[k];
    else if (k === 'value') el.value = attrs[k];
    else if (k === 'checked') el.checked = !!attrs[k];
    else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), attrs[k]);
    else el.setAttribute(k, attrs[k]);
  });
  (kids || []).forEach(function (c) { if (c) el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
  return el;
}
function key(prefix) { return prefix + '_' + Math.random().toString(16).slice(2, 8); }
function setState(s) {
  var st = document.getElementById('state');
  st.className = 'save-state' + (s === 'dirty' ? ' is-dirty' : s === 'saving' ? ' is-saving' : '');
  st.lastChild.textContent = s === 'dirty' ? 'שינויים שלא נשמרו' : s === 'saving' ? 'שומר…' : 'כל השינויים נשמרו';
}
function setDirty(v) { dirty = v; setState(v ? 'dirty' : 'saved'); document.getElementById('save').disabled = !v; }
function input(obj, prop, cls, placeholder, area) {
  var el = area ? h('textarea', {class: 'textarea', rows: 2}) : h('input', {class: cls || 'input'});
  el.value = obj[prop] || ''; if (placeholder) el.placeholder = placeholder;
  el.addEventListener('input', function () { obj[prop] = el.value; setDirty(true);
    if (obj === defn && prop === 'title') document.getElementById('edtitle').textContent = el.value || 'שאלון ללא שם'; });
  return el;
}
function field(label, control, hint) {
  return h('label', {class: 'field'}, [h('span', {class: 'label', text: label}), control, hint ? h('span', {class: 'hint', text: hint}) : null]);
}
function move(list, i, d) { var j = i + d; if (j < 0 || j >= list.length) return false;
  var t = list[i]; list[i] = list[j]; list[j] = t; return true; }
function iconBtn(icon, tip, fn, cls) {
  var b = h('button', {type: 'button', class: 'btn btn-ghost btn-sm btn-icon' + (cls ? ' ' + cls : ''), 'data-tip': tip, 'aria-label': tip, html: UI.icon(icon)});
  b.addEventListener('click', fn); return b;
}
function change() { setDirty(true); render(); }
function grip(cls, label) { return h('button', {type: 'button', class: 'drag-handle ' + cls, 'aria-label': label, title: label, html: UI.icon('grip')}); }
function snapshot() { return JSON.stringify(defn.sections); }
function undoable(label, before) {
  UI.toast(label, {icon: 'trash', action: {label: 'ביטול', run: function () { defn.sections = JSON.parse(before); change(); UI.toast('שוחזר'); }}});
}
function newQuestion() { return {key: key('q'), label: '', kind: 'text', required: false, hint: '', role: '', options: [], scale_max: 10}; }

function optionsEditor(q) {
  var box = h('div', {class: 'opts'});
  (q.options || []).forEach(function (o, oi) {
    var inp = h('input', {class: 'input', value: o, placeholder: 'אפשרות ' + (oi + 1)});
    inp.addEventListener('input', function () { q.options[oi] = inp.value; setDirty(true); });
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); q.options.splice(oi + 1, 0, ''); fresh = q.key + ':' + (oi + 1); change(); }
      if (e.key === 'Backspace' && !inp.value && q.options.length > 1) { e.preventDefault(); q.options.splice(oi, 1);
        fresh = q.key + ':' + Math.max(0, oi - 1); change(); }
    });
    inp.setAttribute('data-opt', q.key + ':' + oi);
    box.appendChild(h('div', {class: 'opt'}, [h('span', {class: 'mark' + (q.kind === 'multi' ? ' sq' : '')}), inp,
      iconBtn('x', 'הסרה', function () { q.options.splice(oi, 1); change(); })]));
  });
  var add = h('button', {type: 'button', class: 'btn btn-ghost btn-sm', html: UI.icon('plus') + '<span>הוספת אפשרות</span>'});
  add.addEventListener('click', function () { q.options = q.options || []; q.options.push(''); fresh = q.key + ':' + (q.options.length - 1); change(); });
  box.appendChild(h('div', {}, [add]));
  return field('אפשרויות', box, 'Enter מוסיף אפשרות חדשה');
}

function questionCard(section, si, q, qi) {
  var list = section.questions;
  var type = UI.picker({value: q.kind, options: meta.kindOptions, label: 'סוג שאלה', onChange: function (v) {
    q.kind = v; if (q.kind !== 'url') q.role = '';
    if ((q.kind === 'choice' || q.kind === 'multi') && !(q.options || []).filter(Boolean).length) q.options = ['אפשרות א', 'אפשרות ב'];
    change(); }});
  var req = h('label', {class: 'switch'}, [h('input', {type: 'checkbox', checked: q.required,
    onchange: function (e) { q.required = e.target.checked; setDirty(true); }}), 'חובה']);
  var label = input(q, 'label', 'inline-input lg', 'נוסח השאלה');
  label.setAttribute('data-label', q.key);
  var extra = h('div', {class: 'ed-q-extra'}, [input(q, 'hint', 'inline-input sm', 'הסבר קטן מתחת לשאלה (לא חובה)')]);
  if (q.kind === 'choice' || q.kind === 'multi') extra.appendChild(optionsEditor(q));
  if (q.kind === 'scale') {
    var sm = UI.picker({value: q.scale_max || 10, label: 'טווח הסולם', options: [5, 7, 10].map(function (n) {
      return {value: n, label: '1 עד ' + n, icon: 'gauge', hint: n === 10 ? 'הנפוץ ביותר' : ''}; }),
      onChange: function (v) { q.scale_max = parseInt(v, 10); setDirty(true); }});
    extra.appendChild(field('טווח הסולם', sm));
  }
  if (q.kind === 'url') {
    var role = UI.picker({value: q.role || '', options: meta.roleOptions, label: 'סוג הקישור',
      onChange: function (v) { q.role = v; setDirty(true); }});
    extra.appendChild(field('ה-AI ינתח את הקישור הזה בתור', role));
  }
  var tools = h('div', {class: 'ed-tools'}, [
    iconBtn('arrow-up', 'למעלה', function () {
      if (qi > 0) move(list, qi, -1); else if (si > 0) defn.sections[si - 1].questions.push(list.splice(qi, 1)[0]); else return;
      moved = q.key; change(); }),
    iconBtn('arrow-down', 'למטה', function () {
      if (qi < list.length - 1) move(list, qi, 1); else if (si < defn.sections.length - 1) defn.sections[si + 1].questions.unshift(list.splice(qi, 1)[0]); else return;
      moved = q.key; change(); }),
    iconBtn('copy', 'שכפול', function () { var c = JSON.parse(JSON.stringify(q)); c.key = key('q'); c.label = c.label + ' (עותק)';
      list.splice(qi + 1, 0, c); fresh = c.key; change(); }),
    iconBtn('trash', 'מחיקה', function () { var before = snapshot(); list.splice(qi, 1); change();
      undoable('השאלה נמחקה', before); }, 'btn-danger'),
  ]);
  return h('div', {class: 'ed-q', 'data-key': q.key}, [
    h('div', {class: 'ed-q-main'}, [grip('q-grip', 'גרירה לשינוי מיקום השאלה'),
      h('span', {class: 'kind-icon', html: UI.icon(meta.icons[q.kind] || 'type')}), label, type, req]),
    extra,
    h('div', {class: 'ed-q-foot'}, [h('span', {class: 'ed-key', text: q.key, title: 'המזהה הקבוע של השאלה'}), h('span', {class: 'spacer'}), tools]),
  ]);
}

function render() {
  root.textContent = '';
  var settings = h('div', {class: 'card ed-card'}, [
    field('שם השאלון', input(defn, 'title', 'inline-input xl', 'שם השאלון'), 'הכותרת שהלקוח רואה'),
    h('div', {class: 'ed-grid2'}, [field('פתיח', input(defn, 'intro', '', 'מופיע מתחת לכותרת', true)),
                                   field('הודעת תודה', input(defn, 'thanks', '', 'מופיעה אחרי השליחה', true))]),
  ]);
  root.appendChild(settings);
  defn.sections.forEach(function (s, si) {
    var sec = h('section', {class: 'card ed-section' + (collapsed[s.id] ? ' is-collapsed' : ''), 'data-key': s.id});
    var collapse = iconBtn('chevron-down', collapsed[s.id] ? 'פתיחה' : 'כיווץ', function () { collapsed[s.id] = !collapsed[s.id]; render(); }, 'collapse');
    sec.appendChild(h('div', {class: 'ed-section-head'}, [
      grip('sec-grip', 'גרירה לשינוי מיקום החלק'),
      h('span', {class: 'ed-num', text: String(si + 1)}),
      h('div', {class: 'ed-section-titles'}, [input(s, 'title', 'inline-input lg', 'כותרת החלק'),
                                             input(s, 'description', 'inline-input sm', 'תיאור קצר לחלק (לא חובה)')]),
      h('span', {class: 'badge hide-sm', text: s.questions.length + ' שאלות', style: 'margin-top:8px'}),
      h('div', {class: 'ed-tools'}, [
        iconBtn('arrow-up', 'הזזת החלק למעלה', function () { if (move(defn.sections, si, -1)) { moved = s.id; change(); } }),
        iconBtn('arrow-down', 'הזזת החלק למטה', function () { if (move(defn.sections, si, 1)) { moved = s.id; change(); } }),
        iconBtn('trash', 'מחיקת החלק', function () {
          UI.confirm({danger: true, title: 'למחוק את החלק "' + (s.title || '') + '"?', text: 'כל ' + s.questions.length +
            ' השאלות בו יימחקו. תשובות שכבר התקבלו נשמרות.', ok: 'מחיקה'}).then(function (yes) {
            if (!yes) return; var before = snapshot(); defn.sections.splice(si, 1); change(); undoable('החלק נמחק', before); }); }, 'btn-danger'),
        collapse,
      ]),
    ]));
    var body = h('div', {class: 'ed-section-body'});
    s.questions.forEach(function (q, qi) { body.appendChild(questionCard(s, si, q, qi)); });
    var add = h('button', {type: 'button', class: 'add-row', html: UI.icon('plus') + '<span>הוספת שאלה</span>'});
    add.addEventListener('click', function () { var q = newQuestion(); s.questions.push(q); fresh = q.key; change(); });
    body.appendChild(add); sec.appendChild(body); root.appendChild(sec);
  });
  var addSec = h('button', {type: 'button', class: 'add-row big', html: UI.icon('plus') + '<span>חלק חדש (שלב נוסף בטופס)</span>'});
  addSec.addEventListener('click', function () { var s = {id: key('s'), title: 'חלק חדש', description: '', questions: [newQuestion()]};
    defn.sections.push(s); fresh = s.id; change(); });
  root.appendChild(addSec);
  if (fresh) {
    var opt = root.querySelector('[data-opt="' + fresh + '"]');
    var node = opt || root.querySelector('[data-key="' + fresh + '"]');
    if (node) { if (!opt) node.classList.add('is-new'); var f = opt || node.querySelector('[data-label], .inline-input');
      if (f) { f.focus(); if (!opt) node.scrollIntoView({block: 'nearest', behavior: 'smooth'}); } }
    fresh = null;
  }
  if (moved) { var m = root.querySelector('[data-key="' + moved + '"]');
    if (m) { m.classList.add('flash'); m.scrollIntoView({block: 'nearest', behavior: 'smooth'}); } moved = null; }
}

function showErrors(list) {
  var box = document.getElementById('errors');
  box.hidden = !list.length; box.innerHTML = '';
  if (!list.length) return;
  box.innerHTML = UI.icon('alert');
  box.appendChild(h('div', {}, [h('b', {text: 'לא נשמר. צריך לתקן:'}), h('ul', {}, list.map(function (e) { return h('li', {text: e}); }))]));
  box.classList.remove('shake'); void box.offsetWidth; box.classList.add('shake');
  window.scrollTo({top: 0, behavior: 'smooth'});
}
function clean() {
  defn.sections.forEach(function (s) { s.questions.forEach(function (q) {
    if (q.options) q.options = q.options.map(function (o) { return (o || '').trim(); }).filter(Boolean); }); });
}
function save() {
  var b = document.getElementById('save'); clean(); UI.busy(b, true); setState('saving');
  UI.api('/questionnaires/' + encodeURIComponent(defn.id), {definition: defn, version: version}).then(function (d) {
    UI.busy(b, false);
    if (d._status === 200) { version = d.version; showErrors([]); setDirty(false); b.disabled = false; UI.done(b, 'נשמר');
      setTimeout(function () { b.disabled = !dirty; }, 1500); render(); }
    else if (d._status === 409) { setState('dirty'); showErrors(['השאלון נשמר בינתיים ממקום אחר. העתק את השינויים שלך, רענן את הדף והחל אותם שוב.']); }
    else { setState('dirty'); showErrors(d.errors || ['שגיאה בשמירה (' + d._status + ')']); }
  });
}
document.getElementById('save').onclick = save;
document.addEventListener('keydown', function (e) {
  if ((e.metaKey || e.ctrlKey) && e.key === 's') { e.preventDefault(); if (dirty) save(); } });
window.addEventListener('beforeunload', function (e) { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
render(); setDirty(false);

// Drag a question (also into another section) or a whole section; the others slide.
UI.sortable({root: root, item: '.ed-q', handle: '.q-grip', tail: '.add-row',
  lists: function () { return Array.prototype.slice.call(root.querySelectorAll('.ed-section-body')).filter(function (l) { return l.offsetParent; }); },
  onEnd: function (item, changed) {
    if (!changed) return;
    var byKey = {};
    defn.sections.forEach(function (s) { s.questions.forEach(function (q) { byKey[q.key] = q; }); });
    root.querySelectorAll('.ed-section').forEach(function (node) {
      var s = defn.sections.filter(function (x) { return x.id === node.getAttribute('data-key'); })[0]; if (!s) return;
      s.questions = Array.prototype.map.call(node.querySelectorAll('.ed-q'), function (n) { return byKey[n.getAttribute('data-key')]; });
    });
    moved = item.getAttribute('data-key'); change();
  }});
var before = null;
UI.sortable({root: root, item: '.ed-section', handle: '.sec-grip', tail: '.add-row.big',
  lists: function () { return [root]; },
  // Whole sections are tall: fold them all while one is being dragged.
  onStart: function () { before = JSON.stringify(collapsed); root.querySelectorAll('.ed-section').forEach(function (n) { n.classList.add('is-collapsed'); }); },
  onEnd: function (item, changed) {
    collapsed = JSON.parse(before || '{}');
    if (!changed) { render(); return; }
    var order = Array.prototype.map.call(root.querySelectorAll('.ed-section'), function (n) { return n.getAttribute('data-key'); });
    defn.sections.sort(function (a, b) { return order.indexOf(a.id) - order.indexOf(b.id); });
    moved = item.getAttribute('data-key'); change();
  }});
"""


#: The question-type picker: grouped, each with what it is for.
_KIND_OPTIONS = [
    {"value": k, "label": questionnaire.KINDS[k], "icon": ui.KIND_ICONS[k], "hint": hint, "group": group}
    for group, items in (
        ("טקסט חופשי", (("text", "שורה אחת, כמו שם או תחום"), ("textarea", "כמה שורות, לתשובה מפורטת"),
                        ("number", "ערך מספרי, כמו תקציב"))),
        ("בחירה", (("choice", "הלקוח בוחר אפשרות אחת"), ("multi", "אפשר לסמן כמה אפשרויות"),
                   ("scale", "דירוג מספרי, למשל 1 עד 10"))),
        ("פרטי קשר וקישורים", (("email", "כתובת מייל תקינה"), ("tel", "מספר טלפון"),
                                ("url", "אתר או רשת חברתית: ה-AI יכול לנתח"))),
    ) for k, hint in items]

_ROLE_ICONS = {"": "ban", "instagram": "instagram", "tiktok": "music", "facebook": "facebook",
               "youtube": "youtube", "linkedin": "linkedin", "website": "globe"}
_ROLE_OPTIONS = [{"value": k, "label": "לא משמש לניתוח" if not k else v, "icon": _ROLE_ICONS.get(k, "link"),
                  "hint": "" if k else "ה-AI לא ייכנס לקישור הזה"} for k, v in questionnaire.ROLES.items()]


def page_editor(base: str, qid: str) -> Response:
    defn = questionnaire_store.get_definition(qid)
    if not defn:
        return Response(404, "לא נמצא")
    is_default = qid == questionnaire_store.default_id()
    badge = (f'<span class="badge badge-brand hide-sm">{ui.icon("star", 11)}<span>נשלח ללקוחות</span></span>'
             if is_default else "")
    body = (f'<div class="ed-bar"><div class="ed-bar-in">'
            f'<a class="btn btn-ghost btn-sm btn-icon" href="{base}/admin/questionnaires" data-tip="כל השאלונים">{ui.icon("arrow-right", 16)}</a>'
            f'<h1 id="edtitle">{_esc(defn.get("title"))}</h1>{badge}<span class="spacer"></span>'
            '<span class="save-state" id="state"><i></i><span></span></span>'
            f'<a class="btn btn-sm" target="_blank" href="{base}/admin/questionnaires/{quote(qid)}/preview">{ui.icon("eye", 15)}'
            '<span class="hide-sm">תצוגה מקדימה</span></a>'
            f'<button class="btn btn-primary btn-sm" id="save" disabled>{ui.icon("check", 15)}<span>שמירה</span>'
            '<span class="kbd hide-sm">⌘S</span></button></div></div>'
            f'<div class="alert alert-danger" id="errors" hidden style="margin-bottom:16px"></div>'
            + (f'<div class="alert alert-info reveal" style="margin-bottom:16px">{ui.icon("info")}<span>זה השאלון שנשלח ללקוחות. '
               'שינוי שנשמר חל מיד על כל מי שיפתח אותו, גם על מי שכבר קיבל קישור. תשובות שכבר התקבלו נשמרות כפי שהיו.</span></div>'
               if is_default else "")
            + '<div id="editor"></div>'
            + _data_tag("qdata", defn)
            + _data_tag("qmeta", {"kinds": questionnaire.KINDS, "icons": ui.KIND_ICONS,
                                  "kindOptions": _KIND_OPTIONS, "roleOptions": _ROLE_OPTIONS}))
    return _shell(base, "questionnaires", f"עריכה · {defn.get('title')}", body, script=EDITOR_JS, narrow=True)


def page_responses(base: str, qid: str = "") -> Response:
    defs = {d["id"]: d for d in questionnaire_store.list_definitions()}
    rows_data = questionnaire_store.list_responses(qid or None)
    answered_n = sum(1 for r in rows_data if r.get("status") == "answered")
    waiting_n = len(rows_data) - answered_n
    rate = round(100 * answered_n / len(rows_data)) if rows_data else 0
    options = '<option value="" data-icon="inbox">כל השאלונים</option>' + "".join(
        f'<option value="{_esc(i)}" data-icon="clipboard"{" selected" if i == qid else ""}>{_esc(d.get("title"))}</option>'
        for i, d in defs.items())
    q_options = "".join(f'<option value="{_esc(i)}" data-icon="clipboard"{" selected" if i == (qid or questionnaire_store.default_id()) else ""}>'
                        f'{_esc(d.get("title"))}</option>' for i, d in defs.items())
    rows = ""
    for r in rows_data:
        answered = r.get("status") == "answered"
        days = _days_since(r.get("sent_at"))
        status = (f'<span class="badge badge-ok badge-dot">מולא</span>' if answered
                  else f'<span class="badge badge-warn badge-dot">ממתין</span>')
        waiting = ("" if answered else
                   f'<span class="{"badge badge-err" if (days or 0) >= 7 else "badge" if (days or 0) >= 3 else "muted"} num">'
                   f'{"היום" if not days else "יום אחד" if days == 1 else f"{days} ימים"}</span>')
        href = f'{base}/admin/responses/{quote(str(r.get("client_id")))}/{quote(str(r.get("questionnaire_id")))}'
        name = str(r.get("client_name") or r.get("client_id") or "")
        rows += (f'<tr data-href="{_esc(href)}" data-status="{"answered" if answered else "waiting"}" data-name="{_esc(name.lower())}">'
                 f'<td data-v="{_esc(name)}"><div class="person"><span class="avatar">{_esc(ui.initials(name))}</span>'
                 f'<a class="cell-strong" href="{_esc(href)}">{_esc(name)}</a></div></td>'
                 f'<td class="hide-sm">{_esc(r.get("questionnaire_title"))}</td>'
                 f'<td data-v="{"מולא" if answered else "ממתין"}">{status}</td>'
                 f'<td class="muted hide-sm" data-v="{_ms(r.get("sent_at"))}">{ui.when(r.get("sent_at"), "-")}</td>'
                 f'<td class="muted" data-v="{_ms(r.get("answered_at"))}">{ui.when(r.get("answered_at"), "-")}</td>'
                 f'<td data-v="{-1 if answered else (days or 0)}">{waiting}</td>'
                 f'<td style="width:1%">{ui.icon("chevron-left", 16, cls="row-go")}</td></tr>')
    export = (f'<a class="btn" href="{base}/admin/questionnaires/{quote(qid)}/export.csv">{ui.icon("download")}'
              '<span>ייצוא ל-Excel</span></a>' if qid else "")
    actions = (f'<select class="select" id="filter" data-picker="inline" aria-label="שאלון">{options}</select>{export}'
               f'<button class="btn btn-primary" data-modal="linkmodal">{ui.icon("link")}<span>קישור ללקוח</span></button>')
    head = ui.page_head("תשובות", "מי קיבל איזה שאלון, מי מילא ומי עדיין ממתין.", actions)
    stats = (ui.stat("נשלחו", len(rows_data), ico="send", tone="brand", i=0)
             + ui.stat("מולאו", answered_n, ico="check-circle", tone="ok", i=1)
             + ui.stat("ממתינים", waiting_n, ico="hourglass", tone="warn", i=2)
             + ui.stat("אחוז מענה", rate, ico="percent", suffix="%", i=3))
    if rows:
        table = (f"""<div class="toolbar reveal" style="--i:4"><div class="segmented" id="seg">
            <button class="is-active" data-f="">הכל <span class="count">{len(rows_data)}</span></button>
            <button data-f="answered">מולאו <span class="count">{answered_n}</span></button>
            <button data-f="waiting">ממתינים <span class="count">{waiting_n}</span></button></div>
            <label class="with-icon grow">{ui.icon("search", 16)}<input class="input" type="search" id="find" data-search
              placeholder="חיפוש לקוח" aria-label="חיפוש לקוח"><span class="kbd">/</span></label></div>
            <div class="table-wrap reveal" style="--i:5"><table class="table" data-sortable><thead><tr><th data-sort>לקוח</th>
            <th class="hide-sm" data-sort>שאלון</th><th data-sort>סטטוס</th>
            <th class="hide-sm" data-sort="num" data-first="descending" aria-sort="descending">נשלח</th>
            <th data-sort="num" data-first="descending">מולא</th><th data-sort="num" data-first="descending">ממתין</th><th></th></tr></thead>
            <tbody>{rows}</tbody></table>
            <div class="no-results" id="none">{ui.empty("אין תוצאות", "נסה חיפוש אחר או סינון אחר.", ico="search")}</div></div>""")
    else:
        table = '<div class="card">' + ui.empty(
            "עדיין לא נשלח שאלון לאף לקוח", "כשלקוח חותם, השאלון נשלח אליו אוטומטית. אפשר גם ליצור קישור ידנית.",
            action=f'<button class="btn btn-primary" data-modal="linkmodal">{ui.icon("link")}<span>קישור ללקוח</span></button>') + "</div>"
    modal = f"""<dialog class="modal" id="linkmodal"><form id="linkform"><div class="modal-body">
      <div class="modal-icon">{ui.icon("link", 20)}</div><h2 class="modal-title">קישור לשאלון</h2>
      <p class="modal-text">קישור אישי ללקוח: לבדיקה, או כדי לשלוח בעצמך. שום דבר לא נשלח מכאן.</p>
      <div class="modal-fields" id="linkfields"><label class="field"><span class="label">לקוח</span>
        <select class="select" id="client" required disabled data-picker="search" data-placeholder="טוען לקוחות מ-ClickUp…"
          aria-label="לקוח"></select></label>
        <label class="field"><span class="label">שאלון</span><select class="select" id="lq" data-picker aria-label="שאלון">{q_options}</select></label></div>
      <div class="linkbox" id="linkbox" hidden style="margin-top:18px"></div></div>
      <div class="modal-foot"><button type="submit" class="btn btn-primary" id="mklink">{ui.icon("link")}<span>יצירת קישור</span></button>
      <button type="button" class="btn" data-close>סגירה</button></div></form></dialog>"""
    script = LINK_JS + r"""
document.getElementById('filter').onchange = function (e) {
  location.href = BASE + '/admin/responses' + (e.target.value ? '?q=' + encodeURIComponent(e.target.value) : ''); };
var seg = document.getElementById('seg'), find = document.getElementById('find'), state = '';
function applyFilter() {
  var needle = (find ? find.value : '').trim().toLowerCase(), shown = 0;
  document.querySelectorAll('tr[data-status]').forEach(function (tr) {
    var ok = (!state || tr.dataset.status === state) && (!needle || tr.dataset.name.indexOf(needle) >= 0);
    tr.hidden = !ok; if (ok) shown++; });
  var none = document.getElementById('none'); if (none) none.style.display = shown ? 'none' : 'block';
}
if (seg) seg.addEventListener('click', function (e) { var b = e.target.closest('button'); if (!b) return;
  seg.querySelectorAll('button').forEach(function (x) { x.classList.toggle('is-active', x === b); });
  state = b.dataset.f; applyFilter(); });
if (find) find.addEventListener('input', applyFilter);
var loaded = false, names = {};
function loadClients() {
  if (loaded) return; loaded = true;
  UI.api('/clients').then(function (d) {
    var sel = document.getElementById('client'); sel.textContent = '';
    (d.clients || []).forEach(function (c) {
      names[c.id] = c.name; var op = new Option(c.name, c.id);
      op.dataset.avatar = c.name.split(/\s+/).filter(Boolean).slice(0, 2).map(function (w) { return w[0]; }).join('');
      var st = {active: 'לקוח פעיל', lead: 'ליד', paused: 'מושהה', finished: 'הסתיים'}[String(c.status).toLowerCase()] || c.status;
      if (st) op.dataset.hint = st; sel.appendChild(op); });
    sel.value = ''; sel.disabled = false;
    sel.setAttribute('data-placeholder', 'בחירת לקוח');
    var pk = sel._picker; if (pk) { pk.remove(); sel._picker = null; } pk = UI.enhance(sel); pk.focus();
    if (d.error) UI.toast(d.error, {kind: 'error'});
  });
}
document.querySelectorAll('[data-modal=linkmodal]').forEach(function (b) { b.addEventListener('click', loadClients); });
document.getElementById('linkform').addEventListener('submit', function (e) {
  e.preventDefault(); var b = document.getElementById('mklink'), cid = document.getElementById('client').value;
  if (!cid) { var s = document.getElementById('client')._picker || document.getElementById('client'); s.classList.add('shake');
    setTimeout(function () { s.classList.remove('shake'); }, 450); s.click(); return; }
  UI.busy(b, true);
  UI.api('/links', {client_id: cid, questionnaire_id: document.getElementById('lq').value}).then(function (d) {
    UI.busy(b, false);
    if (!d.url) { UI.toast(d.error || 'שגיאה', {kind: 'error'}); return; }
    showLink(document.getElementById('linkbox'), d.url, names[cid]);
  });
});
"""
    body = head + f'<div class="stats">{stats}</div>' + table + modal
    return _shell(base, "responses", "תשובות לשאלונים", body, script=script, spa=True, fill=True)


_EVENTS = {"answered": "הלקוח מילא את השאלון", "updated": "הלקוח עדכן את התשובות",
           "sent:email": "השאלון נשלח במייל", "sent:link": "נוצר קישור לשאלון"}


def page_response(base: str, client_id: str, qid: str) -> Response:
    r = questionnaire_store.get_response(client_id, qid)
    if not r:
        return Response(404, "לא נמצא")
    answered = r.get("status") == "answered"
    answers = r.get("answers") or {}
    name = str(r.get("client_name") or client_id)
    sections: list[tuple[str, list[str]]] = []
    text_lines: list[str] = []
    for q in r.get("snapshot") or []:
        value = questionnaire.display(answers.get(q["key"]))
        if not sections or sections[-1][0] != q.get("section"):
            sections.append((q.get("section") or "", []))
            text_lines.append(f"\n{q.get('section') or ''}")
        rendered = (f'<dd>{_linkify(value)}</dd>' if value else '<dd class="none">לא נענה</dd>')
        sections[-1][1].append(f'<div class="qa"><dt>{_esc(q["label"])}</dt>{rendered}</div>')
        text_lines.append(f"{q['label']}\n{value or '-'}")
    answers_html = "".join(
        f'<section class="card answers-sec reveal" style="--i:{n + 1}"><div class="card-head"><h2>{_esc(title)}</h2></div>'
        f'<dl style="margin:0">{"".join(items)}</dl></section>' for n, (title, items) in enumerate(sections))
    if not answered:
        answers_html = '<div class="card">' + ui.empty(
            "הלקוח עוד לא מילא את השאלון", "אפשר ליצור קישור חדש ולשלוח לו אותו שוב.", ico="hourglass",
            action=f'<button class="btn btn-primary" id="mklink2">{ui.icon("link")}<span>קישור חדש</span></button>') + "</div>"
    history = "".join(
        f'<li>{_esc(_EVENTS.get(str(h.get("event")), str(h.get("event") or "")))}{ui.when(h.get("at"))}</li>'
        for h in reversed(r.get("history") or []))
    doc = (f'<a class="btn" target="_blank" rel="noopener" href="{_esc(r["doc_url"])}">{ui.icon("file")}'
           '<span>המסמך בדרייב</span></a>' if r.get("doc_url") else "")
    copy_btn = (f'<button class="btn" id="copyall">{ui.icon("copy")}<span>העתקת התשובות</span></button>' if answered else "")
    status = ('<span class="badge badge-ok badge-dot">מולא</span>' if answered
              else '<span class="badge badge-warn badge-dot">ממתין</span>')
    body = f"""<a class="back" href="{base}/admin/responses">{ui.icon("arrow-right", 15)}<span>כל התשובות</span></a>
      <div class="resp-head reveal"><span class="avatar lg">{_esc(ui.initials(name))}</span>
        <div style="min-width:0"><h1>{_esc(name)} {status}</h1>
        <div class="resp-meta"><span>{ui.icon("clipboard", 14)}{_esc(r.get('questionnaire_title'))}</span>
          <span>{ui.icon("send", 14)}נשלח {ui.when(r.get('sent_at'), '-')}</span>
          <span>{ui.icon("check-circle", 14)}מולא {ui.when(r.get('answered_at'), '-')}</span></div></div>
        <div class="page-actions"><a class="btn" href="{base}/clients/{quote(client_id)}">{ui.icon("users")}<span>כרטיס לקוח</span></a>{doc}{copy_btn}<button class="btn btn-primary" id="mklink">{ui.icon("link")}<span>קישור חדש</span></button></div></div>
      <div class="card card-pad linkbox" id="linkbox" hidden style="margin-bottom:16px"></div>
      {answers_html}
      <section class="card reveal" style="margin-top:22px;--i:9"><div class="card-head"><h2 class="card-title">היסטוריה</h2></div>
        <ul class="timeline">{history or '<li>-</li>'}</ul></section>
      {_data_tag('ctx', {'client_id': client_id, 'questionnaire_id': qid, 'name': name, 'text': '\n\n'.join(text_lines).strip()})}"""
    script = LINK_JS + r"""
var ctx = JSON.parse(document.getElementById('ctx').textContent);
function mk(b) { UI.busy(b, true);
  UI.api('/links', {client_id: ctx.client_id, questionnaire_id: ctx.questionnaire_id}).then(function (d) {
    UI.busy(b, false); if (!d.url) { UI.toast(d.error || 'שגיאה', {kind: 'error'}); return; }
    var box = document.getElementById('linkbox'); showLink(box, d.url, ctx.name); box.scrollIntoView({block: 'nearest', behavior: 'smooth'}); }); }
document.getElementById('mklink').onclick = function () { mk(this); };
var m2 = document.getElementById('mklink2'); if (m2) m2.onclick = function () { mk(this); };
var ca = document.getElementById('copyall'); if (ca) ca.onclick = function () { UI.copy(ctx.name + '\n' + ctx.text, this); };
"""
    return _shell(base, "responses", f"תשובות · {name}", body, script=script, narrow=True)


_URL = re.compile(r"https?://[^\s<>\"']+")


def _linkify(value: str) -> str:
    """Escape an answer and make its links clickable (they are what the AI reads)."""
    out, pos = [], 0
    for m in _URL.finditer(value):
        out.append(_esc(value[pos:m.start()]))
        url = m.group(0)
        out.append(f'<a href="{_esc(url)}" target="_blank" rel="noopener noreferrer"><bdi>{_esc(url)}</bdi></a>')
        pos = m.end()
    out.append(_esc(value[pos:]))
    return "".join(out)


def export_csv(qid: str) -> Response:
    defn = questionnaire_store.get_definition(qid)
    rows = questionnaire_store.list_responses(qid)
    columns: list[tuple[str, str]] = [(q["key"], q["label"]) for _s, q in questionnaire.questions(defn or {})]
    known = {k for k, _ in columns}
    for r in rows:  # questions that were removed since some answered them
        for q in r.get("snapshot") or []:
            if q["key"] not in known:
                columns.append((q["key"], q["label"])); known.add(q["key"])
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["לקוח", "מזהה לקוח", "סטטוס", "נשלח", "מולא"] + [label for _k, label in columns])
    for r in rows:
        answers = r.get("answers") or {}
        writer.writerow([r.get("client_name"), r.get("client_id"),
                         "מולא" if r.get("status") == "answered" else "ממתין",
                         _local(r.get("sent_at")), _local(r.get("answered_at"))]
                        + [questionnaire.display(answers.get(k)) for k, _l in columns])
    # A BOM so Excel opens Hebrew as Hebrew.
    name = re.sub(r"[^\w-]+", "_", str((defn or {}).get("title") or qid))
    return Response(200, "﻿" + out.getvalue(), "text/csv; charset=utf-8",
                    disposition=f"attachment; filename*=UTF-8''{quote(name)}.csv")


# -------------------------------------------------------------------- API


def _clients(dry_run: bool) -> list[dict[str, str]]:
    if dry_run:  # the sample clients of the dry-run dashboard, and the tests' client 42
        return [{"id": "42", "name": "מכללת דוגמה", "status": "active"},
                {"id": "מכללת אלפא", "name": "מכללת אלפא", "status": "active"},
                {"id": "מכללת בטא", "name": "מכללת בטא", "status": "lead"}]
    from .lib.clients.crm import CrmClient

    clients = CrmClient()._all_clients()
    return sorted(({"id": str(c.get("id")), "name": str(c.get("name") or c.get("id")),
                    "status": str(c.get("status") or "")} for c in clients), key=lambda c: c["name"])


def api(method: str, parts: list[str], body: dict[str, Any], *, dry_run: bool = False) -> Response:
    try:
        if parts == ["clients"] and method == "POST":
            try:
                return _json(200, {"clients": _clients(dry_run)})
            except Exception as exc:  # noqa: BLE001
                return _json(200, {"clients": [], "error": f"לא הצלחתי לטעון לקוחות מ-ClickUp: {exc}"})
        if parts == ["questionnaires"] and method == "POST":
            defn = questionnaire_store.create_definition(str(body.get("title") or ""),
                                                         copy_from=body.get("copy_from") or None)
            return _json(200, {"id": defn["id"]})
        if len(parts) == 2 and parts[0] == "questionnaires" and method == "POST":
            defn = dict(body.get("definition") or {})
            defn["id"] = parts[1]
            saved = questionnaire_store.save_definition(defn, expected_version=int(body.get("version") or 0))
            log.info("questionnaire_saved", extra={"id": saved["id"], "version": saved["version"]})
            return _json(200, {"ok": True, "version": saved["version"]})
        if len(parts) == 3 and parts[0] == "questionnaires" and parts[2] == "default":
            questionnaire_store.set_default(parts[1])
            return _json(200, {"ok": True})
        if len(parts) == 3 and parts[0] == "questionnaires" and parts[2] == "delete":
            questionnaire_store.delete_definition(parts[1])
            return _json(200, {"ok": True})
        if parts == ["links"] and method == "POST":
            client_id = str(body.get("client_id") or "").strip()
            qid = str(body.get("questionnaire_id") or "") or questionnaire_store.default_id()
            if not client_id or not questionnaire_store.get_definition(qid):
                return _json(400, {"error": "חסר לקוח או שאלון"})
            name = next((c["name"] for c in _clients(dry_run) if c["id"] == client_id), client_id) \
                if body.get("client_name") is None else str(body["client_name"])
            questionnaire_store.record_sent(client_id, name, qid, via="link")
            return _json(200, {"url": signing.questionnaire_url(client_id)})
    except questionnaire_store.Conflict:
        return _json(409, {"errors": ["השאלון שונה בינתיים"]})
    except questionnaire_store.Invalid as exc:
        return _json(422, {"errors": exc.errors})
    except KeyError:
        return _json(404, {"errors": ["לא נמצא"]})
    return _json(404, {"errors": ["לא נמצא"]})


# ----------------------------------------------------------------- router


def handle(method: str, route: str, query: dict[str, str], body: bytes, headers: dict[str, str],
           *, base: str = "", dry_run: bool = False) -> Response:
    """Route one authenticated request under ``/admin``. The caller checked the session."""
    method = method.upper()
    parts = [p for p in route.split("/") if p][1:]  # drop "admin"
    if parts[:1] == ["api"]:
        if method != "POST":
            return _json(405, {"errors": ["method"]})
        if headers.get("x-requested-with") != "dashboard":
            return _json(403, {"errors": ["missing X-Requested-With"]})
        try:
            data = json.loads(body.decode("utf-8") or "{}") if body else {}
        except (ValueError, UnicodeDecodeError):
            return _json(400, {"errors": ["גוף הבקשה אינו JSON"]})
        return api(method, parts[1:], data if isinstance(data, dict) else {}, dry_run=dry_run)
    if method != "GET":
        return Response(405, "method not allowed")
    if not parts:
        return Response(303, "", location=f"{base}/admin/questionnaires")
    if parts == ["questionnaires"]:
        return page_list(base)
    if len(parts) == 2 and parts[0] == "questionnaires":
        return page_editor(base, parts[1])
    if len(parts) == 3 and parts[0] == "questionnaires" and parts[2] == "preview":
        from . import questionnaire_page

        defn = questionnaire_store.get_definition(parts[1])
        return Response(200, questionnaire_page.preview_page(defn)) if defn else Response(404, "לא נמצא")
    if len(parts) == 3 and parts[0] == "questionnaires" and parts[2] == "export.csv":
        return export_csv(parts[1])
    if parts == ["responses"]:
        return page_responses(base, str(query.get("q") or ""))
    if len(parts) == 3 and parts[0] == "responses":
        return page_response(base, parts[1], parts[2])
    return Response(404, "לא נמצא")
