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

from .lib import deliverables, questionnaire, questionnaire_store, signing
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
:root { --ink:#14171a; --muted:#5b6472; --line:#dde3ea; --ground:#f2f5f8; --card:#fff;
  --brand:#1f6fd6; --brand-ink:#1557ad; --brand-soft:#e7f0fc; --err:#b42318; --err-soft:#fdecea;
  --ok:#0a7c42; --radius:14px; }
* { box-sizing:border-box; }
html { -webkit-text-size-adjust:100%; }
body { margin:0; background:var(--ground); color:var(--ink);
  font-family:"Heebo",system-ui,"Segoe UI",Arial,sans-serif; font-size:16px; line-height:1.6; }
.band { background:linear-gradient(100deg,#00e5d0 0%,#00a8f0 45%,#2f7de1 100%);
  padding:28px 20px 76px; text-align:center; }
.band img { width:210px; max-width:62%; height:auto; }
.band .name { color:#fff; font-weight:700; font-size:22px; letter-spacing:.3px; }
.wrap { max-width:720px; margin:-56px auto 48px; padding-inline:16px; }
.card { background:var(--card); border-radius:var(--radius);
  box-shadow:0 1px 2px rgba(16,24,40,.06),0 12px 32px -18px rgba(16,24,40,.28); padding:32px 34px; }
h1 { font-size:26px; line-height:1.25; margin:0 0 8px; text-wrap:balance; }
.intro { color:var(--muted); margin:0 0 4px; }
.for { color:var(--muted); font-size:14px; margin:0; }
.notice { background:var(--brand-soft); color:var(--brand-ink); border-radius:10px; padding:10px 14px;
  margin:18px 0 0; font-size:14.5px; }
.errbox { background:var(--err-soft); color:var(--err); border-radius:10px; padding:12px 14px;
  margin:18px 0 0; font-size:14.5px; }
.progress { margin:26px 0 6px; display:none; }
.progress .meta { display:flex; justify-content:space-between; font-size:13.5px; color:var(--muted);
  margin-bottom:8px; font-variant-numeric:tabular-nums; }
.progress .bar { height:6px; background:var(--line); border-radius:99px; overflow:hidden; }
.progress .bar i { display:block; height:100%; width:0; background:var(--brand); border-radius:99px;
  transition:width .3s ease; }
fieldset.step { border:0; margin:28px 0 0; padding:0; min-width:0; }
fieldset.step legend { font-size:19px; font-weight:700; padding:0; margin-bottom:2px; }
.step .desc { color:var(--muted); margin:0 0 6px; font-size:15px; }
.q { margin-top:22px; }
.q > label, .q > .label { display:block; font-weight:600; margin-bottom:6px; }
.q .req { color:var(--err); margin-inline-start:2px; }
.q .hint { display:block; font-weight:400; color:var(--muted); font-size:13.5px; margin-top:1px; }
input[type=text], input[type=email], input[type=tel], input[type=number], textarea {
  width:100%; font:inherit; color:inherit; padding:12px 14px; border:1.5px solid var(--line);
  border-radius:10px; background:#fff; transition:border-color .15s, box-shadow .15s; }
textarea { min-height:104px; resize:vertical; }
input:focus, textarea:focus { outline:none; border-color:var(--brand); box-shadow:0 0 0 3px var(--brand-soft); }
input.ltr { direction:ltr; text-align:left; }
.pills { display:flex; flex-wrap:wrap; gap:8px; }
.pill { position:relative; display:inline-flex; align-items:center; gap:8px; padding:9px 14px;
  border:1.5px solid var(--line); border-radius:99px; cursor:pointer; background:#fff; font-weight:500;
  user-select:none; }
.pill input { accent-color:var(--brand); margin:0; }
.pill:has(input:checked) { border-color:var(--brand); background:var(--brand-soft); color:var(--brand-ink); }
.pill:has(input:focus-visible) { box-shadow:0 0 0 3px var(--brand-soft); }
.scale { display:flex; flex-wrap:wrap; gap:6px; }
.scale .pill { width:46px; justify-content:center; padding:9px 0; font-variant-numeric:tabular-nums; }
.scale .pill input { position:absolute; opacity:0; }
.q.has-err input[type=text], .q.has-err input[type=email], .q.has-err input[type=tel],
.q.has-err input[type=number], .q.has-err textarea { border-color:var(--err); }
.q .err { display:none; color:var(--err); font-size:13.5px; margin-top:6px; }
.q.has-err .err { display:block; }
.nav { display:flex; gap:10px; justify-content:space-between; align-items:center; margin-top:32px;
  padding-top:20px; border-top:1px solid var(--line); flex-wrap:wrap; }
.btn { font:inherit; font-weight:600; border-radius:10px; padding:12px 26px; cursor:pointer;
  border:1.5px solid transparent; }
.btn.primary { background:var(--brand); color:#fff; }
.btn.primary:hover { background:var(--brand-ink); }
.btn.ghost { background:#fff; border-color:var(--line); color:var(--ink); }
.btn:disabled { opacity:.6; cursor:default; }
.btn:focus-visible { outline:3px solid var(--brand-soft); outline-offset:2px; }
.saved { color:var(--muted); font-size:13px; }
.foot { text-align:center; color:var(--muted); font-size:12.5px; margin-top:18px; }
.done { text-align:center; padding:18px 0 8px; }
.done .tick { width:64px; height:64px; margin:0 auto 14px; border-radius:50%; background:#e6f4ec;
  color:var(--ok); display:grid; place-items:center; font-size:32px; }
.preview { background:#fff6e0; color:#7a5200; border-radius:10px; padding:10px 14px; margin:0 0 18px;
  font-size:14.5px; font-weight:500; }
/* With JavaScript: one section at a time. */
.js .progress { display:block; }
.js fieldset.step { display:none; }
.js fieldset.step.on { display:block; }
.nojs-only { }
.js .nojs-only { display:none; }
.js-only { display:none; }
.js .js-only { display:inline-block; }
@media (max-width:560px) { .card { padding:24px 18px; } h1 { font-size:22px; }
  .band { padding-bottom:70px; } .btn { padding:12px 18px; } }
@media (prefers-reduced-motion:reduce) { .progress .bar i { transition:none; } }
"""

PAGE_JS = r"""
(function () {
  document.documentElement.classList.add('js');
  var form = document.getElementById('qform'); if (!form) return;
  var steps = Array.prototype.slice.call(form.querySelectorAll('fieldset.step'));
  var prev = document.getElementById('prev'), next = document.getElementById('next'),
      send = document.getElementById('send'), fill = document.getElementById('fill'),
      label = document.getElementById('steplabel'), count = document.getElementById('stepcount'),
      saved = document.getElementById('saved');
  var preview = form.hasAttribute('data-preview');
  var key = 'qdraft:' + form.getAttribute('data-draft');
  var i = 0;

  function show(n) {
    i = Math.max(0, Math.min(steps.length - 1, n));
    steps.forEach(function (s, k) { s.classList.toggle('on', k === i); });
    prev.style.visibility = i === 0 ? 'hidden' : 'visible';
    next.style.display = i === steps.length - 1 ? 'none' : '';
    send.style.display = i === steps.length - 1 ? '' : 'none';
    fill.style.width = (100 * (i + 1) / steps.length) + '%';
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
      if (err) { q.querySelector('.err').textContent = err; bad = bad || q; }
    });
    if (bad) { var f = bad.querySelector('input,textarea'); if (f) f.focus(); }
    return !bad;
  }
  function save() {
    if (preview) return;
    var data = {};
    new FormData(form).forEach(function (v, k) { (data[k] = data[k] || []).push(v); });
    try { localStorage.setItem(key, JSON.stringify(data)); saved.textContent = 'הטיוטה נשמרה במכשיר הזה'; }
    catch (e) {}
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
    saved.textContent = 'המשכת מהטיוטה ששמרת';
  }
  var timer;
  form.addEventListener('input', function (e) {
    var q = e.target.closest('.q'); if (q) q.classList.remove('has-err');
    clearTimeout(timer); timer = setTimeout(save, 400);
  });
  next.addEventListener('click', function () {
    if (check(steps[i])) { show(i + 1); window.scrollTo({ top: 0, behavior: 'smooth' }); }
  });
  prev.addEventListener('click', function () { show(i - 1); window.scrollTo({ top: 0, behavior: 'smooth' }); });
  form.addEventListener('submit', function (e) {
    for (var k = 0; k < steps.length; k++) { if (!check(steps[k])) { show(k); e.preventDefault(); return; } }
    if (preview) { e.preventDefault(); document.getElementById('previewnote').scrollIntoView(); return; }
    send.disabled = true; send.textContent = 'שולח…';
    try { localStorage.removeItem(key); } catch (err) {}
  });
  restore();
  var first = form.querySelector('.q.has-err');
  show(first ? steps.indexOf(first.closest('fieldset.step')) : 0);
})();
"""


def _page(title: str, body: str, *, script: bool = False) -> str:
    logo = _logo_data_uri()
    brand = (f'<img src="{logo}" alt="DROR BARAK">' if logo
             else '<div class="name">DROR BARAK</div>')
    js = f"<script>{PAGE_JS}</script>" if script else ""
    return f"""<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{_esc(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;600;700&display=swap">
<style>{PAGE_CSS}</style></head>
<body><div class="band">{brand}</div><div class="wrap"><div class="card">{body}</div>
<div class="foot">דרור ברק · ייעוץ שיווקי ואסטרטגי</div></div>{js}</body></html>"""


def error_page(message: str) -> str:
    from .sign_page import client_message  # one Hebrew wording for both pages

    return _page("שגיאה", f'<div class="errbox">{_esc(client_message(message))}</div>'
                 '<p class="for" style="margin-top:14px">אם הקישור אינו פועל, אנא פנה/י לדרור ברק.</p>')


def done_page(defn: Optional[dict[str, Any]] = None) -> str:
    thanks = (defn or {}).get("thanks") or "קיבלנו את התשובות."
    return _page("תודה", f'<div class="done"><div class="tick">✓</div><h1>תודה!</h1>'
                         f'<p class="intro">{_esc(thanks)}</p></div>')


# ----------------------------------------------------------------- the form


def _question_html(q: dict[str, Any], value: Any, error: str) -> str:
    key, kind = q["key"], q["kind"]
    req = ' <span class="req" aria-hidden="true">*</span>' if q.get("required") else ""
    hint = f'<span class="hint">{_esc(q["hint"])}</span>' if q.get("hint") else ""
    attrs = f' data-required="1"' if q.get("required") else ""
    cls = "q has-err" if error else "q"
    err = f'<div class="err" role="alert">{_esc(error)}</div>'
    fid = f"f_{key}"

    if kind in ("choice", "multi", "scale"):
        itype = "checkbox" if kind == "multi" else "radio"
        chosen = set(value) if isinstance(value, list) else {str(value or "")}
        options = ([str(n) for n in range(1, int(q.get("scale_max") or 10) + 1)]
                   if kind == "scale" else q.get("options") or [])
        pills = "".join(
            f'<label class="pill"><input type="{itype}" name="{_esc(key)}" value="{_esc(o)}"'
            f'{" checked" if o in chosen else ""}>{_esc(o)}</label>' for o in options)
        wrap = "scale" if kind == "scale" else "pills"
        return (f'<div class="{cls}"{attrs} role="group" aria-labelledby="l_{_esc(key)}">'
                f'<span class="label" id="l_{_esc(key)}">{_esc(q["label"])}{req}{hint}</span>'
                f'<div class="{wrap}">{pills}</div>{err}</div>')

    label = f'<label for="{_esc(fid)}">{_esc(q["label"])}{req}{hint}</label>'
    text = _esc(questionnaire.display(value))
    required = " required" if q.get("required") else ""
    if kind == "textarea":
        field = f'<textarea id="{_esc(fid)}" name="{_esc(key)}"{required}>{text}</textarea>'
    else:
        input_type, extra = {
            "email": ("email", ' class="ltr" autocomplete="email"'),
            "tel": ("tel", ' class="ltr" autocomplete="tel"'),
            "url": ("text", ' class="ltr" inputmode="url" placeholder="https://"'),
            "number": ("text", ' inputmode="decimal"'),
        }.get(kind, ("text", ""))
        field = f'<input type="{input_type}" id="{_esc(fid)}" name="{_esc(key)}" value="{text}"{extra}{required}>'
    return f'<div class="{cls}"{attrs}>{label}{field}{err}</div>'


def render_form(defn: dict[str, Any], *, client_name: str = "", action: str = "",
                answers: Optional[dict[str, Any]] = None, errors: Optional[dict[str, str]] = None,
                notice: str = "", preview: bool = False, draft_key: str = "") -> str:
    answers, errors = answers or {}, errors or {}
    sections = defn.get("sections") or []
    parts = []
    if preview:
        parts.append('<div class="preview" id="previewnote">תצוגה מקדימה — כך הלקוח יראה את השאלון. '
                     'התשובות לא נשמרות.</div>')
    parts.append(f'<h1>{_esc(defn.get("title"))}</h1>')
    if defn.get("intro"):
        parts.append(f'<p class="intro">{_esc(defn["intro"])}</p>')
    if client_name:
        parts.append(f'<p class="for">עבור {_esc(client_name)}</p>')
    if notice:
        parts.append(f'<div class="notice">{_esc(notice)}</div>')
    if errors:
        parts.append(f'<div class="errbox" role="alert">יש {len(errors)} '
                     f'{"שדה שדורש" if len(errors) == 1 else "שדות שדורשים"} תיקון — מסומנים באדום.</div>')
    parts.append('<div class="progress" aria-hidden="true"><div class="meta"><span id="steplabel"></span>'
                 '<span id="stepcount"></span></div><div class="bar"><i id="fill"></i></div></div>')
    flags = (" data-preview" if preview else "") + (" data-prefilled" if answers else "")
    parts.append(f'<form id="qform" method="post" action="{_esc(action)}" novalidate'
                 f' data-draft="{_esc(draft_key or defn.get("id"))}"{flags}>')
    for s in sections:
        desc = f'<p class="desc">{_esc(s["description"])}</p>' if s.get("description") else ""
        qs = "".join(_question_html(q, answers.get(q["key"]), errors.get(q["key"], ""))
                     for q in s.get("questions") or [])
        parts.append(f'<fieldset class="step" data-title="{_esc(s.get("title"))}">'
                     f'<legend>{_esc(s.get("title"))}</legend>{desc}{qs}</fieldset>')
    parts.append('<div class="nav"><button type="button" class="btn ghost js-only" id="prev">הקודם</button>'
                 '<span class="saved" id="saved"></span>'
                 '<span><button type="button" class="btn primary js-only" id="next">הבא</button>'
                 f'<button type="submit" class="btn primary" id="send">{"שליחה" if not preview else "שליחה (תצוגה)"}</button>'
                 '</span></div></form>')
    parts.append('<p class="for" style="margin-top:14px">שדות המסומנים ב-<span class="req">*</span> הם חובה.</p>')
    return _page(str(defn.get("title") or "שאלון"), "".join(parts), script=True)


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
    body = questionnaire.to_document_html(defn.get("title") or "שאלון", name, snap, answers)
    doc_html = f'<html><head><meta charset="utf-8"></head><body dir="rtl">{body}</body></html>'
    doc: dict[str, str] = {"url": ""}
    try:
        doc = deliverables.save_html_doc(crm, {**client, "id": client_id},
                                         f"{defn.get('title')} — {name}", doc_html, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 - the answers are stored below either way
        auto.log_action("questionnaire_doc_failed", "error", client_id=client_id, detail=str(exc))

    questionnaire_store.record_answer(client_id, name, defn, answers, doc_url=doc["url"])
    crm.append_automation_log(client_id, "📋 השאלון מולא" + (f" — התשובות בדרייב:\n{doc['url']}" if doc["url"] else ""))
    auto.log_action("questionnaire_answered", client_id=client_id,
                    detail=str(defn.get("title") or ""), url=doc["url"] or None)

    if questionnaire.social_profiles(snap, answers):
        try:
            tasks.dispatch("social_prep", client_id=client_id, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - never fail the client's submission over it
            log.warning("social_prep_dispatch_failed", extra={"client_id": client_id, "error": str(exc)})
