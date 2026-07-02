# SUMMARY — Dror Barak Automation Project

## What was built

A complete, tested code base that automates Dror's workflow **from lead to active
client**, replacing the manual sales / onboarding / service steps. Ten automations
plus shared infrastructure and a webhook receiver, all in Python.

| # | Automation | What it does | Trigger |
|---|---|---|---|
| T1 | Lead → Google Contacts | Saves a new lead's phone to Google Contacts | Webhook: new CRM lead |
| T2 | Send questionnaire | WhatsApp message with the Google Forms link | Webhook: status → initial meeting |
| T3 | Social-media prep report | Claude analyzes each social profile (last ~5 videos) into a meeting-prep report | Webhook: form submit / manual |
| T4 | Send quote + capture signature | Fillout quote with signature; on signing, stores PDF in Drive + link in CRM | Manual send + Fillout webhook |
| T5 | Onboarding (central) | Drive folder + template copy + Morning client + WhatsApp channel + CRM write-back | Webhook: status → signed |
| T6 | Monthly payment requests | Active clients → Morning payment request → WhatsApp payment link | Scheduled: 1st of month |
| T7 | Monthly campaign summary | Campaign analysis + AI recommendations → Drive → Dror for approval | Scheduled: month end / manual |
| T8 | Strategy bot | Audience/competitor/presence analysis → full strategy → Drive → notify Dror | Manual |
| T9 | ClickUp → Claude Code (gift) | Turns a ClickUp task into a Claude Code work brief | Webhook: ClickUp task |
| T10 | Daily summary | End-of-day WhatsApp digest of everything the automations did | Scheduled: end of day |

**Shared infrastructure** (`src/lib`): `.env` config loader, structured JSON
logging (UTF-8 safe for Hebrew), retry with exponential backoff + jitter on all API
calls, an append-only run-log (feeds the CRM log **and** the daily summary), an
editable WhatsApp template store, and dry-run-capable API clients for every system.
A stdlib webhook receiver (`src/webhook_server.py`) maps each inbound webhook to its
automation.

**Design principle — buildable and testable without credentials:** every API client
supports `--dry-run`, returning canned documented responses and recording the calls
it *would* make. The full pipeline runs end-to-end with no secrets; a 24-test suite
(`python -m pytest`) covers the infra and every automation in dry-run — **all
passing**. Live mode uses the same code against the real REST endpoints.

## How to deploy / run (this client's environment)

1. `python -m pip install -r requirements.txt`
2. `cp .env.example .env` and fill in the credentials below.
3. Prove everything works with no side effects: `python -m pytest` and/or any
   `python -m src.automations.<name> ... --dry-run`.
4. Go live per mode (details + cron examples in `README.md`):
   - **Scheduled** (cron / Windows Task Scheduler): T6 monthly, T7 month-end,
     T10 daily.
   - **Webhook**: `python -m src.webhook_server` behind an authenticated reverse
     proxy; point Taskey / Google Forms / Fillout / ClickUp webhooks at the routes.
   - **Manual**: run any module directly.

## Credentials I need from you

Fill these in `.env` (see `.env.example`). Nothing is needed for dry-run; each live
automation needs only its own systems' keys.

- **Taskey CRM** — `CRM_BASE_URL`, `CRM_API_TOKEN` **(⚠ biggest unknown — see below)**
- **Morning** — `MORNING_API_KEY`, `MORNING_API_SECRET`
- **Green API (WhatsApp)** — `GREEN_API_ID_INSTANCE`, `GREEN_API_TOKEN_INSTANCE`
- **Fillout** — `FILLOUT_API_KEY`, `FILLOUT_QUOTE_FORM_ID`
- **Google** — `GOOGLE_ACCESS_TOKEN` (OAuth/service account), `DRIVE_CLIENTS_PARENT_ID`,
  `DRIVE_TEMPLATE_IDS`, `QUESTIONNAIRE_URL`
- **ClickUp** — `CLICKUP_API_TOKEN` (+ optional `CLAUDE_CODE_CMD`)
- **Anthropic** — `ANTHROPIC_API_KEY`
- **Recipients** — `DROR_WHATSAPP`

## Open Questions for the client

Full list in `TASKS.md`. The ones that most affect go-live:

1. **Taskey API (critical).** Does Taskey expose a REST API and outbound webhooks on
   status change? Need base URL, auth, and field/status ids. The CRM is the hub for
   triggers and write-back; it sits behind a `CrmClient` abstraction so the real
   adapter drops in cleanly, but **we cannot go live on any CRM-triggered automation
   without this.** If Taskey has no API, fallback is a Make/webhook bridge or a
   Google-Sheet mirror.
2. **Make vs. code.** Confirm whether Make orchestrates (calling these scripts) or is
   dropped; the build stands alone via cron + webhook server.
3. **Fillout / Morning / Google / ClickUp specifics** — form ids, the payment-request
   payload, Drive folder ids + template inventory, and the ClickUp list to watch.
4. **Campaign data source** (T7) — Meta/Google Ads API, a Sheet, or manual export?
5. **NotebookLM** — the "last 5 videos" prep currently runs via the Anthropic API;
   confirm whether NotebookLM should be in the loop (no public API today).
6. **WhatsApp copy** — review/replace the placeholder Hebrew message wording in
   `src/lib/whatsapp_templates.py`.

## Out of scope (as agreed)

Employee hour-tracking (the campaigners' / general worker's Google Forms + Sheets)
is deliberately **not** touched — Dror asked to keep that as-is.
