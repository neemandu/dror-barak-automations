"""The signing page — what Dror's client actually opens.

Replaces Fillout. Two routes:

  GET  /sign?t=<token>   render the contract with a signature pad
  POST /sign?t=<token>   capture the signature, produce the PDF, file it

On signing:
  1. render the contract with the client's details and their drawn signature
  2. append the audit trail (time, IP, document hash) into the document itself
  3. convert to PDF via Drive
  4. store it in the client's Drive folder
  5. attach it to the `חוזה חתום` field on the ClickUp task
  6. move the client's secondary status to `חתם`, which is what triggers onboarding

The page also collects the details the contract needs but ClickUp does not hold —
ת.ז/ח.פ, address, email. The client knows their own company number better than
Dror does, and a contract cannot be enforced without it. Those are written back to
ClickUp so they are not asked for twice.

The page is public by necessity: the client has no account. The token in the URL
is the credential — see src/lib/signing.py.
"""

from __future__ import annotations

import html
import json
from typing import Any, Optional
from urllib.parse import parse_qs

from . import ui
from .lib import client_folder, config, contract, idempotency, pdf, signing
from .lib.clients.crm import CrmClient
from .lib.logging_setup import get_logger

log = get_logger("sign", "page")

# Details the contract needs that ClickUp has no field for. The client fills them.
ASK_CLIENT = [
    ("client_business_id", "ת.ז / עוסק מורשה / ח.פ", "text"),
    ("client_address", "כתובת", "text"),
    ("client_email", "דוא״ל", "email"),
    ("client_phone", "טלפון", "tel"),
]


class SignError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


# ------------------------------------------------------------------ rendering

PAGE_CSS = """
html.public body { background: #eef1f5; }
.sbar { position: sticky; top: 0; z-index: 40; border-bottom: 1px solid var(--border);
  background: color-mix(in srgb, #ffffff 86%, transparent); backdrop-filter: saturate(180%) blur(12px);
  -webkit-backdrop-filter: saturate(180%) blur(12px); }
.sbar-in { max-width: 880px; margin: 0 auto; padding: 0 20px; height: 60px; display: flex; align-items: center; gap: 12px; }
.sbar .title { font-weight: 700; font-size: 15px; }
.sbar .spacer { flex: 1; }
.wrap { max-width: 880px; margin: 0 auto; padding: 26px 20px 120px; }
.intro { padding: 22px 24px; margin-bottom: 18px; }
.intro h1 { margin: 0 0 6px; font-size: 22px; font-weight: 800; letter-spacing: -.01em; }
.intro p { margin: 0; color: var(--fg-muted); }
.flow { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 18px; }
.flow-step { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 12px; background: var(--surface-2);
  border: 1px solid var(--border); font-size: 14px; font-weight: 500; color: var(--fg-muted); transition: all var(--d3) var(--ease); }
.flow-step .n { width: 24px; height: 24px; border-radius: 50%; flex: none; display: grid; place-items: center; font-size: 12px; font-weight: 700;
  background: var(--surface-active); color: var(--fg-2); transition: all var(--d3) var(--ease); }
.flow-step .n svg { display: none; width: 13px; height: 13px; stroke-width: 3; }
.flow-step.is-done { color: var(--success-ink); background: var(--success-soft); border-color: var(--success-border); }
.flow-step.is-done .n { background: var(--success); color: #fff; animation: pop var(--d3) var(--spring); }
.flow-step.is-done .n b { display: none; }
.flow-step.is-done .n svg { display: block; }
.paper { background: #fff; border-radius: 16px; padding: 44px 52px; margin-bottom: 18px;
  box-shadow: 0 1px 2px rgba(16, 24, 40, .05), 0 18px 40px -22px rgba(16, 24, 40, .25); }
.brand-banner { border-radius: 12px; margin: 0 0 28px; padding: 26px 30px; background: var(--brand-grad); display: flex; align-items: center; }
.brand-logo { max-width: 240px; height: auto; display: block; }
.brand-footer { margin-top: 34px; text-align: center; }
.brand-footer img { max-width: 100%; height: auto; }
.contract { color: #1d2939; }
.contract h1 { font-size: 26px; font-weight: 800; letter-spacing: -.01em; margin: 0 0 6px; }
.contract h2 { font-size: 17px; font-weight: 700; margin: 30px 0 8px; }
.contract h3 { font-size: 15px; font-weight: 700; margin: 18px 0 6px; }
.contract p, .contract li { font-size: 14.5px; line-height: 1.85; }
.contract hr { border: 0; border-top: 1px solid var(--border); margin: 26px 0; }
.contract .lead { color: var(--fg-muted); font-size: 15.5px; }
.filled { background: #fff4d6; padding: 1px 5px; border-radius: 5px; font-weight: 600; transition: background var(--d3); }
.filled.pulse { animation: flash .9s var(--ease); }
.parties { display: flex; gap: 32px; flex-wrap: wrap; }
.party { flex: 1; min-width: 220px; }
table.annex { width: 100%; border-collapse: separate; border-spacing: 0; margin: 14px 0; font-size: 13.5px; border: 1px solid var(--border);
  border-radius: 10px; overflow: hidden; }
table.annex th, table.annex td { padding: 10px 12px; text-align: right; border-bottom: 1px solid var(--border-soft); }
table.annex th { background: var(--surface-2); font-weight: 600; color: var(--fg-2); }
table.annex tr:last-child td { border-bottom: 0; }
table.annex .total { font-weight: 700; background: var(--surface-2); }
.signatures { display: flex; gap: 32px; flex-wrap: wrap; }
.sig { flex: 1; min-width: 240px; }
.sig-box { border-bottom: 1.5px solid #1d2939; height: 70px; margin: 6px 0; }
.panel { padding: 24px; margin-bottom: 16px; }
.panel h2 { display: flex; align-items: center; gap: 10px; margin: 0 0 4px; font-size: 18px; font-weight: 700; }
.panel h2 .n { width: 26px; height: 26px; border-radius: 8px; display: grid; place-items: center; background: var(--fg); color: #fff; font-size: 13px; }
.panel .why { margin: 0 0 18px; color: var(--fg-muted); font-size: 14px; }
.fields { display: grid; grid-template-columns: 1fr 1fr; gap: 14px 16px; }
.fields .input { height: 48px; font-size: 16px; border-radius: 12px; border-width: 1.5px; box-shadow: none; }
.fields .input[type=email], .fields .input[type=tel] { direction: ltr; text-align: left; }
.pad-wrap { position: relative; border-radius: 14px; border: 1.5px dashed var(--border-strong); background: var(--surface-2);
  transition: border-color var(--d2), background var(--d2); overflow: hidden; }
.pad-wrap.has-ink { border-style: solid; border-color: var(--brand); background: #fff; }
.pad-wrap canvas { display: block; width: 100%; height: 190px; touch-action: none; cursor: crosshair; }
.pad-hint { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px;
  color: var(--fg-subtle); font-size: 15px; pointer-events: none; transition: opacity var(--d3) var(--ease), transform var(--d3) var(--ease); }
.pad-hint .icon { width: 26px; height: 26px; }
.pad-wrap.has-ink .pad-hint { opacity: 0; transform: scale(.96); }
.pad-line { position: absolute; left: 28px; right: 28px; bottom: 40px; border-top: 1px dashed var(--border-strong); pointer-events: none; }
.pad-tools { display: flex; align-items: center; gap: 10px; margin-top: 10px; min-height: 30px; }
.pad-ok { display: inline-flex; align-items: center; gap: 6px; color: var(--success-ink); font-size: 13.5px; font-weight: 600;
  opacity: 0; transition: opacity var(--d3); }
.pad-ok.on { opacity: 1; }
.submit { padding: 22px 24px; }
.missing { margin: 12px 0 0; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: 13.5px; color: var(--fg-muted); }
.missing .badge { background: var(--warn-soft); color: var(--warn-ink); }
.note { margin: 14px 0 0; color: var(--fg-subtle); font-size: 12.5px; display: flex; gap: 6px; align-items: flex-start; }
.note .icon { margin-top: 2px; }
.gobar { position: fixed; z-index: 50; bottom: 18px; left: 50%; transform: translate(-50%, 0); display: flex; align-items: center; gap: 12px;
  padding: 8px 8px 8px 18px; border-radius: 99px; background: #101828; color: #fff; font-size: 14px; font-weight: 500;
  box-shadow: var(--sh-xl); transition: transform var(--d3) var(--ease), opacity var(--d3); white-space: nowrap; }
.gobar.is-hidden { transform: translate(-50%, 140%); opacity: 0; pointer-events: none; }
.gobar .btn { --h: 38px; border-radius: 99px; }
.center { text-align: center; }
.done-wrap { max-width: 560px; margin: 8vh auto 0; padding: 40px 30px; text-align: center; }
.done-wrap h1 { margin: 20px 0 8px; font-size: 26px; font-weight: 800; }
.done-wrap p { margin: 0 auto; color: var(--fg-muted); max-width: 400px; }
.check-anim { width: 88px; height: 88px; margin: 0 auto; }
.check-anim circle { fill: var(--success-soft); stroke: var(--success); stroke-width: 3; stroke-dasharray: 252; stroke-dashoffset: 252;
  animation: draw .7s var(--ease) forwards; }
.check-anim path { fill: none; stroke: var(--success); stroke-width: 5; stroke-linecap: round; stroke-linejoin: round;
  stroke-dasharray: 60; stroke-dashoffset: 60; animation: draw .4s .55s var(--ease) forwards; }
.fail-icon { width: 64px; height: 64px; margin: 0 auto; border-radius: 50%; display: grid; place-items: center;
  background: var(--danger-soft); color: var(--danger-ink); box-shadow: 0 0 0 10px color-mix(in srgb, var(--danger-soft) 55%, transparent); }
@keyframes draw { to { stroke-dashoffset: 0; } }
@media (max-width: 640px) {
  .wrap { padding: 16px 12px 110px; }
  .paper { padding: 26px 20px; border-radius: 14px; }
  .flow { grid-template-columns: 1fr 1fr 1fr; gap: 6px; }
  .flow-step { flex-direction: column; gap: 6px; padding: 10px 6px; font-size: 12.5px; text-align: center; }
  .fields { grid-template-columns: 1fr; }
  .panel, .submit { padding: 20px 18px; }
  .sbar .lockbadge span { display: none; }
}
"""


def _page(title: str, body: str, *, script: str = "") -> str:
    bar = (f'<header class="sbar"><div class="sbar-in">{ui.BRAND_MARK}<span class="title">דרור ברק</span>'
           f'<span class="spacer"></span><span class="badge badge-ok lockbadge">{ui.icon("lock", 12)}'
           '<span>חתימה מאובטחת</span></span></div></header>')
    return ui.document(title, bar + body, kind="public", css=PAGE_CSS, script=script)


# SigningError messages are English because they are also what the logs say. The
# client reads Hebrew, so the page translates the ones a client can actually hit.
_CLIENT_MESSAGES = {
    "this signing link is not valid": "הקישור אינו תקין.",
    "malformed signing link": "הקישור אינו תקין. ייתכן שהוא נקטע בהעתקה.",
    "this signing link has expired": "תוקף הקישור פג.",
    "the signature is missing or not a PNG image": "לא התקבלה חתימה. אנא חתמו בתיבה ונסו שוב.",
    "the signature image is corrupt": "החתימה לא נקלטה כראוי. אנא נסו לחתום שוב.",
    "the signature image is not a PNG": "החתימה לא נקלטה כראוי. אנא נסו לחתום שוב.",
    "the signature appears to be blank": "תיבת החתימה ריקה. אנא חתמו ונסו שוב.",
}


def client_message(message: str) -> str:
    """The Hebrew a client should see for ``message``; unknown text passes through
    only if it is already Hebrew, otherwise a generic line (never a stack detail)."""
    if message in _CLIENT_MESSAGES:
        return _CLIENT_MESSAGES[message]
    if any("\u0590" <= ch <= "\u05ff" for ch in message):
        return message
    return "משהו השתבש בפתיחת הקישור."


def error_page(message: str) -> str:
    return _page("הקישור לא נפתח", f"""<main class="wrap"><div class="card done-wrap reveal">
      <div class="fail-icon">{ui.icon("alert", 28)}</div><h1>הקישור לא נפתח</h1>
      <p>{_esc(client_message(message))}</p>
      <p style="margin-top:10px;font-size:14px">אפשר לפנות לדרור ברק ונשלח קישור חדש.</p></div></main>""")


def done_page(link: str = "") -> str:
    extra = (f'<a class="btn btn-primary btn-lg" style="margin-top:24px" href="{_esc(link)}" target="_blank" rel="noopener">'
             f'{ui.icon("download", 17)}<span>להורדת ההסכם החתום</span></a>' if link else "")
    check = ('<svg class="check-anim" viewBox="0 0 84 84" aria-hidden="true"><circle cx="42" cy="42" r="40"/>'
             '<path d="M26 43l11 11 21-23"/></svg>')
    return _page("ההסכם נחתם", f"""<main class="wrap"><div class="card done-wrap reveal">{check}
        <h1>ההסכם נחתם בהצלחה</h1>
        <p>עותק חתום נשמר ונשלח לדרור. תודה, ומתחילים לעבוד!</p>{extra}</div></main>""")


def _form_html(fields: dict[str, str]) -> str:
    """Inputs for the details we still need. Prefilled from ClickUp where known."""
    rows = ""
    for key, label, kind in ASK_CLIENT:
        value = fields.get(key) or ""
        auto = {"email": ' autocomplete="email"', "tel": ' autocomplete="tel"'}.get(kind, "")
        rows += (f'<label class="field"><span class="label">{_esc(label)}</span>'
                 f'<input class="input" name="{key}" type="{kind}" value="{_esc(value)}" required '
                 f'data-field="{key}"{auto}></label>')
    return f"""<section class="card panel reveal" id="details">
      <h2><span class="n">2</span>פרטי הלקוח</h2>
      <p class="why">הפרטים מופיעים בהסכם ומזהים אתכם כצד לו. הם מתעדכנים במסמך תוך כדי הקלדה.</p>
      <div class="fields">{rows}</div></section>"""


SIGN_JS = r"""
(function () {
  var form = document.getElementById('f'), go = document.getElementById('go'), sig = document.getElementById('sig');
  var inputs = Array.prototype.slice.call(document.querySelectorAll('input[data-field]'));
  var steps = {read: document.getElementById('s-read'), details: document.getElementById('s-details'), sign: document.getElementById('s-sign')};
  var drawn = false, readDone = false;

  // Live-bind the form inputs into the contract text above, with a brief highlight.
  inputs.forEach(function (input) {
    input.addEventListener('input', function () {
      var target = document.querySelector('[data-bind="' + input.name + '"]');
      if (target) { target.textContent = input.value || '―――'; target.classList.remove('pulse'); void target.offsetWidth; target.classList.add('pulse'); }
      update();
    });
  });

  // Signature pad: pointer events, smoothed strokes, retina-sharp.
  var wrap = document.getElementById('padwrap'), pad = document.getElementById('pad'), ctx = pad.getContext('2d');
  function size() {
    var ratio = window.devicePixelRatio || 1, w = pad.clientWidth, h = pad.clientHeight;
    var keep = drawn ? pad.toDataURL() : null;
    pad.width = w * ratio; pad.height = h * ratio; ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.strokeStyle = '#101828';
    if (keep) { var img = new Image(); img.onload = function () { ctx.drawImage(img, 0, 0, w, h); }; img.src = keep; }
  }
  size(); window.addEventListener('resize', size);
  var drawing = false, last = null, mid = null;
  function at(e) { var r = pad.getBoundingClientRect(); return {x: e.clientX - r.left, y: e.clientY - r.top, t: Date.now()}; }
  pad.addEventListener('pointerdown', function (e) { drawing = true; pad.setPointerCapture(e.pointerId); last = at(e); mid = last;
    ctx.beginPath(); ctx.arc(last.x, last.y, 1.1, 0, Math.PI * 2); ctx.fillStyle = '#101828'; ctx.fill(); e.preventDefault(); });
  pad.addEventListener('pointermove', function (e) {
    if (!drawing) return; var p = at(e), m = {x: (last.x + p.x) / 2, y: (last.y + p.y) / 2};
    var speed = Math.hypot(p.x - last.x, p.y - last.y) / Math.max(1, p.t - last.t);
    ctx.lineWidth = Math.max(1.4, Math.min(3.2, 3.4 - speed * 1.2));
    ctx.beginPath(); ctx.moveTo(mid.x, mid.y); ctx.quadraticCurveTo(last.x, last.y, m.x, m.y); ctx.stroke();
    last = p; mid = m;
    if (!drawn) { drawn = true; wrap.classList.add('has-ink'); update(); }
    e.preventDefault();
  });
  ['pointerup', 'pointercancel', 'pointerleave'].forEach(function (n) { pad.addEventListener(n, function () { drawing = false; }); });
  document.getElementById('clear').addEventListener('click', function () {
    ctx.clearRect(0, 0, pad.width, pad.height); drawn = false; wrap.classList.remove('has-ink'); update(); });

  function update() {
    var missing = inputs.filter(function (i) { return !i.value.trim(); }).map(function (i) { return i.closest('label').querySelector('.label').textContent; });
    if (!drawn) missing.push('חתימה');
    steps.details.classList.toggle('is-done', inputs.every(function (i) { return i.value.trim(); }));
    steps.sign.classList.toggle('is-done', drawn);
    document.getElementById('padok').classList.toggle('on', drawn);
    go.disabled = missing.length > 0;
    var box = document.getElementById('missing'); box.innerHTML = '';
    if (missing.length) { box.appendChild(document.createTextNode('נשאר: '));
      missing.forEach(function (m) { var b = document.createElement('span'); b.className = 'badge'; b.textContent = m; box.appendChild(b); }); }
  }

  // Reading counts once the contract's end has been on screen.
  var sign = document.getElementById('signpanel'), gobar = document.getElementById('gobar');
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (es) { es.forEach(function (e) { if (e.isIntersecting && !readDone) { readDone = true; steps.read.classList.add('is-done'); } }); })
      .observe(document.getElementById('paper-end'));
    new IntersectionObserver(function (es) { es.forEach(function (e) {
      gobar.classList.toggle('is-hidden', e.isIntersecting || e.boundingClientRect.top < 0); }); }, {threshold: .05})
      .observe(document.getElementById('details'));
  } else { steps.read.classList.add('is-done'); gobar.classList.add('is-hidden'); }
  document.getElementById('jump').addEventListener('click', function () {
    document.getElementById('details').scrollIntoView({behavior: 'smooth', block: 'start'}); });

  form.addEventListener('submit', function (e) {
    if (!drawn) { e.preventDefault(); wrap.classList.remove('shake'); void wrap.offsetWidth; wrap.classList.add('shake'); return; }
    sig.value = pad.toDataURL('image/png');
    go.classList.add('is-loading'); go.disabled = true;
  });
  update();
})();
"""


def render_sign_page(
    token: str, fields: dict[str, str], error: str = ""
) -> str:
    """The contract, a form for the missing details, and a signature pad.

    The form's action is **relative** (``?t=...``). The page is served under a
    stage prefix — ``/dev/sign`` — so an absolute ``/sign`` posts to a path that
    does not exist, and API Gateway answers 404 *after* the client has signed.
    Relative keeps whatever path the page was loaded from, and works unchanged
    under /prod or a custom domain.
    """
    # Placeholders the client is about to fill get a visible marker rather than a
    # blank, so the document never looks like it has holes in it.
    display = dict(fields)
    for key, label, _ in ASK_CLIENT:
        if not display.get(key):
            display[key] = "―――"
    body = contract.render(
        display, signatures={"provider_signature": "", "client_signature": ""}
    )
    # Tag the spans the form should live-update.
    for key, _, _ in ASK_CLIENT:
        body = body.replace(
            f'<span class="filled">{html.escape(display[key])}</span>',
            f'<span class="filled" data-bind="{key}">{html.escape(display[key])}</span>',
            1,
        )

    err = (f'<div class="alert alert-danger shake" role="alert" style="margin-bottom:16px">{ui.icon("alert")}'
           f'<span>{_esc(client_message(error))}</span></div>' if error else "")
    name = fields.get("client_name") or ""
    hello = f"שלום {_esc(name)}," if name else "שלום,"
    flow = "".join(
        f'<div class="flow-step" id="s-{key}"><span class="n"><b>{n}</b>{ui.icon("check", 13)}</span><span>{label}</span></div>'
        for n, (key, label) in enumerate((("read", "קריאה"), ("details", "פרטים"), ("sign", "חתימה")), start=1))
    return _page("הסכם התקשרות - לחתימה", f"""
<main class="wrap">
  {err}
  <section class="card intro reveal"><h1>{hello} ההסכם מוכן לחתימה</h1>
    <p>קוראים את ההסכם, משלימים כמה פרטים וחותמים. לוקח בערך שתי דקות.</p>
    <div class="flow">{flow}</div></section>
  <form method="post" action="?t={_esc(token)}" id="f">
    <article class="paper reveal" style="--i:1">{body}<span id="paper-end"></span></article>
    {_form_html(fields)}
    <section class="card panel" id="signpanel">
      <h2><span class="n">3</span>חתימה</h2>
      <p class="why">חתמו בתוך המסגרת, עם העכבר או האצבע.</p>
      <div class="pad-wrap" id="padwrap"><canvas id="pad" aria-label="תיבת חתימה"></canvas><span class="pad-line"></span>
        <div class="pad-hint">{ui.icon("pen", 26)}<span>חתמו כאן</span></div></div>
      <div class="pad-tools"><button type="button" class="btn btn-sm" id="clear">{ui.icon("rotate", 14)}<span>ניקוי</span></button>
        <span class="pad-ok" id="padok">{ui.icon("check-circle", 15)}<span>החתימה נקלטה</span></span></div>
      <input type="hidden" name="signature" id="sig">
    </section>
    <section class="card submit">
      <button type="submit" class="btn btn-primary btn-lg btn-block" id="go" disabled>{ui.icon("pen", 17)}<span>חתימה על ההסכם</span></button>
      <div class="missing" id="missing"></div>
      <p class="note">{ui.icon("lock", 13)}<span>בלחיצה על הכפתור נרשמים מועד החתימה, כתובת ה-IP וטביעת אצבע
         דיגיטלית של נוסח ההסכם המוצג לך.</span></p>
    </section>
  </form>
</main>
<div class="gobar" id="gobar"><span>מוכנים לחתום?</span><button type="button" class="btn btn-primary" id="jump">
  <span>לפרטים ולחתימה</span>{ui.icon("arrow-down", 15)}</button></div>""", script=SIGN_JS)


# --------------------------------------------------------------------- logic


def _client_fields(client: dict[str, Any]) -> dict[str, str]:
    return contract.fields_from_client(
        client,
        price_strategy=client.get("price_strategy"),
        price_campaigns=client.get("price_campaigns"),
    )


def handle_get(token: str, dry_run: bool = False) -> str:
    """Render the signing page for a token."""
    client_id = signing.resolve(token)
    client = CrmClient(dry_run=dry_run).get_client(client_id)
    return render_sign_page(token, _client_fields(client))


def handle_post(
    token: str,
    form: dict[str, list[str]],
    *,
    ip: str = "",
    user_agent: str = "",
    dry_run: bool = False,
) -> str:
    """Capture a signature: render, convert, store, attach, advance the status."""
    client_id = signing.resolve(token)
    crm = CrmClient(dry_run=dry_run)
    client = crm.get_client(client_id)

    fields = _client_fields(client)
    # What the client typed wins: they know their own company number.
    for key, _, _ in ASK_CLIENT:
        supplied = (form.get(key) or [""])[0].strip()
        if supplied:
            fields[key] = supplied

    missing = contract.missing_for(fields)
    if missing:
        return render_sign_page(token, fields, error="חסרים פרטים: " + ", ".join(missing))

    try:
        signature_png = signing.decode_signature((form.get("signature") or [""])[0])
    except signing.SigningError as exc:
        return render_sign_page(token, fields, error=str(exc))

    # One signature per client. A double submit must not file two contracts.
    once = idempotency.guard("signed_contract", client_id)
    if not idempotency.claim(once):
        log.info("already_signed", extra={"client_id": client_id})
        return done_page()

    try:
        result = _finalise(crm, client_id, client, fields, signature_png,
                           ip=ip, user_agent=user_agent, dry_run=dry_run)
    except Exception:
        idempotency.release(once)  # let them try again
        raise
    return done_page(result.get("link", ""))


def _finalise(
    crm: CrmClient,
    client_id: str,
    client: dict[str, Any],
    fields: dict[str, str],
    signature_png: bytes,
    *,
    ip: str,
    user_agent: str,
    dry_run: bool,
) -> dict[str, Any]:
    import base64

    sig_tag = ('<img alt="חתימת הלקוח" style="max-height:70px" '
               f'src="data:image/png;base64,{base64.b64encode(signature_png).decode()}">')
    body = contract.render(fields, signatures={
        "provider_signature": "", "client_signature": sig_tag,
    })

    # Hash what the client actually saw, signature and all.
    record = signing.audit_record(client_id, body, ip=ip, user_agent=user_agent)
    document = (f'<html><head><meta charset="utf-8"></head><body dir="rtl">'
                f'{body}{signing.audit_html(record)}</body></html>')

    # Drive keeps the readable Hebrew name; ClickUp is given an ASCII one, because
    # it rejects non-ASCII filenames outright.
    name = f"הסכם חתום - {client.get('name') or client_id}.pdf"
    clickup_name = f"signed-contract-{client_id}.pdf"
    if dry_run:
        log.info("would_finalise", extra={"client_id": client_id, "name": name,
                                          "sha256": record["contract_sha256"]})
        return {"dry_run": True, "audit": record}

    pdf_bytes = pdf.html_to_pdf(document, name=name)

    # The contract belongs in the client's own folder. Signing happens before
    # onboarding — signing is what sets `חתם`, and `חתם` is what triggers
    # onboarding — so whoever arrives first creates it. This is idempotent, and
    # onboarding will find the same folder rather than make a second one.
    folder = client_folder.ensure(crm, {**client, "id": client_id}, dry_run=dry_run)
    stored = pdf.upload_pdf(pdf_bytes, name, folder["id"])
    link = stored.get("webViewLink", "")

    # Attach to ClickUp. `חוזה חתום` is an Attachment field, so the PDF itself
    # lands on the task rather than a link that could rot.
    attached = crm.attach_file(client_id, "signed_contract", pdf_bytes, clickup_name)

    # Only now advance the status: `חתם` is what triggers onboarding, and it must
    # not fire for a contract we failed to store.
    crm.update_fields(client_id, sub_status="signed")
    crm.append_automation_log(
        client_id,
        f"✍️ ההסכם נחתם על ידי הלקוח\n"
        f"מסמך: {link}\n"
        f"טביעת אצבע: {record['contract_sha256'][:16]}…\n"
        f"IP: {record['ip'] or 'לא נרשמה'}",
    )
    # No more reminders — they signed.
    signing.clear_pending(client_id)
    _notify_dror(client, pdf_bytes, record)
    log.info("signed", extra={"client_id": client_id, "link": link,
                             "sha256": record["contract_sha256"]})
    return {"link": link, "audit": record, "attached": bool(attached)}


def _notify_dror(client: dict[str, Any], pdf_bytes: bytes, record: dict[str, Any]) -> None:
    """Email Dror that a client just signed. Never fail the signing over it.

    A signed contract is the moment Dror most wants to know about, and a task
    comment he has to go looking for is not the same as it landing in his inbox.
    But the contract is already stored and the status already set — a notification
    that fails must not undo any of that.
    """
    to = config.get("DROR_EMAIL")
    if not to:
        log.info("no_dror_email", extra={"note": "DROR_EMAIL not set; skipping notify"})
        return
    try:
        from .lib import emails

        emails.send_template(
            "signed_notification", to,
            client_name=client.get("name") or client.get("id"),
            signed_at=record["signed_at"],
            fingerprint=record["contract_sha256"][:16] + "…",
            attachments=[emails.Attachment(
                filename="signed-contract.pdf", content=pdf_bytes)],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("dror_notify_failed", extra={"error": str(exc)})
