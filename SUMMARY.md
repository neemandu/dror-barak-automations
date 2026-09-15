# SUMMARY — Dror Barak Automation Project

*Where things stand as of 2026-09-15. `CLAUDE.md` is the full description,
`TASKS.md` the live to-do list, `docs/OPERATIONS.md` the Hebrew operator guide.*

## What was built

A Python system that automates Dror's path **from lead to active client** around
**ClickUp** (the CRM and single pane of glass): a new lead's phone goes to Google
Contacts; a button sends the quote to our own signing page; signing runs
onboarding (Drive folder, templates, the strategy questionnaire by email, Meta
ad-account check, status promotion); the questionnaire feeds an AI social-media
prep report and a strategy draft; unsigned contracts and unfilled questionnaires
are chased daily; a monthly Meta Ads report is rendered per client; and a
standalone webhook turns Smoove leads into WhatsApp Flows through ManyChat.
Everything logs to one run-log that feeds Dror's daily email and a read-only
dashboard.

It runs as **one AWS SAM stack** (`infra/template.yaml`, eu-central-1): API
Gateway + Lambda for the ClickUp webhook, buttons, signing and questionnaire
pages; a second Lambda for Smoove; EventBridge schedules for reminders, the daily
email and the monthly report; DynamoDB for the run-log and idempotency.

**Buildable and testable without credentials:** every client has a dry-run mock,
`python -m pytest` runs 342 tests with no `.env`, and every automation has a
`--dry-run` CLI.

## What runs in production today

- **Smoove → ManyChat** — the one automation doing daily work: 120 WhatsApp Flows
  since 22.7, no errors.
- **Daily reminders** (signatures, questionnaires) — run every morning, nothing
  to chase yet.
- **ClickUp webhook, buttons, signing page** — wired and healthy; used on 2
  quotes and one signing since July.

## What is blocked, and why

Configuration on the stack, not code (details and owners in `TASKS.md` → "Now"):

- **No email can leave AWS** — `DrorEmail` and the SMTP parameters are empty. That
  silences the questionnaire after signing, signature notices, report approvals,
  the daily digest. Needs an App Password from Dror.
- **The monthly report** — `MetaAccessToken` is empty (it failed on 1.8 and 1.9),
  and Lambda has no Chromium layer to render the PDF.
- **The Smoove endpoint is open** — `SmooveWebhookToken` is empty.
- **The dashboard is not deployed** — it runs locally only.
- **Adoption** — ClickUp holds one client task. Every automation starts from a
  status change there; until Dror manages clients in it, nothing runs.

## Out of scope (as agreed)

Employee hour-tracking (the campaigners' Google Forms + Sheets) is deliberately
**not** touched. Invoicing (Morning) was removed: Dror invoices clients himself.
