# Credentials — what we need, how to get it, where it goes

> **Morning (חשבונית ירוקה) is out of scope.** Dror invoices clients himself; the system does not create documents or payment requests. Any Morning references left in older docs are historical.

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
| 3 | ManyChat (WhatsApp) | `MANYCHAT_API_KEY`, flow ids, `MANYCHAT_CONSENT_PHRASE` | Dror + us |
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

> **The clients list does not exist yet.** The workspace is still the default
> ClickUp template. ClickUp's API cannot create custom fields, so the list is built
> by hand in the UI once — **`docs/CLICKUP_SETUP.md` is the checklist**. No field
> ids are needed in `.env`: the code matches fields and statuses by name.
>
> Verify the setup any time with `python -m src.tools.check_clickup_crm`.


## 3. ManyChat — WhatsApp via the official Meta Business API

**API key** — ManyChat → **Settings → API → Generate your API Key**. Requires a paid
ManyChat plan.
→ `MANYCHAT_API_KEY`

**Flow ids** — one per outbound message we send (questionnaire, quote, payment,
onboarding, daily summary). Each flow's id is under its `⋯` menu in ManyChat.
→ `MANYCHAT_FLOW_QUESTIONNAIRE`, `MANYCHAT_FLOW_QUOTE`, `MANYCHAT_FLOW_PAYMENT`,
  `MANYCHAT_FLOW_ONBOARDING`, `MANYCHAT_FLOW_DAILY_SUMMARY`

**Consent phrase** — Meta requires proof of opt-in when a contact is created through
the API. This string is stored as that proof, so it must describe how the client
actually consented (e.g. "gave his number on the initial call and agreed to WhatsApp
updates"). It should be true, not decorative.
→ `MANYCHAT_CONSENT_PHRASE`

> ### Three constraints the official API brings that Green API did not
>
> **1. The 24-hour window.** Free-form messages can only be sent within 24 hours of
> the client's last inbound message. Every one of Dror's flows is business-initiated
> and therefore *outside* that window, so each one must be a **Meta-approved message
> template**, submitted in advance and reviewed by Meta.
>
> **2. Message wording is no longer freely editable.** Dror asked for an editable
> store of message bodies. Within a template, only the placeholder variables change —
> altering the wording itself means resubmitting the template to Meta for approval.
> `src/lib/whatsapp_templates.py` has to become a map of approved templates and their
> variables, not free text.
>
> **3. Per-conversation billing.** Meta charges per conversation by template category
> (utility vs marketing). Green API did not. The monthly payment-request run and the
> daily summary each carry a real per-message cost.
>
> **Also:** the number, once connected to ManyChat, can only be messaged through
> ManyChat's API — not Meta's Cloud API directly. That's a one-way door worth knowing
> before connecting it.

### Smoove → ManyChat webhook (T12)

A separate AWS Lambda receives a lead from **Smoove** (`{f_name, cellphone, msg}`),
finds/creates the ManyChat contact, and triggers the Flow named by `msg`.

**`msg` → Flow mapping.** `msg` names an env var: `msg="ai_agents"` resolves to
`MANYCHAT_FLOW_AI_AGENTS`. Add a new message type by adding a new
`MANYCHAT_FLOW_<MSG>` variable (uppercased, non-alphanumerics → `_`) — no code
change. An unmapped `msg` is **rejected**, never sent to a default.
→ `MANYCHAT_FLOW_AI_AGENTS` (and one per additional `msg` value)

**Webhook token (optional).** The Smoove endpoint is public. Left empty it is open —
anyone who finds the URL can create contacts and fire billed Flows. Set a random
string and configure Smoove to send it as the `X-Smoove-Token` header (or `?token=`
in the URL) to require it. Generate with:
`python -c "import secrets;print(secrets.token_urlsafe(32))"`
→ `SMOOVE_WEBHOOK_TOKEN`

The webhook URL to give Smoove is the `SmooveWebhookUrl` stack output after deploy.
Full operator steps: `docs/OPERATIONS.md` → "חיבור Smoove ל-WhatsApp (ManyChat)".

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
3. **Assign Assets → Ad Accounts** → tick **every client's ad account**, view
   access is enough. This is per-client: the report reads a different account for
   each client, so an account you skip becomes a client whose report fails.
4. **Generate Token** → tick **`ads_read`** → copy

→ `META_ACCESS_TOKEN`
→ `META_AD_ACCOUNT_ID` — used **only** by `python -m src.tools.check_meta`. The
  account each report uses comes from the `חשבון מודעות Meta` field on the client's
  ClickUp task, not from here.

> **Client accounts must live in — or be shared into — Dror's business.** A system
> user only sees assets its own business owns or has **partner access** to. Adding
> Dror's personal email to a client's account (the People tab) does *not* let his
> system user read it. The client's business must add Dror's business as a
> **Partner** with ad-account access. Confirm the account then appears under Assign
> Assets, and verify end to end with `python -m src.tools.check_meta --account act_…`.

> System-user tokens are long-lived; a personal token expires in ~60 days. Use the
> system user.

## 6. Anthropic — the AI features

`console.anthropic.com` → **Settings → API Keys → Create Key**. Starts with
`sk-ant-`. Used by the social-media prep report, the strategy bot, and the campaign
recommendations.

→ `ANTHROPIC_API_KEY`

> Decide whose card this sits on. If it's Dror's, he creates the key; if it's ours,
> the usage is billed to us and rebilled.

## 7a. The signing domain — sign.drorbrk.co.il

Two DNS records at **Wix** (the domain's DNS is hosted there: ns14/ns15.wixdns.net).
Wix Dashboard → Domains → drorbrk.co.il → Advanced → Edit DNS → CNAME → Add.

This is not cosmetic. A client asked to open
`e3670c4ju8.execute-api.eu-central-1.amazonaws.com`, sign a contract and type in
their ח.פ is being asked to do exactly what everyone is told never to do — and
spam filters take the same view.

  1. **Ownership** — proves the domain is Dror's, so AWS will issue a certificate.
     `python -m src.tools.check_domain` prints the exact record.
  2. **Routing** — points the subdomain at API Gateway.
     `python -m src.tools.setup_domain --apply` prints it, once the cert is issued.

Check progress any time (read-only):

```bash
python -m src.tools.check_domain
```

Then set `SIGN_BASE_URL=https://sign.drorbrk.co.il` and redeploy. Links become
`https://sign.drorbrk.co.il/sign?t=xxxxxxxx` — 42 characters, and recognisably Dror.

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
