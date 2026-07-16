"""The strategy questionnaire — our own form, what the client fills after signing.

Two routes:

  GET  /questionnaire?t=<token>   render the form for a client
  POST /questionnaire?t=<token>   save the answers as a Google Doc, kick off the
                                  social-media analysis

Not Google Forms, deliberately (see :mod:`src.lib.questionnaire`): hosting it
ourselves means the client is known from the signed link, we act the instant it's
submitted, and the answers land as an editable Google Doc in the client's own
Drive folder — no matching, no polling, no Apps Script.

The token in the URL is the credential; the client has no account. Same scheme as
the signing page.
"""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import parse_qs

from .lib import client_folder, config, pdf, questionnaire, signing
from .lib.clients.crm import CrmClient
from .lib.logging_setup import get_logger

log = get_logger("questionnaire", "page")


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


PAGE_CSS = """
* { box-sizing: border-box; }
body { margin:0; background:#eef0f3; color:#14171a; font-family:system-ui,"Segoe UI",Arial,sans-serif; }
.sheet { max-width:720px; margin:24px auto; background:#fff; padding:36px 44px;
  border-radius:10px; box-shadow:0 1px 4px rgba(0,0,0,.12); }
.brand-banner { border-radius:8px; margin:0 0 24px; padding:24px 28px;
  background:linear-gradient(90deg,#00e5d0 0%,#00a8f0 45%,#2f7de1 100%);
  color:#fff; }
.brand-banner h1 { margin:0; font-size:22px; }
.brand-banner p { margin:6px 0 0; opacity:.92; font-size:14px; }
h2 { font-size:16px; margin:26px 0 4px; border-bottom:1px solid #e3e6ea; padding-bottom:6px; }
label { display:block; margin:14px 0 4px; font-size:14px; font-weight:600; }
label .req { color:#c0271c; }
.hint { font-weight:400; color:#5b6472; font-size:12px; }
input, textarea { width:100%; padding:10px 12px; border:1px solid #c9cfd6;
  border-radius:6px; font:inherit; }
textarea { min-height:80px; resize:vertical; }
button { font:inherit; padding:13px 30px; border-radius:8px; border:0; cursor:pointer;
  background:#0a7c42; color:#fff; font-weight:600; margin-top:22px; }
button:disabled { background:#9aa3ad; }
.err { background:#fdecea; border:1px solid #c0271c; color:#8c1d16; padding:12px 14px;
  border-radius:8px; margin-bottom:16px; }
.done { text-align:center; padding:56px 20px; }
.done .tick { font-size:56px; }
.note { color:#5b6472; font-size:12px; margin-top:16px; }
"""


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{_esc(title)}</title><style>{PAGE_CSS}</style></head><body>{body}</body></html>"""


def error_page(message: str) -> str:
    return _page("שגיאה", f'<div class="sheet"><div class="err">{_esc(message)}</div>'
                 '<p class="note">אם הקישור אינו פועל, אנא פנה/י לדרור ברק.</p></div>')


def done_page() -> str:
    return _page("תודה", '<div class="sheet"><div class="done"><div class="tick">✅</div>'
                 '<h1>תודה!</h1><p>קיבלנו את התשובות. דרור ייגש לבניית האסטרטגיה עבורך.</p>'
                 '</div></div>')


def _field_html(q: questionnaire.Question, value: str = "") -> str:
    req = ' <span class="req">*</span>' if q.required else ""
    hint = f' <span class="hint">— {_esc(q.hint)}</span>' if q.hint else ""
    label = f'<label>{_esc(q.label)}{req}{hint}</label>'
    if q.kind == "textarea":
        return label + f'<textarea name="{q.key}"{" required" if q.required else ""}>{_esc(value)}</textarea>'
    input_type = {"url": "url", "email": "email", "tel": "tel"}.get(q.kind, "text")
    return label + (f'<input type="{input_type}" name="{q.key}" value="{_esc(value)}"'
                    f'{" required" if q.required else ""}>')


def render_form(token: str, client: dict[str, Any], answers: dict[str, str] | None = None,
                error: str = "") -> str:
    answers = answers or {}
    name = client.get("name") or ""
    body = ['<div class="sheet">']
    if error:
        body.append(f'<div class="err">{_esc(error)}</div>')
    body.append('<div class="brand-banner"><h1>שאלון הכנה לבניית אסטרטגיה</h1>'
                f'<p>{_esc(name)} — כמה שאלות שיעזרו לנו לבנות לך אסטרטגיה מדויקת</p></div>')
    body.append(f'<form method="post" action="?t={_esc(token)}">')
    for section in questionnaire.SECTIONS:
        body.append(f"<h2>{_esc(section.title)}</h2>")
        for q in section.questions:
            body.append(_field_html(q, answers.get(q.key, "")))
    body.append('<button type="submit">שליחה</button>')
    body.append('<p class="note">שדות המסומנים ב-* הם חובה.</p>')
    body.append("</form></div>")
    return _page("שאלון הכנה לבניית אסטרטגיה", "".join(body))


def handle_get(token: str, dry_run: bool = False) -> str:
    client_id = signing.resolve(token)
    client = CrmClient(dry_run=dry_run).get_client(client_id)
    return render_form(token, client)


def handle_post(token: str, form: dict[str, list[str]], *, dry_run: bool = False) -> str:
    client_id = signing.resolve(token)
    crm = CrmClient(dry_run=dry_run)
    client = crm.get_client(client_id)

    answers = {q.key: (form.get(q.key) or [""])[0].strip()
               for q in questionnaire.all_questions()}

    missing = questionnaire.missing(answers)
    if missing:
        return render_form(token, client, answers,
                           error="חסרים שדות חובה: " + ", ".join(missing))

    _finalise(crm, client_id, client, answers, dry_run=dry_run)
    return done_page()


def _finalise(crm: CrmClient, client_id: str, client: dict[str, Any],
              answers: dict[str, str], *, dry_run: bool) -> None:
    name = client.get("name") or client_id
    doc_html = (f'<html><head><meta charset="utf-8"></head><body dir="rtl">'
                f'{questionnaire.to_document_html(name, answers)}</body></html>')

    if dry_run:
        log.info("would_save_questionnaire", extra={"client_id": client_id,
                 "answers": len([a for a in answers.values() if a])})
        _kick_off_social(client_id, answers, dry_run=True)
        return

    folder = client_folder.ensure(crm, {**client, "id": client_id}, dry_run=False)
    doc = pdf.html_to_google_doc(doc_html, f"שאלון אסטרטגיה — {name}", folder["id"])
    link = doc.get("webViewLink", "")

    crm.append_automation_log(
        client_id, f"📋 השאלון מולא ונשמר כמסמך בדרייב\n{link}")
    log.info("questionnaire_saved", extra={"client_id": client_id, "doc": link})

    _kick_off_social(client_id, answers, dry_run=False)


def _kick_off_social(client_id: str, answers: dict[str, str], *, dry_run: bool) -> None:
    """Hand the profile links to the last-5-videos analysis. Never fail the
    questionnaire over it — the answers are already saved."""
    profiles = questionnaire.social_profiles(answers)
    if not profiles:
        return
    try:
        from .automations import social_prep

        social_prep.run(client_id, dry_run=dry_run, profiles=profiles)
    except Exception as exc:  # noqa: BLE001
        log.warning("social_prep_failed", extra={"client_id": client_id, "error": str(exc)})
