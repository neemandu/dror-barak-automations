"""The questionnaire a client fills — our own form, branded, one section per step.

Two routes, same token scheme as the signing page (the link *is* the
credential; the client has no account):

  GET  /questionnaire?t=<token>   the form (prefilled if they answered before)
  POST /questionnaire?t=<token>   validate, store, file a Google Doc, and start
                                  the social-media analysis in the background

What the form asks comes from :mod:`src.lib.questionnaire_store` — Dror edits it
in the admin editor; nothing here names a question. The page works without
JavaScript (every section on one page, one submit button); with it, sections
become steps with a progress bar and the answers are kept as a draft on the
device until they are sent.
"""

from __future__ import annotations

import base64
import functools
import html
import json
from pathlib import Path
from typing import Any, Optional

from . import ui
from .lib import branded_doc, deliverables, questionnaire, questionnaire_store, signing
from .lib.clients.crm import CrmClient
from .lib.logging_setup import get_logger

log = get_logger("questionnaire", "page")

LOGO = Path(__file__).resolve().parents[1] / "templates" / "assets" / "logo_header.png"


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


@functools.lru_cache(maxsize=1)
def _logo_data_uri() -> str:
    try:
        return "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode("ascii")
    except OSError:
        return ""


PAGE_CSS = """
html.public body { background: var(--bg); }
.hero { position: relative; overflow: hidden; padding: 34px 20px 104px; text-align: center; background: var(--brand-grad); }
.hero::before { content: ""; position: absolute; inset: 0; pointer-events: none;
  background: radial-gradient(640px 260px at 88% -20%, rgba(255, 255, 255, .38), transparent 62%),
              radial-gradient(520px 240px at 8% 130%, rgba(255, 255, 255, .22), transparent 60%); }
.hero img { position: relative; width: 196px; max-width: 58%; height: auto; }
.hero .name { position: relative; color: #fff; font-weight: 800; font-size: 22px; letter-spacing: .5px; }
.shell { position: relative; max-width: 760px; margin: -76px auto 40px; padding: 0 16px; }
.sheet { position: relative; background: var(--surface); border-radius: 22px; padding: 42px 46px;
  box-shadow: 0 1px 2px rgba(16, 24, 40, .04), 0 28px 64px -28px rgba(16, 24, 40, .32); }
.eyebrow { display: inline-flex; align-items: center; gap: 7px; margin-bottom: 14px; padding: 5px 11px; border-radius: 99px;
  background: var(--brand-soft); color: var(--brand-ink); font-size: 13px; font-weight: 600; }
.sheet h1 { margin: 0 0 10px; font-size: 30px; font-weight: 800; letter-spacing: -.02em; }
.lead { margin: 0; color: var(--fg-muted); font-size: 16.5px; }
.for { margin: 14px 0 0; display: inline-flex; align-items: center; gap: 7px; color: var(--fg-muted); font-size: 14px; }
.notice { margin-top: 20px; }
.progress { margin: 32px 0 0; display: none; }
.segs { display: flex; gap: 6px; }
.segs i { position: relative; flex: 1; height: 6px; border-radius: 99px; overflow: hidden; background: var(--surface-active); }
.segs i::after { content: ""; position: absolute; inset: 0; border-radius: inherit; background: var(--brand-grad);
  transform: scaleX(0); transform-origin: right; transition: transform .5s var(--ease); }
.segs i.on::after { transform: none; }
.progress .meta { display: flex; justify-content: space-between; gap: 10px; margin-top: 10px; font-size: 13.5px; color: var(--fg-muted); }
.progress .meta b { color: var(--fg); font-weight: 600; }
fieldset.step { border: 0; margin: 30px 0 0; padding: 0; min-width: 0; }
fieldset.step legend { padding: 0; margin-bottom: 4px; font-size: 21px; font-weight: 700; letter-spacing: -.01em; }
.step .desc { margin: 0 0 4px; color: var(--fg-muted); font-size: 15px; }
.q { margin-top: 26px; }
.q > label, .q > .qlabel { display: block; margin-bottom: 9px; font-size: 16px; font-weight: 600; color: var(--fg); }
.q .req { color: var(--danger); margin-inline-start: 3px; }
.q .qhint { display: block; margin-top: 2px; font-size: 14px; font-weight: 400; color: var(--fg-muted); }
.control { width: 100%; height: 52px; padding: 0 16px; border: 1.5px solid var(--border); border-radius: 13px; background: var(--surface);
  color: var(--fg); font: inherit; font-size: 16px; transition: border-color var(--d1), box-shadow var(--d2) var(--ease), background var(--d1); }
textarea.control { height: auto; min-height: 124px; padding: 14px 16px; line-height: 1.6; resize: vertical; }
.control:hover { border-color: var(--border-strong); }
.control:focus { outline: none; border-color: var(--brand); box-shadow: 0 0 0 4px var(--ring-soft); }
.control::placeholder { color: var(--fg-subtle); }
.control.ltr { direction: ltr; text-align: left; }
.choices { display: flex; flex-wrap: wrap; gap: 10px; }
.choice { position: relative; display: inline-flex; align-items: center; gap: 10px; min-height: 48px; padding: 0 16px 0 14px;
  border: 1.5px solid var(--border); border-radius: 13px; background: var(--surface); cursor: pointer; font-weight: 500; user-select: none;
  -webkit-tap-highlight-color: transparent;
  transition: border-color var(--d1), background var(--d1), color var(--d1), transform var(--d1) var(--ease), box-shadow var(--d2); }
.choice:hover { border-color: var(--border-strong); background: var(--surface-2); }
.choice:active { transform: scale(.97); }
.choice input { position: absolute; opacity: 0; pointer-events: none; }
.choice .tick { flex: none; width: 20px; height: 20px; border-radius: 50%; border: 1.5px solid var(--border-strong); display: grid;
  place-items: center; transition: background var(--d2) var(--ease), border-color var(--d2); }
.choice.multi .tick { border-radius: 6px; }
.choice .tick svg { width: 12px; height: 12px; stroke-width: 3.2; color: #fff; transform: scale(0); transition: transform var(--d3) var(--spring); }
.choice:has(input:checked) { border-color: var(--brand); background: var(--brand-soft); color: var(--brand-ink); }
.choice:has(input:checked) .tick { background: var(--brand); border-color: var(--brand); }
.choice:has(input:checked) .tick svg { transform: scale(1); }
.choice:has(input:focus-visible) { box-shadow: 0 0 0 4px var(--ring-soft); }
.scale { display: grid; grid-template-columns: repeat(auto-fit, minmax(42px, 1fr)); gap: 6px; }
.scale .choice { justify-content: center; padding: 0; min-height: 50px; font-weight: 600; font-variant-numeric: tabular-nums; }
.scale .choice .tick { display: none; }
.scale-ends { display: flex; justify-content: space-between; margin-top: 7px; font-size: 12.5px; color: var(--fg-subtle); }
.q.has-err .control, .q.has-err .choice { border-color: var(--danger); }
.q .err { display: flex; align-items: center; gap: 6px; max-height: 0; opacity: 0; overflow: hidden; margin-top: 0;
  color: var(--danger-ink); font-size: 14px; font-weight: 500; transition: max-height var(--d3) var(--ease), opacity var(--d2), margin var(--d2); }
.q.has-err .err { max-height: 48px; opacity: 1; margin-top: 8px; }
.formnav { position: sticky; bottom: 0; z-index: 5; display: flex; align-items: center; gap: 10px; margin: 40px -46px -42px;
  padding: 18px 46px 22px; border-top: 1px solid var(--border); border-radius: 0 0 22px 22px;
  background: color-mix(in srgb, var(--surface) 90%, transparent); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); }
.formnav .spacer { flex: 1; }
.saved { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--fg-muted); opacity: 0; transition: opacity var(--d3); }
.saved.on { opacity: 1; }
.saved .icon { color: var(--success); }
.foot { display: flex; align-items: center; justify-content: center; gap: 7px; margin: 22px 0 0; color: var(--fg-subtle); font-size: 13px; }
.req-note { margin: 18px 0 0; font-size: 13.5px; color: var(--fg-muted); }
.preview-bar { position: sticky; top: 0; z-index: 60; display: flex; align-items: center; justify-content: center; gap: 8px;
  padding: 11px 16px; background: #101828; color: #fff; font-size: 14px; font-weight: 500; }
.step.enter-next { animation: step-next .42s var(--ease) both; }
.step.enter-prev { animation: step-prev .42s var(--ease) both; }
@keyframes step-next { from { opacity: 0; transform: translateX(-28px); } to { opacity: 1; transform: none; } }
@keyframes step-prev { from { opacity: 0; transform: translateX(28px); } to { opacity: 1; transform: none; } }
.done { text-align: center; padding: 14px 0 6px; }
.done h1 { margin-top: 18px; }
.done .lead { max-width: 440px; margin: 0 auto; }
.check-anim { width: 84px; height: 84px; margin: 0 auto; }
.check-anim circle { fill: var(--success-soft); stroke: var(--success); stroke-width: 3; stroke-dasharray: 252; stroke-dashoffset: 252;
  animation: draw .7s var(--ease) forwards; }
.check-anim path { fill: none; stroke: var(--success); stroke-width: 5; stroke-linecap: round; stroke-linejoin: round;
  stroke-dasharray: 60; stroke-dashoffset: 60; animation: draw .4s .55s var(--ease) forwards; }
.fail-icon { width: 64px; height: 64px; margin: 0 auto 6px; border-radius: 50%; display: grid; place-items: center;
  background: var(--danger-soft); color: var(--danger-ink); box-shadow: 0 0 0 10px color-mix(in srgb, var(--danger-soft) 55%, transparent); }
@keyframes draw { to { stroke-dashoffset: 0; } }
#confetti { position: fixed; inset: 0; pointer-events: none; z-index: 70; }
html.js .progress { display: block; }
html.js fieldset.step { display: none; }
html.js fieldset.step.on { display: block; }
html.js .nojs-only { display: none; }
.js-only { display: none; }
html.js .js-only { display: inline-flex; }
@media (max-width: 600px) {
  .hero { padding: 26px 16px 92px; }
  .shell { padding: 0 12px; margin-top: -72px; }
  .sheet { padding: 28px 20px; border-radius: 20px; }
  .sheet h1 { font-size: 24px; }
  .formnav { margin: 32px -20px -28px; padding: 14px 20px 16px; border-radius: 0 0 20px 20px; }
  .formnav .btn-lg { --h: 48px; padding: 0 18px; }
  .saved span { display: none; }
}
"""

PAGE_JS = r"""
(function () {
  document.documentElement.classList.add('js');
  var form = document.getElementById('qform'); if (!form) return;
  var steps = Array.prototype.slice.call(form.querySelectorAll('fieldset.step'));
  var prev = document.getElementById('prev'), next = document.getElementById('next'),
      send = document.getElementById('send'), segs = document.getElementById('segs'),
      label = document.getElementById('steplabel'), count = document.getElementById('stepcount'),
      saved = document.getElementById('saved');
  var preview = form.hasAttribute('data-preview');
  var key = 'qdraft:' + form.getAttribute('data-draft');
  var i = 0;
  steps.forEach(function () { segs.appendChild(document.createElement('i')); });

  function show(n, dir) {
    i = Math.max(0, Math.min(steps.length - 1, n));
    steps.forEach(function (s, k) {
      s.classList.toggle('on', k === i); s.classList.remove('enter-next', 'enter-prev');
      if (k === i && dir) { void s.offsetWidth; s.classList.add(dir > 0 ? 'enter-next' : 'enter-prev'); }
    });
    Array.prototype.forEach.call(segs.children, function (seg, k) { seg.classList.toggle('on', k <= i); });
    prev.style.visibility = i === 0 ? 'hidden' : 'visible';
    next.style.display = i === steps.length - 1 ? 'none' : '';
    send.style.display = i === steps.length - 1 ? '' : 'none';
    count.textContent = 'שלב ' + (i + 1) + ' מתוך ' + steps.length;
    label.textContent = steps[i].getAttribute('data-title');
  }
  function valueOf(q) {
    var inputs = q.querySelectorAll('input,textarea');
    for (var k = 0; k < inputs.length; k++) {
      var el = inputs[k];
      if ((el.type === 'radio' || el.type === 'checkbox') ? el.checked : el.value.trim()) return true;
    }
    return false;
  }
  function check(step) {
    var bad = null;
    step.querySelectorAll('.q').forEach(function (q) {
      var err = '';
      if (q.hasAttribute('data-required') && !valueOf(q)) err = 'שדה חובה';
      var email = q.querySelector('input[type=email]');
      if (!err && email && email.value.trim() && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.value.trim()))
        err = 'כתובת אימייל לא תקינה';
      q.classList.toggle('has-err', !!err);
      if (err) { q.querySelector('.err span').textContent = err; bad = bad || q; }
    });
    if (bad) {
      bad.classList.remove('shake'); void bad.offsetWidth; bad.classList.add('shake');
      var f = bad.querySelector('input,textarea'); if (f) f.focus({preventScroll: true});
      bad.scrollIntoView({block: 'center', behavior: 'smooth'});
    }
    return !bad;
  }
  var hideSaved;
  function save() {
    if (preview) return;
    var data = {};
    new FormData(form).forEach(function (v, k) { (data[k] = data[k] || []).push(v); });
    try { localStorage.setItem(key, JSON.stringify(data)); saved.classList.add('on');
      clearTimeout(hideSaved); hideSaved = setTimeout(function () { saved.classList.remove('on'); }, 2200); } catch (e) {}
  }
  function restore() {
    if (preview || form.hasAttribute('data-prefilled')) return;
    var data; try { data = JSON.parse(localStorage.getItem(key) || 'null'); } catch (e) { data = null; }
    if (!data) return;
    Object.keys(data).forEach(function (k) {
      form.querySelectorAll('[name="' + k + '"]').forEach(function (el) {
        if (el.type === 'radio' || el.type === 'checkbox') el.checked = data[k].indexOf(el.value) >= 0;
        else el.value = data[k][0] || '';
      });
    });
    if (window.UI) UI.toast('המשכת מהטיוטה ששמרת במכשיר הזה', {icon: 'rotate'});
  }
  function top() { document.querySelector('.sheet').scrollIntoView({behavior: 'smooth', block: 'start'}); }
  var timer;
  form.addEventListener('input', function (e) {
    var q = e.target.closest('.q'); if (q) q.classList.remove('has-err');
    clearTimeout(timer); timer = setTimeout(save, 450);
  });
  form.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && e.target.tagName === 'INPUT' && i < steps.length - 1) { e.preventDefault(); next.click(); }
  });
  next.addEventListener('click', function () { if (check(steps[i])) { show(i + 1, 1); top(); } });
  prev.addEventListener('click', function () { show(i - 1, -1); top(); });
  form.addEventListener('submit', function (e) {
    for (var k = 0; k < steps.length; k++) { if (!check(steps[k])) { if (k !== i) show(k, k > i ? 1 : -1); e.preventDefault(); return; } }
    if (preview) { e.preventDefault(); if (window.UI) UI.toast('בתצוגה מקדימה התשובות לא נשלחות'); return; }
    send.classList.add('is-loading'); send.disabled = true;
    try { localStorage.removeItem(key); } catch (err) {}
  });
  restore();
  var first = form.querySelector('.q.has-err');
  show(first ? steps.indexOf(first.closest('fieldset.step')) : 0);
})();
"""

CONFETTI_JS = r"""
(function () {
  if (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var c = document.createElement('canvas'); c.id = 'confetti'; document.body.appendChild(c);
  var ctx = c.getContext('2d'), W = c.width = innerWidth, H = c.height = innerHeight, parts = [];
  var colors = ['#00e5d0', '#00a8f0', '#2f7de1', '#12b76a', '#fdb022'];
  for (var k = 0; k < 110; k++) parts.push({x: W / 2, y: H * .32, vx: (Math.random() - .5) * 14, vy: -Math.random() * 13 - 4,
    r: 3 + Math.random() * 4, c: colors[k % colors.length], a: Math.random() * 6, s: (Math.random() - .5) * .3});
  var t0 = performance.now();
  (function frame(t) {
    var el = t - t0; ctx.clearRect(0, 0, W, H);
    parts.forEach(function (p) { p.vy += .38; p.vx *= .99; p.x += p.vx; p.y += p.vy; p.a += p.s;
      ctx.save(); ctx.globalAlpha = Math.max(0, 1 - el / 2400); ctx.translate(p.x, p.y); ctx.rotate(p.a);
      ctx.fillStyle = p.c; ctx.fillRect(-p.r, -p.r / 2, p.r * 2, p.r); ctx.restore(); });
    if (el < 2400) requestAnimationFrame(frame); else c.remove();
  })(t0);
})();
"""


def _page(title: str, body: str, *, script: str = "", preview: bool = False) -> str:
    logo = _logo_data_uri()
    brand = (f'<img src="{logo}" alt="DROR BARAK">' if logo else '<div class="name">DROR BARAK</div>')
    bar = (f'<div class="preview-bar">{ui.icon("eye", 16)}<span>תצוגה מקדימה: כך הלקוח יראה את השאלון. '
           'התשובות לא נשמרות.</span></div>' if preview else "")
    return ui.document(
        title,
        f'{bar}<header class="hero">{brand}</header><main class="shell"><div class="sheet reveal">{body}</div>'
        f'<p class="foot">{ui.icon("lock", 13)}<span>דרור ברק · ליווי עסקים והגדלת מכירות</span></p></main>',
        kind="public", css=PAGE_CSS, script=script)


def error_page(message: str) -> str:
    from .sign_page import client_message  # one Hebrew wording for both pages

    return _page("הקישור לא נפתח", f'<div class="done"><div class="fail-icon">{ui.icon("alert", 28)}</div>'
                 f'<h1>הקישור לא נפתח</h1><p class="lead">{_esc(client_message(message))}</p>'
                 '<p class="req-note">אפשר לפנות לדרור ברק ונשלח קישור חדש.</p></div>')


def done_page(defn: Optional[dict[str, Any]] = None) -> str:
    thanks = (defn or {}).get("thanks") or "קיבלנו את התשובות."
    check = ('<svg class="check-anim" viewBox="0 0 84 84" aria-hidden="true"><circle cx="42" cy="42" r="40"/>'
             '<path d="M26 43l11 11 21-23"/></svg>')
    return _page("תודה", f'<div class="done">{check}<h1>תודה!</h1><p class="lead">{_esc(thanks)}</p>'
                         '<p class="req-note">אפשר לסגור את הדף.</p></div>', script=CONFETTI_JS)


# ----------------------------------------------------------------- the form


def _question_html(q: dict[str, Any], value: Any, error: str) -> str:
    key, kind = q["key"], q["kind"]
    req = '<span class="req" aria-hidden="true">*</span>' if q.get("required") else ""
    hint = f'<span class="qhint">{_esc(q["hint"])}</span>' if q.get("hint") else ""
    attrs = ' data-required="1"' if q.get("required") else ""
    cls = "q has-err" if error else "q"
    err = f'<div class="err" role="alert">{ui.icon("alert", 15)}<span>{_esc(error)}</span></div>'
    fid = f"f_{key}"

    if kind in ("choice", "multi", "scale"):
        itype = "checkbox" if kind == "multi" else "radio"
        chosen = set(value) if isinstance(value, list) else {str(value or "")}
        options = ([str(n) for n in range(1, int(q.get("scale_max") or 10) + 1)]
                   if kind == "scale" else q.get("options") or [])
        tick = ui.icon("check", 12)
        pills = "".join(
            f'<label class="choice{" multi" if kind == "multi" else ""}"><input type="{itype}" name="{_esc(key)}" '
            f'value="{_esc(o)}"{" checked" if o in chosen else ""}><span class="tick">{tick}</span>'
            f'<span>{_esc(o)}</span></label>' for o in options)
        wrap = "scale" if kind == "scale" else "choices"
        ends = ('<div class="scale-ends"><span>נמוך</span><span>גבוה</span></div>' if kind == "scale" else "")
        return (f'<div class="{cls}"{attrs} role="group" aria-labelledby="l_{_esc(key)}">'
                f'<span class="qlabel" id="l_{_esc(key)}">{_esc(q["label"])}{req}{hint}</span>'
                f'<div class="{wrap}">{pills}</div>{ends}{err}</div>')

    label = f'<label for="{_esc(fid)}">{_esc(q["label"])}{req}{hint}</label>'
    text = _esc(questionnaire.display(value))
    required = " required" if q.get("required") else ""
    if kind == "textarea":
        field = f'<textarea class="control" id="{_esc(fid)}" name="{_esc(key)}"{required}>{text}</textarea>'
    else:
        input_type, extra = {
            "email": ("email", ' class="control ltr" autocomplete="email" placeholder="name@example.com"'),
            "tel": ("tel", ' class="control ltr" autocomplete="tel" placeholder="050-0000000"'),
            "url": ("text", ' class="control ltr" inputmode="url" placeholder="https://"'),
            "number": ("text", ' class="control" inputmode="decimal"'),
        }.get(kind, ("text", ' class="control"'))
        field = f'<input type="{input_type}" id="{_esc(fid)}" name="{_esc(key)}" value="{text}"{extra}{required}>'
    return f'<div class="{cls}"{attrs}>{label}{field}{err}</div>'


def render_form(defn: dict[str, Any], *, client_name: str = "", action: str = "",
                answers: Optional[dict[str, Any]] = None, errors: Optional[dict[str, str]] = None,
                notice: str = "", preview: bool = False, draft_key: str = "") -> str:
    answers, errors = answers or {}, errors or {}
    sections = defn.get("sections") or []
    n_questions = sum(len(s.get("questions") or []) for s in sections)
    parts = [f'<span class="eyebrow">{ui.icon("clipboard", 14)}<span>{len(sections)} שלבים · {n_questions} שאלות</span></span>',
             f'<h1>{_esc(defn.get("title"))}</h1>']
    if defn.get("intro"):
        parts.append(f'<p class="lead">{_esc(defn["intro"])}</p>')
    if client_name:
        parts.append(f'<p class="for">{ui.icon("users", 15)}<span>עבור {_esc(client_name)}</span></p>')
    if notice:
        parts.append(f'<div class="alert alert-info notice">{ui.icon("info")}<span>{_esc(notice)}</span></div>')
    if errors:
        parts.append(f'<div class="alert alert-danger notice" role="alert">{ui.icon("alert")}<span>יש {len(errors)} '
                     f'{"שדה שדורש" if len(errors) == 1 else "שדות שדורשים"} תיקון. הם מסומנים באדום.</span></div>')
    parts.append('<div class="progress" aria-hidden="true"><div class="segs" id="segs"></div>'
                 '<div class="meta"><b id="steplabel"></b><span id="stepcount" class="num"></span></div></div>')
    flags = (" data-preview" if preview else "") + (" data-prefilled" if answers else "")
    parts.append(f'<form id="qform" method="post" action="{_esc(action)}" novalidate'
                 f' data-draft="{_esc(draft_key or defn.get("id"))}"{flags}>')
    for s in sections:
        desc = f'<p class="desc">{_esc(s["description"])}</p>' if s.get("description") else ""
        qs = "".join(_question_html(q, answers.get(q["key"]), errors.get(q["key"], ""))
                     for q in s.get("questions") or [])
        parts.append(f'<fieldset class="step" data-title="{_esc(s.get("title"))}">'
                     f'<legend>{_esc(s.get("title"))}</legend>{desc}{qs}</fieldset>')
    parts.append('<p class="req-note">שדות המסומנים ב-<span class="req" style="color:var(--danger)">*</span> הם חובה.</p>')
    parts.append(
        '<div class="formnav">'
        f'<button type="button" class="btn btn-lg js-only" id="prev">{ui.icon("arrow-right", 17)}<span>הקודם</span></button>'
        f'<span class="saved" id="saved">{ui.icon("check-circle", 14)}<span>נשמר במכשיר</span></span><span class="spacer"></span>'
        f'<button type="button" class="btn btn-primary btn-lg js-only" id="next"><span>הבא</span>{ui.icon("arrow-left", 17)}</button>'
        f'<button type="submit" class="btn btn-primary btn-lg" id="send"><span>{"שליחה" if not preview else "שליחה (תצוגה)"}</span>'
        f'{ui.icon("send", 16)}</button></div></form>')
    return _page(str(defn.get("title") or "שאלון"), "".join(parts), script=PAGE_JS, preview=preview)


def preview_page(defn: dict[str, Any]) -> str:
    """The form as a client will see it, for the admin; nothing is submitted."""
    return render_form(defn, client_name="לקוח לדוגמה", action="#", preview=True)


# ---------------------------------------------------------------- handlers


def _load(token: str, dry_run: bool) -> tuple[str, dict[str, Any], dict[str, Any]]:
    client_id = signing.resolve(token)
    client = CrmClient(dry_run=dry_run).get_client(client_id)
    return client_id, client, questionnaire_store.definition_for_client(client_id)


def handle_get(token: str, dry_run: bool = False) -> str:
    client_id, client, defn = _load(token, dry_run)
    previous = questionnaire_store.get_response(client_id, defn["id"])
    answers, notice = None, ""
    if previous and previous.get("status") == "answered":
        answers = previous.get("answers") or {}
        notice = (f"כבר מילאת את השאלון ב-{_date(previous.get('answered_at'))}. "
                  "אפשר לעדכן תשובות ולשלוח שוב.")
    return render_form(defn, client_name=str(client.get("name") or ""), action=f"?t={token}",
                       answers=answers, notice=notice, draft_key=f"{defn['id']}:{token[-12:]}")


def handle_post(token: str, form: dict[str, list[str]], *, dry_run: bool = False) -> str:
    client_id, client, defn = _load(token, dry_run)
    answers = questionnaire.parse_answers(defn, form)
    errors = questionnaire.problems(defn, answers)
    if errors:
        return render_form(defn, client_name=str(client.get("name") or ""), action=f"?t={token}",
                           answers=answers, errors=errors, draft_key=f"{defn['id']}:{token[-12:]}")
    _finalise(client_id, client, defn, answers, dry_run=dry_run)
    return done_page(defn)


def _date(iso: Any) -> str:
    text = str(iso or "")[:10]
    return f"{text[8:10]}.{text[5:7]}.{text[0:4]}" if len(text) == 10 else text


def _finalise(client_id: str, client: dict[str, Any], defn: dict[str, Any],
              answers: dict[str, Any], *, dry_run: bool) -> None:
    from .automations.base import Automation
    from .lib import tasks

    auto = Automation("questionnaire", dry_run=dry_run)
    crm = CrmClient(dry_run=dry_run)
    name = str(client.get("name") or client_id)

    # Answered — stop the chase before anything that could fail, so a Drive
    # error can never turn into a reminder for a form the client just filled.
    if not dry_run:
        signing.clear_questionnaire_pending(client_id)

    snap = questionnaire.snapshot(defn)
    title = str(defn.get("title") or "שאלון")
    doc: dict[str, str] = {"url": ""}
    try:
        doc = deliverables.save_doc(
            crm, {**client, "id": client_id}, file_name=f"{title} - {name}", title=title,
            subtitle=f"{name}   ·   מולא ב-{branded_doc.hebrew_date()}",
            blocks=questionnaire.to_document_blocks(snap, answers), dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 - the answers are stored below either way
        auto.log_action("questionnaire_doc_failed", "error", client_id=client_id, detail=str(exc))

    questionnaire_store.record_answer(client_id, name, defn, answers, doc_url=doc["url"])
    crm.append_automation_log(client_id, "📋 השאלון מולא" + (f" - התשובות בדרייב:\n{doc['url']}" if doc["url"] else ""))
    auto.log_action("questionnaire_answered", client_id=client_id,
                    detail=str(defn.get("title") or ""), url=doc["url"] or None)

    if questionnaire.social_profiles(snap, answers):
        try:
            tasks.dispatch("social_prep", client_id=client_id, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - never fail the client's submission over it
            log.warning("social_prep_dispatch_failed", extra={"client_id": client_id, "error": str(exc)})
