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
    """ISO UTC → ``DD.MM.YYYY HH:MM`` in Israel time (fixed +3 if no tz database)."""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    try:
        from zoneinfo import ZoneInfo

        dt = dt.astimezone(ZoneInfo("Asia/Jerusalem"))
    except Exception:  # noqa: BLE001 - no tz database on this host
        dt = dt.astimezone(timezone(timedelta(hours=3)))
    return dt.strftime("%d.%m.%Y %H:%M")


def _days_since(iso: Any) -> Optional[int]:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, (datetime.now(timezone.utc) - dt).days)


# ------------------------------------------------------------------- chrome

ADMIN_CSS = """
[hidden] { display:none !important; }
.nav { display:flex; gap:4px; flex-wrap:wrap; margin:0 0 18px; border-bottom:1px solid var(--line); }
.nav a { padding:8px 14px; text-decoration:none; color:var(--muted); border-bottom:2px solid transparent;
  margin-bottom:-1px; font-weight:500; }
.nav a.on { color:var(--text); border-bottom-color:var(--text); }
.bar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:16px; }
.bar h1 { margin:0; }
.bar .grow { flex:1; }
.btn { display:inline-block; font:inherit; font-size:14px; padding:7px 14px; border-radius:8px;
  border:1px solid var(--line); background:var(--card); color:var(--text); cursor:pointer;
  text-decoration:none; white-space:nowrap; }
.btn.primary { background:var(--text); color:var(--bg); border-color:var(--text); }
.btn.danger { color:var(--err); }
.btn:disabled { opacity:.5; cursor:default; }
.pill { display:inline-block; font-size:12px; padding:1px 9px; border-radius:99px; border:1px solid var(--line);
  color:var(--muted); white-space:nowrap; }
.pill.ok { color:var(--ok); border-color:var(--ok); }
.pill.wait { color:var(--skip); border-color:var(--skip); }
.pill.def { color:var(--text); border-color:var(--text); }
.tablewrap { overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:10px; }
.tablewrap table td, .tablewrap table th { padding:9px 12px; border-bottom:1px solid var(--line);
  text-align:start; vertical-align:middle; }
.tablewrap table th { font-size:12.5px; color:var(--muted); font-weight:600; white-space:nowrap; }
.tablewrap tr:last-child td { border-bottom:0; }
.actions { display:flex; gap:6px; flex-wrap:wrap; }
.panel { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px;
  margin-bottom:14px; }
.panel h2 { font-size:15px; margin:0 0 10px; }
.row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
.row > * { min-width:0; }
.muted { color:var(--muted); font-size:13px; }
.answers dt { font-weight:600; margin-top:12px; }
.answers dd { margin:2px 0 0; white-space:pre-wrap; }
.answers h3 { font-size:14px; margin:18px 0 0; padding-bottom:4px; border-bottom:1px solid var(--line); }
.linkout { direction:ltr; text-align:left; font-family:ui-monospace,Menlo,monospace; font-size:12.5px;
  width:100%; }
.toast { position:fixed; bottom:18px; left:50%; transform:translateX(-50%); background:var(--text);
  color:var(--bg); padding:9px 16px; border-radius:8px; font-size:14px; z-index:20; }
.errors { border:1px solid var(--err); color:var(--err); border-radius:10px; padding:10px 14px;
  margin-bottom:14px; }
.errors ul { margin:4px 0 0; padding-inline-start:18px; }
/* editor */
.ed-top { position:sticky; top:0; z-index:5; background:var(--bg); padding:10px 0; margin-bottom:6px;
  border-bottom:1px solid var(--line); }
.ed-card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px;
  margin-bottom:12px; }
.ed-section { border:1px solid var(--line); border-radius:12px; padding:14px; margin-bottom:14px;
  background:color-mix(in srgb, var(--card) 92%, var(--bg)); }
.ed-section > .head { display:grid; grid-template-columns:1fr auto; gap:8px; align-items:start; }
.ed-q { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px;
  margin-top:10px; }
.ed-q .grid { display:grid; grid-template-columns:1fr 150px auto; gap:8px; align-items:center; }
.ed-q .more { display:grid; grid-template-columns:1fr; gap:8px; margin-top:8px; }
.ed label.small { font-size:12.5px; color:var(--muted); display:block; margin-bottom:2px; }
.ed input[type=text], .ed textarea, .ed select { width:100%; font:inherit; padding:7px 10px;
  border-radius:8px; border:1px solid var(--line); background:var(--bg); color:var(--text); }
.ed textarea { min-height:64px; resize:vertical; }
.ed .title-input { font-size:17px; font-weight:600; }
.ed .tools { display:flex; gap:4px; flex-wrap:wrap; }
.ed .tools .btn { padding:4px 9px; font-size:13px; }
.ed .key { font-family:ui-monospace,Menlo,monospace; font-size:11.5px; color:var(--muted); direction:ltr; }
.ed .req { display:flex; align-items:center; gap:6px; font-size:13.5px; white-space:nowrap; }
@media (max-width:640px) { .ed-q .grid { grid-template-columns:1fr; } }
"""


def nav(base: str, active: str) -> str:
    items = (("dashboard", "/dashboard", "פעילות"), ("questionnaires", "/admin/questionnaires", "שאלונים"),
             ("responses", "/admin/responses", "תשובות לשאלונים"))
    links = "".join(f'<a href="{base}{path}" class="{"on" if key == active else ""}">{label}</a>'
                    for key, path, label in items)
    return f'<nav class="nav">{links}<a href="{base}/logout" style="margin-inline-start:auto">יציאה</a></nav>'


def _shell(base: str, active: str, title: str, body: str, *, script: str = "") -> Response:
    from . import dashboard

    page = dashboard._page(title, f'<div class="wrap">{nav(base, active)}{body}</div>'
                                  f'<style>{ADMIN_CSS}</style>'
                                  + (f"<script>{script}</script>" if script else ""))
    return Response(200, page.decode("utf-8"))


def _data_tag(element_id: str, data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/json" id="{element_id}">{raw}</script>'


COMMON_JS = r"""
function api(path, body) {
  return fetch(BASE + '/admin/api' + path, {method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', 'X-Requested-With': 'dashboard'},
    body: JSON.stringify(body || {})}).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { d._status = r.status; return d; });
    });
}
function toast(text) {
  var t = document.createElement('div'); t.className = 'toast'; t.textContent = text;
  document.body.appendChild(t); setTimeout(function () { t.remove(); }, 2200);
}
function copyText(text) {
  if (navigator.clipboard) navigator.clipboard.writeText(text).then(function () { toast('הועתק'); });
}
"""


# ------------------------------------------------------------------ pages


def _counts(qid: str, responses: list[dict[str, Any]]) -> tuple[int, int]:
    mine = [r for r in responses if r.get("questionnaire_id") == qid]
    return len(mine), sum(1 for r in mine if r.get("status") == "answered")


def page_list(base: str) -> Response:
    defs = questionnaire_store.list_definitions()
    default = questionnaire_store.default_id()
    responses = questionnaire_store.list_responses()
    rows = ""
    for d in defs:
        sent, answered = _counts(d["id"], responses)
        n = sum(len(s.get("questions") or []) for s in d.get("sections") or [])
        is_def = d["id"] == default
        badge = ' <span class="pill def">נשלח ללקוחות</span>' if is_def else ""
        actions = (f'<a class="btn" href="{base}/admin/questionnaires/{quote(d["id"])}">עריכה</a>'
                   f'<a class="btn" target="_blank" href="{base}/admin/questionnaires/{quote(d["id"])}/preview">תצוגה</a>'
                   f'<a class="btn" href="{base}/admin/responses?q={quote(d["id"])}">תשובות</a>'
                   f'<button class="btn" data-copy="{_esc(d["id"])}">שכפל</button>')
        if not is_def:
            actions += (f'<button class="btn" data-default="{_esc(d["id"])}">הגדר כנשלח ללקוחות</button>'
                        f'<button class="btn danger" data-delete="{_esc(d["id"])}">מחק</button>')
        rows += (f'<tr><td><b>{_esc(d.get("title"))}</b>{badge}</td><td>{n}</td>'
                 f'<td>{sent}</td><td>{answered}</td><td class="muted">{_esc(_local(d.get("updated_at")))}</td>'
                 f'<td><div class="actions">{actions}</div></td></tr>')
    body = f"""<div class="bar"><h1>שאלונים</h1><span class="grow"></span>
      <input type="text" id="newtitle" placeholder="שם לשאלון חדש" style="padding:7px 10px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--text)">
      <button class="btn primary" id="create">שאלון חדש</button></div>
      <p class="muted">השאלון המסומן "נשלח ללקוחות" הוא זה שיוצא אוטומטית אחרי חתימה. לקוח שכבר קיבל שאלון
      ממשיך לראות את השאלון שקיבל, גם אם מחליפים את ברירת המחדל.</p>
      <div class="tablewrap"><table><tr><th>שאלון</th><th>שאלות</th><th>נשלח</th><th>מולא</th>
      <th>עודכן</th><th></th></tr>{rows}</table></div>"""
    script = f"var BASE={json.dumps(base)};" + COMMON_JS + r"""
document.getElementById('create').onclick = function () {
  api('/questionnaires', {title: document.getElementById('newtitle').value}).then(function (d) {
    if (d.id) location.href = BASE + '/admin/questionnaires/' + encodeURIComponent(d.id);
    else toast((d.errors || ['שגיאה']).join(' '));
  });
};
document.addEventListener('click', function (e) {
  var b = e.target.closest('button'); if (!b) return;
  if (b.dataset.copy) api('/questionnaires', {copy_from: b.dataset.copy}).then(function (d) {
    if (d.id) location.href = BASE + '/admin/questionnaires/' + encodeURIComponent(d.id); });
  if (b.dataset.default) api('/questionnaires/' + encodeURIComponent(b.dataset.default) + '/default').then(function () { location.reload(); });
  if (b.dataset.delete && confirm('למחוק את השאלון? תשובות שכבר התקבלו נשמרות.'))
    api('/questionnaires/' + encodeURIComponent(b.dataset.delete) + '/delete').then(function (d) {
      if (d.ok) location.reload(); else toast((d.errors || ['שגיאה']).join(' ')); });
});
"""
    return _shell(base, "questionnaires", "שאלונים", body, script=script)


EDITOR_JS = r"""
var data = JSON.parse(document.getElementById('qdata').textContent);
var meta = JSON.parse(document.getElementById('qmeta').textContent);
var defn = data, version = data.version || 0, dirty = false;
var root = document.getElementById('editor');

function h(tag, attrs, kids) {
  var el = document.createElement(tag);
  Object.keys(attrs || {}).forEach(function (k) {
    if (k === 'text') el.textContent = attrs[k];
    else if (k === 'value') el.value = attrs[k];
    else if (k === 'checked') el.checked = !!attrs[k];
    else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), attrs[k]);
    else el.setAttribute(k, attrs[k]);
  });
  (kids || []).forEach(function (c) { if (c) el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
  return el;
}
function key(prefix) { return prefix + '_' + Math.random().toString(16).slice(2, 8); }
function setDirty(v) {
  dirty = v;
  document.getElementById('state').textContent = v ? 'יש שינויים שלא נשמרו' : 'הכל נשמר';
  document.getElementById('save').disabled = !v;
}
function field(obj, prop, label, opts) {
  opts = opts || {};
  var input = opts.area ? h('textarea', {value: obj[prop] || ''}) : h('input', {type: 'text', value: obj[prop] || ''});
  if (opts.cls) input.className = opts.cls;
  if (opts.placeholder) input.placeholder = opts.placeholder;
  input.addEventListener('input', function () { obj[prop] = input.value; setDirty(true); });
  return h('div', {}, [label ? h('label', {class: 'small', text: label}) : null, input]);
}
function move(list, i, d) { var j = i + d; if (j < 0 || j >= list.length) return false;
  var t = list[i]; list[i] = list[j]; list[j] = t; return true; }
function btn(text, fn, cls) { return h('button', {type: 'button', class: 'btn' + (cls ? ' ' + cls : ''), text: text, onclick: fn}); }
function change() { setDirty(true); render(); }

function questionCard(section, si, q, qi) {
  var type = h('select', {}, Object.keys(meta.kinds).map(function (k) {
    var o = h('option', {value: k, text: meta.kinds[k]}); if (k === q.kind) o.selected = true; return o; }));
  type.onchange = function () { q.kind = type.value; if (q.kind !== 'url') q.role = '';
    if ((q.kind === 'choice' || q.kind === 'multi') && !(q.options || []).length) q.options = ['אפשרות א', 'אפשרות ב'];
    change(); };
  var req = h('label', {class: 'req'}, [h('input', {type: 'checkbox', checked: q.required,
    onchange: function (e) { q.required = e.target.checked; setDirty(true); }}), 'חובה']);
  var more = h('div', {class: 'more'}, [field(q, 'hint', 'הסבר קטן מתחת לשאלה (לא חובה)')]);
  if (q.kind === 'choice' || q.kind === 'multi') {
    var ta = h('textarea', {value: (q.options || []).join('\n'), rows: Math.max(3, (q.options || []).length + 1)});
    ta.addEventListener('input', function () { q.options = ta.value.split('\n').map(function (s) { return s.trim(); }).filter(Boolean); setDirty(true); });
    more.appendChild(h('div', {}, [h('label', {class: 'small', text: 'אפשרויות — אחת בכל שורה'}), ta]));
  }
  if (q.kind === 'scale') {
    var sm = h('select', {}, [5, 7, 10].map(function (n) { var o = h('option', {value: n, text: '1 עד ' + n});
      if (n === (q.scale_max || 10)) o.selected = true; return o; }));
    sm.onchange = function () { q.scale_max = parseInt(sm.value, 10); setDirty(true); };
    more.appendChild(h('div', {}, [h('label', {class: 'small', text: 'טווח הסולם'}), sm]));
  }
  if (q.kind === 'url') {
    var role = h('select', {}, Object.keys(meta.roles).map(function (k) {
      var o = h('option', {value: k, text: k ? meta.roles[k] : 'לא משמש לניתוח'}); if (k === (q.role || '')) o.selected = true; return o; }));
    role.onchange = function () { q.role = role.value; setDirty(true); };
    more.appendChild(h('div', {}, [h('label', {class: 'small', text: 'הניתוח האוטומטי ייכנס לקישור הזה בתור'}), role]));
  }
  var list = section.questions;
  var tools = h('div', {class: 'tools'}, [
    btn('↑', function () {
      if (qi > 0) { move(list, qi, -1); }
      else if (si > 0) { defn.sections[si - 1].questions.push(list.splice(qi, 1)[0]); }
      change(); }),
    btn('↓', function () {
      if (qi < list.length - 1) { move(list, qi, 1); }
      else if (si < defn.sections.length - 1) { defn.sections[si + 1].questions.unshift(list.splice(qi, 1)[0]); }
      change(); }),
    btn('שכפל', function () { var c = JSON.parse(JSON.stringify(q)); c.key = key('q'); c.label = c.label + ' (עותק)';
      list.splice(qi + 1, 0, c); change(); }),
    btn('מחק', function () { if (confirm('למחוק את השאלה? תשובות שכבר התקבלו נשמרות.')) { list.splice(qi, 1); change(); } }, 'danger'),
  ]);
  return h('div', {class: 'ed-q'}, [
    h('div', {class: 'grid'}, [field(q, 'label', null, {placeholder: 'נוסח השאלה'}), type, req]),
    more,
    h('div', {class: 'row', style: 'margin-top:8px;justify-content:space-between'},
      [tools, h('span', {class: 'key', text: q.key})]),
  ]);
}

function render() {
  root.textContent = '';
  root.appendChild(h('div', {class: 'ed-card'}, [
    field(defn, 'title', 'שם השאלון (הכותרת שהלקוח רואה)', {cls: 'title-input'}),
    field(defn, 'intro', 'פתיח — מופיע מתחת לכותרת', {area: true}),
    field(defn, 'thanks', 'הודעת תודה — אחרי שליחה', {area: true}),
  ]));
  defn.sections.forEach(function (s, si) {
    var sec = h('div', {class: 'ed-section'}, [
      h('div', {class: 'head'}, [
        h('div', {}, [field(s, 'title', 'חלק ' + (si + 1) + ' — כותרת (כל חלק הוא שלב בטופס)', {cls: 'title-input'}),
                      field(s, 'description', 'תיאור קצר לחלק (לא חובה)')]),
        h('div', {class: 'tools'}, [
          btn('↑', function () { if (move(defn.sections, si, -1)) change(); }),
          btn('↓', function () { if (move(defn.sections, si, 1)) change(); }),
          btn('מחק חלק', function () { if (confirm('למחוק את החלק ואת כל השאלות בו?')) { defn.sections.splice(si, 1); change(); } }, 'danger'),
        ]),
      ]),
    ]);
    s.questions.forEach(function (q, qi) { sec.appendChild(questionCard(s, si, q, qi)); });
    sec.appendChild(h('div', {style: 'margin-top:10px'}, [btn('+ שאלה', function () {
      s.questions.push({key: key('q'), label: '', kind: 'text', required: false, hint: '', role: '', options: [], scale_max: 10});
      change(); })]));
    root.appendChild(sec);
  });
  root.appendChild(btn('+ חלק חדש', function () {
    defn.sections.push({id: key('s'), title: 'חלק חדש', description: '', questions: [
      {key: key('q'), label: '', kind: 'text', required: false, hint: '', role: '', options: [], scale_max: 10}]});
    change(); }));
}

function showErrors(list) {
  var box = document.getElementById('errors');
  box.hidden = !list.length; box.textContent = '';
  if (!list.length) return;
  box.appendChild(h('b', {text: 'לא נשמר — צריך לתקן:'}));
  box.appendChild(h('ul', {}, list.map(function (e) { return h('li', {text: e}); })));
  window.scrollTo({top: 0, behavior: 'smooth'});
}
function save() {
  var b = document.getElementById('save'); b.disabled = true; b.textContent = 'שומר…';
  api('/questionnaires/' + encodeURIComponent(defn.id), {definition: defn, version: version}).then(function (d) {
    b.textContent = 'שמירה';
    if (d._status === 200) { version = d.version; showErrors([]); setDirty(false); toast('נשמר'); }
    else if (d._status === 409) { showErrors(['השאלון נשמר בינתיים ממקום אחר. העתק את השינויים שלך, רענן את הדף והחל אותם שוב.']); b.disabled = false; }
    else { showErrors(d.errors || ['שגיאה בשמירה (' + d._status + ')']); b.disabled = false; }
  });
}
document.getElementById('save').onclick = save;
document.addEventListener('keydown', function (e) {
  if ((e.metaKey || e.ctrlKey) && e.key === 's') { e.preventDefault(); if (dirty) save(); } });
window.addEventListener('beforeunload', function (e) { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
render(); setDirty(false);
"""


def page_editor(base: str, qid: str) -> Response:
    defn = questionnaire_store.get_definition(qid)
    if not defn:
        return Response(404, "לא נמצא")
    is_default = qid == questionnaire_store.default_id()
    body = (f'<div class="ed-top"><div class="bar" style="margin:0"><h1>עריכת שאלון</h1>'
            + (' <span class="pill def">נשלח ללקוחות</span>' if is_default else "")
            + '<span class="grow"></span><span class="muted" id="state"></span>'
            f'<a class="btn" target="_blank" href="{base}/admin/questionnaires/{quote(qid)}/preview">תצוגה מקדימה</a>'
            '<button class="btn primary" id="save" disabled>שמירה</button></div></div>'
            '<div class="errors" id="errors" hidden></div>'
            + ('<p class="muted">שינוי כאן חל מיד על כל לקוח שיפתח את השאלון — גם על מי שכבר קיבל קישור. '
               'תשובות שכבר התקבלו נשמרות כפי שהיו.</p>' if is_default else "")
            + '<div class="ed" id="editor"></div>'
            + _data_tag("qdata", defn)
            + _data_tag("qmeta", {"kinds": questionnaire.KINDS, "roles": questionnaire.ROLES}))
    script = f"var BASE={json.dumps(base)};" + COMMON_JS + EDITOR_JS
    return _shell(base, "questionnaires", f"עריכה — {defn.get('title')}", body, script=script)


def page_responses(base: str, qid: str = "") -> Response:
    defs = {d["id"]: d for d in questionnaire_store.list_definitions()}
    rows_data = questionnaire_store.list_responses(qid or None)
    options = '<option value="">כל השאלונים</option>' + "".join(
        f'<option value="{_esc(i)}"{" selected" if i == qid else ""}>{_esc(d.get("title"))}</option>'
        for i, d in defs.items())
    rows = ""
    for r in rows_data:
        answered = r.get("status") == "answered"
        status = ('<span class="pill ok">מולא</span>' if answered else '<span class="pill wait">ממתין</span>')
        waiting = "" if answered else f"{_days_since(r.get('sent_at'))} ימים"
        href = f'{base}/admin/responses/{quote(str(r.get("client_id")))}/{quote(str(r.get("questionnaire_id")))}'
        rows += (f'<tr><td><a href="{href}"><b>{_esc(r.get("client_name"))}</b></a></td>'
                 f'<td>{_esc(r.get("questionnaire_title"))}</td><td>{status}</td>'
                 f'<td class="muted">{_esc(_local(r.get("sent_at")))}</td>'
                 f'<td class="muted">{_esc(_local(r.get("answered_at")))}</td><td class="muted">{waiting}</td>'
                 f'<td><a class="btn" href="{href}">פתח</a></td></tr>')
    if not rows:
        rows = '<tr><td colspan="7" class="muted" style="padding:22px">עדיין לא נשלח שאלון לאף לקוח.</td></tr>'
    export = (f'<a class="btn" href="{base}/admin/questionnaires/{quote(qid)}/export.csv">ייצוא ל-Excel (CSV)</a>'
              if qid else "")
    body = f"""<div class="bar"><h1>תשובות לשאלונים</h1><span class="grow"></span>
      <select id="filter" style="padding:7px 10px;border-radius:8px">{options}</select>{export}</div>
      <div class="panel"><h2>קישור לשאלון עבור לקוח</h2>
        <p class="muted" style="margin-top:0">יוצר קישור אישי — לבדיקה, או כדי לשלוח ללקוח בעצמך (למשל בוואטסאפ).
        שום דבר לא נשלח מכאן.</p>
        <div class="row"><select id="client" style="min-width:220px;padding:7px 10px;border-radius:8px">
          <option value="">טוען לקוחות…</option></select>
          <select id="lq" style="padding:7px 10px;border-radius:8px">{options.replace('<option value="">כל השאלונים</option>', '')}</select>
          <button class="btn primary" id="mklink">צור קישור</button></div>
        <div id="linkbox" hidden style="margin-top:10px" class="row">
          <input class="linkout" id="linkout" readonly><button class="btn" id="copylink">העתק</button>
          <a class="btn" id="openlink" target="_blank">פתח</a></div></div>
      <div class="tablewrap"><table><tr><th>לקוח</th><th>שאלון</th><th>סטטוס</th><th>נשלח</th><th>מולא</th>
      <th>ממתין</th><th></th></tr>{rows}</table></div>"""
    script = f"var BASE={json.dumps(base)};" + COMMON_JS + r"""
document.getElementById('filter').onchange = function (e) {
  location.href = BASE + '/admin/responses' + (e.target.value ? '?q=' + encodeURIComponent(e.target.value) : ''); };
api('/clients').then(function (d) {
  var sel = document.getElementById('client'); sel.textContent = '';
  var first = document.createElement('option'); first.value = ''; first.textContent = 'בחר לקוח'; sel.appendChild(first);
  (d.clients || []).forEach(function (c) { var o = document.createElement('option'); o.value = c.id;
    o.textContent = c.name + (c.status ? ' · ' + c.status : ''); sel.appendChild(o); });
  if (d.error) toast(d.error);
});
document.getElementById('mklink').onclick = function () {
  var cid = document.getElementById('client').value; if (!cid) { toast('בחר לקוח'); return; }
  api('/links', {client_id: cid, questionnaire_id: document.getElementById('lq').value}).then(function (d) {
    if (!d.url) { toast(d.error || 'שגיאה'); return; }
    document.getElementById('linkbox').hidden = false;
    document.getElementById('linkout').value = d.url; document.getElementById('openlink').href = d.url;
  });
};
document.getElementById('copylink').onclick = function () { copyText(document.getElementById('linkout').value); };
"""
    return _shell(base, "responses", "תשובות לשאלונים", body, script=script)


def page_response(base: str, client_id: str, qid: str) -> Response:
    r = questionnaire_store.get_response(client_id, qid)
    if not r:
        return Response(404, "לא נמצא")
    answered = r.get("status") == "answered"
    answers = r.get("answers") or {}
    parts, current = [], None
    for q in r.get("snapshot") or []:
        value = questionnaire.display(answers.get(q["key"]))
        if q.get("section") != current:
            current = q.get("section")
            parts.append(f"<h3>{_esc(current)}</h3>")
        parts.append(f'<dt>{_esc(q["label"])}</dt><dd>{_esc(value) if value else "<span class=muted>—</span>"}</dd>')
    body_answers = (f'<dl class="answers">{"".join(parts)}</dl>' if answered
                    else '<p class="muted">הלקוח עוד לא מילא את השאלון.</p>')
    history = "".join(f'<li>{_esc(_local(h.get("at")))} · {_esc({"answered": "מולא", "updated": "עודכן"}.get(h.get("event"), h.get("event", "").replace("sent:email", "נשלח במייל").replace("sent:link", "נוצר קישור")))}</li>'
                      for h in r.get("history") or [])
    doc = f'<a class="btn" target="_blank" href="{_esc(r["doc_url"])}">המסמך בדרייב ↗</a>' if r.get("doc_url") else ""
    body = f"""<div class="bar"><h1>{_esc(r.get('client_name'))}</h1>
      <span class="pill {'ok' if answered else 'wait'}">{'מולא' if answered else 'ממתין'}</span>
      <span class="grow"></span>{doc}<button class="btn" id="mklink">קישור חדש לשאלון</button></div>
      <p class="muted">{_esc(r.get('questionnaire_title'))} · נשלח {_esc(_local(r.get('sent_at'))) or '—'}
      · מולא {_esc(_local(r.get('answered_at'))) or '—'}</p>
      <div id="linkbox" hidden class="row" style="margin-bottom:14px"><input class="linkout" id="linkout" readonly>
        <button class="btn" id="copylink">העתק</button><a class="btn" id="openlink" target="_blank">פתח</a></div>
      <div class="panel">{body_answers}</div>
      <div class="panel"><h2>היסטוריה</h2><ul class="muted" style="margin:0">{history or '<li>—</li>'}</ul></div>
      {_data_tag('ctx', {'client_id': client_id, 'questionnaire_id': qid})}"""
    script = f"var BASE={json.dumps(base)};" + COMMON_JS + r"""
var ctx = JSON.parse(document.getElementById('ctx').textContent);
document.getElementById('mklink').onclick = function () {
  api('/links', ctx).then(function (d) {
    if (!d.url) { toast(d.error || 'שגיאה'); return; }
    document.getElementById('linkbox').hidden = false;
    document.getElementById('linkout').value = d.url; document.getElementById('openlink').href = d.url; }); };
document.getElementById('copylink').onclick = function () { copyText(document.getElementById('linkout').value); };
"""
    return _shell(base, "responses", f"תשובות — {r.get('client_name')}", body, script=script)


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
    if dry_run:
        return [{"id": "42", "name": "מכללת דוגמה", "status": "active"}]
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
