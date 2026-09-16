# Tasks

See `CLAUDE.md` for the full description of each automation. "Done" means **logic
complete + dry-run verified**; live runs additionally need the credentials in
`docs/CREDENTIALS.md`. Last audited against production (run-log, Lambda metrics,
ClickUp) on 2026-09-15.

`python -m pytest` → 342 passing.

## Now — production is missing configuration, not code

The stack (`dror-automations-dev`) has run live (`WebhookDryRun=0`) since 27.7, but
several parameters are empty, so whole automations cannot complete. None of these
needs code; most need a value from Dror. Once a value is in `.env`, it reaches the
stack with `python -m src.tools.push_stack_params <ParameterName>`.

- [ ] **`DrorEmail` + `SmtpHost` / `SmtpUser` / `SmtpPassword`.** Nothing on AWS can
  send email: not the strategy questionnaire after signing, not the signature
  notification, not the report approval, not the daily digest, not the strategy
  notice. Needs a Workspace **App Password** from Dror (`docs/CREDENTIALS.md`).
- [x] **`MetaAccessToken`.** Was empty on the stack (the report died on 1.8 and 1.9
  before reading a campaign); verified against the active client's account and
  pushed on 16.9.
- [ ] **`SmooveWebhookToken`.** Empty = the Smoove endpoint is open; anyone with the
  URL can create contacts and fire billed WhatsApp Flows. Generate one, deploy it,
  configure Smoove to send it as `X-Smoove-Token`.
- [ ] **`DriveTemplateIds`** — the parameter exists since 16.9 but is empty, so
  onboarding copies no templates and logs `no_templates`. Needs the template
  inventory from Dror (Open Questions), then `push_stack_params DriveTemplateIds`.
- [ ] **Custom domain.** Signing links still go out under
  `e3670c4ju8.execute-api.eu-central-1.amazonaws.com`. The first certificate
  request timed out (no DNS record was ever added at Wix). Request again with
  `check_domain --request`, add the two Wix records (`docs/CREDENTIALS.md` §7a),
  `setup_domain --apply`, then push `SignBaseUrl`.

## Backlog

- [ ] **Chromium on Lambda for the campaign report.** The PDF renders via headless
  Chromium (`src/lib/pdf_chromium.py`); the stack has no layer and no
  `PLAYWRIGHT_CHROMIUM_PATH`, so the monthly schedule cannot produce a report even
  once the Meta token is set. The stack is **arm64** and the usual
  `@sparticuz/chromium` layer is x86_64-only: either an arm64 Chromium build, or
  move `CampaignReportFunction` alone to x86_64.
- [ ] **Deploy the dashboard** — or decide it stays local. `src/dashboard.py` is a
  stdlib server with in-memory sessions (`_sessions`), so on Lambda Dror would be
  logged out at random; a signed cookie replaces it. Today Dror has no dashboard.
- [ ] **A test stack.** There is one stack, named `dev`, and it is production. A
  second stage (`Stage=test`, `WebhookDryRun=1`, a test ClickUp list) is the only
  way to exercise a full flow against the real services without risking a client.
- [ ] **Clean test rows out of the production run-log.** Entries from 16.7 with
  automation `t` / `a`, and the dry-run Taskey migration, still show on the
  dashboard and in the digest as if they happened.
- [ ] **Keep client documents out of the Lambda package.** `CodeUri: ../` packages
  the working folder: the 27.7 deploy shipped `docs/contract_source.txt`, Dror's
  proposal PDF and call-notes DOCX into all four functions (replaced on 15.9 by a
  build from a clean checkout). Add exclusions to the template, and purge the old
  artifacts from the `aws-sam-cli-managed-default` bucket.
- [ ] **Decide the initial-meeting questionnaire.** The original #2 (WhatsApp a
  questionnaire on `פגישה ראשונית`) was replaced by the post-signing strategy
  questionnaire; nothing fires on `פגישה ראשונית` today. Dror's call whether a
  pre-quote step is wanted back, and over which channel.
- [ ] **Replace onboarding's "open a WhatsApp channel" step.** The official API
  cannot create groups, so the `create_group` call is gone — but nothing took its
  place: a newly onboarded client gets the questionnaire email and no WhatsApp at
  all. The replacement is a Meta-approved welcome **Flow** sent through ManyChat,
  the same shape as T12. Deferred deliberately (Dror's call) until the Flow copy
  exists and is approved; `whatsapp_templates.onboarding_welcome` is the draft
  wording, currently unused.
- [ ] **Rework `whatsapp_templates.py`.** Free-text bodies are no longer possible;
  it becomes a map of approved template names → variables.
- [ ] **File the signed contract into the `חוזים` subfolder.** Onboarding gives every
  client folder four subfolders, but the signing page runs *before* it and drops the
  signed PDF in the folder root. Small and cosmetic.
- [ ] **Retire dead code from replaced systems:** `src/lib/clients/green_api.py`,
  `fillout.py`, `daily_summary.py` (the WhatsApp digest), and the Morning field
  mappings in `crm.py` / `subjects.py` / `docs/CLICKUP_SETUP.md`.
- [ ] **Add auth to `webhook_server.py`**, the local stdlib receiver, or retire it
  now that the Lambda is the real entrypoint.

## Done

- [x] **T0 — Shared infrastructure** (`src/lib`): config, structured logging,
  retry/backoff, HTTP helper, run-log, subjects, template store, API clients — each
  with a dry-run/mock mode.
- [x] **T1 — Lead → Google Contacts.** `src/automations/lead_to_contacts.py`.
- [x] **T2 — Send questionnaire.** `src/automations/send_questionnaire.py` — now the
  `שלח שאלון` re-send button, sharing one implementation with onboarding. The
  `initial_meeting` trigger was retired with the move to our own form (see Backlog).
- [x] **T3 — Social-media prep report (AI).** `src/automations/social_prep.py`, run
  when the questionnaire is submitted.
- [x] **T4 — Send quote + capture signature.** `src/automations/send_quote.py` +
  `src/sign_page.py` (our own page at `/sign`, replacing Fillout).
- [x] **T5 — Onboarding (central).** `src/automations/onboarding.py`. Promotes the
  client to **`active`** as well as `in_work`, gives the Drive folder its four
  standard subfolders and records the recordings path, copies templates under their
  own names and skips ones already in the folder, flags a missing Meta ad account on
  the task, emails the strategy questionnaire, and leaves a summary comment.
- [x] **Questionnaire chase.** `src/automations/questionnaire_reminders.py` — 3
  and 7 days, then one escalation to Dror and stop. Shares the daily
  `ReminderFunction` schedule with `sign_reminders`.
- [x] **T7 — Monthly campaign summary.** `src/automations/campaign_summary.py`:
  Meta Ads insights → PDF via `src/lib/campaign_report.py` → email to Dror for
  approval; scheduled on the 1st with a self-invoke fan-out
  (`src/scheduled.py::campaign_report_handler`). Blocked on AWS — see above.
- [x] **T8 — Strategy bot.** `src/automations/strategy_bot.py`; notifies Dror by email.
- [x] **T9 — ClickUp → Claude Code (bonus).** `src/automations/clickup_to_claude.py`.
- [x] **T10 — Daily report to Dror.** `src/automations/daily_email.py`, scheduled on
  AWS as `DailyEmailFunction` (15.9). Supersedes the WhatsApp `daily_summary.py`.
- [x] **T11 — Dashboard.** `src/dashboard.py` — read-only, password-protected, local.
- [x] **T12 — Smoove → ManyChat.** `src/smoove_handler.py`; the one automation doing
  real work every day (120 Flows since 22.7, no errors).
- [x] **CrmClient against ClickUp** (`src/lib/clients/crm.py`, fields matched by
  name), **ManyChat client** (`src/lib/clients/manychat.py`), **run-log on
  DynamoDB** (`RunLogTable`), **signing page**, **webhook stack deployed** (15.7,
  live since 27.7) and **ClickUp webhook registered**
  (`src/tools/register_clickup_webhook.py`).
- [x] **Taskey → ClickUp migration.** `src/tools/migrate_taskey_to_clickup.py`.
- [x] **Credentials guide.** `docs/CREDENTIALS.md`. **Operator guide (Hebrew).**
  `docs/OPERATIONS.md`.

---

## Decided

1. **CRM** — ClickUp replaces Taskey, whose API was never confirmed.
2. **E-signature** — our own signing page replaces Fillout, to drop the monthly fee.
3. **WhatsApp** — ManyChat on the official Meta Business API replaces Green API.
4. **Campaign data** — the Meta Ads API (system-user token, `ads_read`).
5. **Google auth** — service account with domain-wide delegation.
6. **Dror's daily digest and notifications** — email, not WhatsApp.
7. **Dashboard scope** — read-only, shared password. Triggers deferred until Dror
   has used it.
8. **Repo** — public, so Dror can work in it with his own Claude Code. Client
   documents and secrets stay out (see `CLAUDE.md` → Public repo).
9. **Hosting** — AWS Lambda + API Gateway (eu-central-1), one SAM stack. Make is
   dropped.
10. **Questionnaire** — our own page, after signing, not a Google Form before the
    quote.

## Open Questions

1. **Is Dror working in ClickUp?** The clients list holds one task and none was
   created in the last 30 days. Every automation starts from a status change
   there, so until it is the CRM in practice, nothing runs. The most important
   question on this list.
2. **WhatsApp templates.** Who writes the Hebrew and submits them to Meta for
   approval? The client-facing flows (welcome after onboarding, quote, reminders)
   cannot exist until they do; today those go by email.
3. **The per-client WhatsApp channel.** Impossible on the official API. What
   replaces it in onboarding?
4. **Template inventory.** Which Drive template files exist (contract, quote,
   strategy, campaign report) and their ids. `DRIVE_TEMPLATE_IDS` is empty
   everywhere.
5. **Anthropic billing.** Whose account and card.
6. **NotebookLM.** The "last 5 videos" prep runs via the Anthropic API today.
   NotebookLM has no public API — confirm it stays out.
