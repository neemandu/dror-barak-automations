# Credentials — what we need, how to get it, where it goes

Every value below goes in **`.env`** in the project root (copy `.env.example` to
`.env` first). `.env` is gitignored and never committed — the public repo only
ever contains `.env.example`, which has the key names and no values.

Nothing here is needed to run `--dry-run` or the test suite. Each automation only
needs the systems it actually touches, so you can go live one automation at a time.

**Who gets it:** "Dror" = only he can obtain it (it's his account). "Us" = we set it
up, no action needed from him.

| # | System | Value(s) | Who |
|---|---|---|---|
| 1 | ClickUp (CRM) | `CLICKUP_API_TOKEN`, `CLICKUP_LIST_ID`, `CLICKUP_WEBHOOK_SECRET` | Dror / us |
| 2 | Morning | `MORNING_API_KEY`, `MORNING_API_SECRET` | Dror |
| 3 | Green API (WhatsApp) | `GREEN_API_ID_INSTANCE`, `GREEN_API_TOKEN_INSTANCE` | Dror |
| 4 | Google Workspace | `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_IMPERSONATE_SUBJECT`, Drive ids, `QUESTIONNAIRE_URL` | Us + one share from Dror |
| 5 | Meta Ads | `META_ACCESS_TOKEN`, `META_AD_ACCOUNT_ID` | Dror |
| 6 | Anthropic | `ANTHROPIC_API_KEY` | Us or Dror (billing) |
| 7 | Signing page | `SIGN_BASE_URL`, `SIGN_LINK_SECRET` | Us (needs a domain) |
| 8 | Recipients | `DROR_WHATSAPP`, `DROR_EMAIL` | Dror |

---

## 1. ClickUp — the CRM

Powers the lead→client lifecycle: status changes here trigger automations, and
results (Drive links, signed contract, run log) are written back here.

**API token** — ClickUp → avatar bottom-left → **Settings → Apps → API Token →
Generate**. Starts with `pk_`.
→ `CLICKUP_API_TOKEN`

**List id** — open the clients list in the browser. The URL looks like
`app.clickup.com/9012345/v/li/901100123456`; the last number is the id.
→ `CLICKUP_LIST_ID`

**Webhook secret** — invent a long random string; ClickUp echoes it back when we
register the webhook. Used to prove inbound webhooks are really from ClickUp.
→ `CLICKUP_WEBHOOK_SECRET`

> **Still to decide:** the custom fields on the client task (primary status,
> secondary status, monthly price, Drive path, signed contract, Morning status).
> The Taskey→ClickUp migration tool maps to them, but the field ids only exist once
> the list is built. See `src/tools/migrate_taskey_to_clickup.py`.

## 2. Morning (חשבונית ירוקה) — invoices and payment requests

1. Log in to `app.greeninvoice.co.il`
2. **הגדרות → כללי → ממשק למתכנתים (API)**
3. **יצירת מפתח / Generate**
4. Copy both values. ⚠️ The **Secret is displayed once only** — save it immediately.

→ `MORNING_API_KEY`, `MORNING_API_SECRET`

## 3. Green API — WhatsApp

1. Register at `green-api.com` (Google sign-in works)
2. Create a new **Instance**
3. Copy **idInstance** and **apiTokenInstance**
4. Scan the **QR** shown on the instance screen from the WhatsApp of the number that
   messages clients (WhatsApp → Settings → **Linked devices** → Link a device)

→ `GREEN_API_ID_INSTANCE`, `GREEN_API_TOKEN_INSTANCE`

> The number stays linked as long as that phone stays online. If Dror logs the
> device out, messages stop until it's re-scanned.

## 4. Google Workspace — Contacts, Drive, Forms

We do the technical setup: create a Google Cloud project, enable the Drive /
People / Forms APIs, create a **service account**, download its JSON key, and turn
on domain-wide delegation so it can act as Dror.

**From Dror we need exactly one thing:** share the **clients parent folder** in
Drive with the service-account email we send him, as **Editor**.

→ `GOOGLE_SERVICE_ACCOUNT_FILE` (path to the JSON key — keep it outside the repo)
→ `GOOGLE_IMPERSONATE_SUBJECT` (Dror's Workspace email)
→ `DRIVE_CLIENTS_PARENT_ID`, `DRIVE_TEMPLATE_IDS`, `DRIVE_DEFAULT_PARENT_ID` — open
each folder in Drive; the id is the last part of the URL
→ `QUESTIONNAIRE_URL` — the public link to the Google Form questionnaire

## 5. Meta Ads — campaign numbers for the monthly report

1. `business.facebook.com` → **Business Settings**
2. **Users → System Users → Add**, name it e.g. `Automation`
3. **Assign Assets** → the **ad account**, with view or manage access
4. **Generate Token** → tick **`ads_read`** → copy

→ `META_ACCESS_TOKEN`
→ `META_AD_ACCOUNT_ID` — the ad account id including the `act_` prefix

> System-user tokens are long-lived; a personal token expires in ~60 days. Use the
> system user.

## 6. Anthropic — the AI features

`console.anthropic.com` → **Settings → API Keys → Create Key**. Starts with
`sk-ant-`. Used by the social-media prep report, the strategy bot, and the campaign
recommendations.

→ `ANTHROPIC_API_KEY`

> Decide whose card this sits on. If it's Dror's, he creates the key; if it's ours,
> the usage is billed to us and rebilled.

## 7. Signing page — replaces Fillout

We host the page that clients open to sign their quote, so there is no Fillout
account and no monthly fee. It needs:

→ `SIGN_BASE_URL` — a public **HTTPS** address clients can reach, e.g.
  `https://sign.dror-barak.co.il`. Needs a domain (or subdomain) and a TLS
  certificate.
→ `SIGN_LINK_SECRET` — generate with:
  `python -c "import secrets;print(secrets.token_urlsafe(48))"`

> **Open decision:** where this is hosted. It must be online whenever a client might
> sign — a laptop won't do.

## 8. Recipients

→ `DROR_WHATSAPP` — international format, digits only, no `+`, e.g. `972501234567`
→ `DROR_EMAIL` — for report approvals and strategy notifications

---

## Handling these safely

- Put values **only** in `.env`. Never in code, never in a commit, never in the
  public repo.
- Don't send tokens over WhatsApp or email. Use a shared password file in Drive
  restricted to Dror + us — **not** "anyone with the link".
- If a token is ever pasted somewhere public, revoke and regenerate it. Every system
  above lets you revoke.
- Check that everything loaded without exposing values:
  `python -c "from src.lib import config; config.load_dotenv(); import os; print({k: bool(os.environ.get(k)) for k in ['CLICKUP_API_TOKEN','MORNING_API_KEY','GREEN_API_ID_INSTANCE','ANTHROPIC_API_KEY']})"`
