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
* { box-sizing: border-box; }
body { margin:0; background:#eef0f3; color:#14171a; font-family:system-ui,"Segoe UI",Arial,sans-serif; }
.sheet { max-width:820px; margin:24px auto; background:#fff; padding:40px 48px;
  border-radius:10px; box-shadow:0 1px 4px rgba(0,0,0,.12); }
.brand-banner { border-radius:8px; margin:0 0 24px; padding:26px 30px;
  background:linear-gradient(90deg,#00e5d0 0%,#00a8f0 45%,#2f7de1 100%);
  display:flex; align-items:center; }
/* The logo is white-on-transparent: it only reads against the gradient. */
.brand-logo { max-width:250px; height:auto; display:block; }
.brand-footer { margin-top:34px; text-align:center; }
.brand-footer img { max-width:100%; height:auto; }
.contract h1 { font-size:24px; margin:0 0 6px; }
.contract h2 { font-size:17px; margin:26px 0 8px; }
.contract h3 { font-size:15px; margin:16px 0 6px; }
.contract p, .contract li { font-size:14px; line-height:1.75; }
.contract hr { border:0; border-top:1px solid #dfe3e8; margin:22px 0; }
.contract .lead { color:#444; font-size:15px; }
.filled { background:#fff6d6; padding:0 3px; border-radius:3px; font-weight:600; }
.parties { display:flex; gap:32px; flex-wrap:wrap; }
.party { flex:1; min-width:220px; }
table.annex { width:100%; border-collapse:collapse; margin:12px 0; font-size:13px; }
table.annex th, table.annex td { border:1px solid #dfe3e8; padding:8px; text-align:right; }
table.annex .total { font-weight:700; background:#f6f7f9; }
.signatures { display:flex; gap:32px; flex-wrap:wrap; }
.sig { flex:1; min-width:240px; }
.sig-box { border-bottom:1px solid #14171a; height:70px; margin:6px 0; }
.form { background:#f6f7f9; border:1px solid #dfe3e8; border-radius:8px; padding:20px; margin:24px 0; }
.form h2 { margin:0 0 4px; font-size:17px; }
.form .why { color:#5b6472; font-size:13px; margin:0 0 14px; }
.row { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:10px; }
.row label { flex:1; min-width:200px; font-size:13px; }
.row input { width:100%; padding:9px 11px; margin-top:4px; border:1px solid #c9cfd6;
  border-radius:6px; font:inherit; }
canvas { border:1px dashed #9aa3ad; border-radius:6px; background:#fff;
  touch-action:none; width:100%; height:170px; display:block; }
.actions { display:flex; gap:12px; align-items:center; margin-top:16px; flex-wrap:wrap; }
button { font:inherit; padding:11px 22px; border-radius:8px; border:0; cursor:pointer; }
button.primary { background:#0a7c42; color:#fff; font-weight:600; }
button.primary:disabled { background:#9aa3ad; cursor:not-allowed; }
button.link { background:transparent; color:#5b6472; text-decoration:underline; padding:6px; }
.err { background:#fdecea; border:1px solid #c0271c; color:#8c1d16; padding:12px 14px;
  border-radius:8px; margin-bottom:16px; font-size:14px; }
.done { text-align:center; padding:56px 20px; }
.done .tick { font-size:56px; }
.note { color:#5b6472; font-size:12px; }
"""


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{_esc(title)}</title><style>{PAGE_CSS}</style></head><body>{body}</body></html>"""


def error_page(message: str) -> str:
    return _page("שגיאה", f"""<div class="sheet"><div class="err">{_esc(message)}</div>
      <p class="note">אם הקישור אינו פועל, אנא פנה/י לדרור ברק.</p></div>""")


def done_page(link: str = "") -> str:
    extra = (f'<p><a href="{_esc(link)}" target="_blank" rel="noopener">להורדת ההסכם החתום</a></p>'
             if link else "")
    return _page("נחתם", f"""<div class="sheet"><div class="done">
        <div class="tick">✅</div>
        <h1>ההסכם נחתם בהצלחה</h1>
        <p>עותק חתום נשמר ונשלח לדרור. תודה!</p>{extra}
      </div></div>""")


def _form_html(fields: dict[str, str]) -> str:
    """Inputs for the details we still need. Prefilled from ClickUp where known."""
    rows = ""
    for key, label, kind in ASK_CLIENT:
        value = fields.get(key) or ""
        rows += (f'<label>{_esc(label)}'
                 f'<input name="{key}" type="{kind}" value="{_esc(value)}" required '
                 f'data-field="{key}"></label>')
    return f"""<div class="form">
      <h2>פרטי הלקוח</h2>
      <p class="why">הפרטים האלה מופיעים בהסכם ומזהים אותך כצד לו. הם מתעדכנים במסמך
         מעלה תוך כדי הקלדה.</p>
      <div class="row">{rows}</div>
    </div>"""


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

    err = f'<div class="err">{_esc(error)}</div>' if error else ""
    return _page("הסכם התקשרות — לחתימה", f"""
<div class="sheet">
  {err}
  <form method="post" action="?t={_esc(token)}" id="f">
    {body}
    {_form_html(fields)}
    <div class="form">
      <h2>חתימה</h2>
      <p class="why">חתמו בתוך המסגרת באמצעות העכבר או האצבע.</p>
      <canvas id="pad"></canvas>
      <input type="hidden" name="signature" id="sig">
      <div class="actions">
        <button type="button" class="link" id="clear">נקה חתימה</button>
        <button type="submit" class="primary" id="go" disabled>אני מאשר/ת וחותם/ת על ההסכם</button>
      </div>
      <p class="note">בלחיצה על הכפתור נרשמים מועד החתימה, כתובת ה־IP וטביעת אצבע
         דיגיטלית של נוסח ההסכם המוצג לך.</p>
    </div>
  </form>
</div>
<script>
(function () {{
  // Live-bind the form inputs into the contract text above.
  document.querySelectorAll('input[data-field]').forEach(function (input) {{
    input.addEventListener('input', function () {{
      var target = document.querySelector('[data-bind="' + input.name + '"]');
      if (target) target.textContent = input.value || '―――';
    }});
  }});

  var pad = document.getElementById('pad'), go = document.getElementById('go'),
      sig = document.getElementById('sig'), drawn = false;
  function size() {{
    var ratio = window.devicePixelRatio || 1, w = pad.clientWidth, h = pad.clientHeight;
    pad.width = w * ratio; pad.height = h * ratio;
    var c = pad.getContext('2d');
    c.scale(ratio, ratio); c.lineWidth = 2; c.lineCap = 'round'; c.strokeStyle = '#14171a';
  }}
  size(); window.addEventListener('resize', size);

  var ctx = pad.getContext('2d'), drawing = false;
  function pos(e) {{
    var r = pad.getBoundingClientRect(), t = e.touches ? e.touches[0] : e;
    return {{ x: t.clientX - r.left, y: t.clientY - r.top }};
  }}
  function start(e) {{ drawing = true; var p = pos(e); ctx.beginPath(); ctx.moveTo(p.x, p.y); e.preventDefault(); }}
  function move(e) {{
    if (!drawing) return;
    var p = pos(e); ctx.lineTo(p.x, p.y); ctx.stroke();
    drawn = true; go.disabled = false; e.preventDefault();
  }}
  function end() {{ drawing = false; }}
  ['mousedown','touchstart'].forEach(function (n) {{ pad.addEventListener(n, start); }});
  ['mousemove','touchmove'].forEach(function (n) {{ pad.addEventListener(n, move); }});
  ['mouseup','mouseleave','touchend'].forEach(function (n) {{ pad.addEventListener(n, end); }});

  document.getElementById('clear').addEventListener('click', function () {{
    ctx.clearRect(0, 0, pad.width, pad.height); drawn = false; go.disabled = true;
  }});

  document.getElementById('f').addEventListener('submit', function (e) {{
    if (!drawn) {{ e.preventDefault(); alert('נא לחתום לפני האישור'); return; }}
    sig.value = pad.toDataURL('image/png');
    go.disabled = true; go.textContent = 'רגע…';
  }});
}})();
</script>""")


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
    name = f"הסכם חתום — {client.get('name') or client_id}.pdf"
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
    log.info("signed", extra={"client_id": client_id, "link": link,
                             "sha256": record["contract_sha256"]})
    return {"link": link, "audit": record, "attached": bool(attached)}
