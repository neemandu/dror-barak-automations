# Tasks

One task per automation, ordered by dependency. See `CLAUDE.md` for the full
description of each. Move items between sections as work progresses.

All automations are built with a `--dry-run` mode and covered by the test suite
(`python -m pytest` → 24 passing). "Done" here means **logic complete + dry-run
verified**; each still needs live credentials (see Open Questions) to run against
production.

## Backlog

_(none — all tasks implemented in dry-run; remaining work is live-credential wiring
tracked under Open Questions.)_

## In Progress

_(none)_

## Done

- [x] **T0 — Shared infrastructure** (`src/lib`): config loader, structured JSON
  logging, retry/backoff, HTTP helper, run-log, WhatsApp template store, and API
  client wrappers (CRM, Morning, Green API, Fillout, Google, ClickUp, Anthropic)
  each with a dry-run/mock mode.
- [x] **T1 — Lead → Google Contacts.** `src/automations/lead_to_contacts.py`.
- [x] **T2 — Send questionnaire (WhatsApp).** `src/automations/send_questionnaire.py`.
- [x] **T3 — Social-media prep report (AI).** `src/automations/social_prep.py`
  (profile analysis reused by T8).
- [x] **T4 — Send quote + capture signature.** `src/automations/send_quote.py`
  (`send` + `signed` actions).
- [x] **T5 — Onboarding (central).** `src/automations/onboarding.py`.
- [x] **T6 — Monthly payment requests.** `src/automations/monthly_payment_requests.py`.
- [x] **T7 — Monthly campaign summary.** `src/automations/campaign_summary.py`.
- [x] **T8 — Strategy bot.** `src/automations/strategy_bot.py`.
- [x] **T9 — ClickUp → Claude Code (bonus).** `src/automations/clickup_to_claude.py`.
- [x] **T10 — Daily summary to Dror.** `src/automations/daily_summary.py`.
- [x] **Webhook receiver** wiring all webhook triggers: `src/webhook_server.py`.

---

## Open Questions

These are noted so work is not blocked, but they need answers from Dror before the
automations can run against production. Where an answer is unknown, the code uses a
documented abstraction + dry-run mock so the logic is complete and testable now.

1. **Taskey CRM API.** Does Taskey expose a REST API and/or outbound webhooks on
   status change? What are the base URL, auth method, and the field/status IDs?
   This is the biggest unknown — the CRM is the hub for triggers (#1, #2, #5) and
   for writing links/logs back. Mitigation: all CRM access goes through a
   `CrmClient` interface with a mock adapter; a real adapter drops in once we have
   API docs. **If Taskey has no API**, the fallback is a Make/webhook bridge or a
   Google-Sheet mirror of the CRM.
2. **Make vs. code.** The proposal budgets Make ($9–16/mo) as the orchestrator, but
   these instructions require code in `src\`. Confirm whether Make should call
   these scripts (via webhook) or be dropped. Current build stands alone via
   cron + a webhook server.
3. **Fillout account & template IDs.** Need the Fillout API key and the form IDs
   for the quote and the contract, plus the field name of the signature.
4. **Morning API.** Need the API token and to confirm the payment-request
   ("דרישת תשלום") endpoint + the client-creation payload (business ID, etc.).
5. **Green API instance.** Need `idInstance` + `apiTokenInstance`, and confirmation
   of WhatsApp "channel/group per client" feasibility (proposal says "לפי היתכנות").
6. **Google Workspace access.** Service-account vs OAuth; the Drive folder IDs for
   the templates folder and the parent that holds client folders; the Google Forms
   questionnaire ID; the tasks Sheet ID (read-only — hour tracking is out of scope).
7. **Template inventory.** Which template files exist (contract, quote, strategy
   docs, campaign report) and their Drive IDs, so onboarding copies the right ones.
   Onboarding also asks Dror *which* templates to copy — is that per-client choice
   or a fixed set?
8. **WhatsApp message copy.** Dror wants an editable store of message bodies (with
   parameters). We ship `src/lib/whatsapp_templates.py` with placeholder Hebrew
   copy — Dror should review/replace the wording.
9. **Campaign data source.** Where do campaign performance numbers come from (Meta
   Ads / Google Ads API, a Sheet, a manual export)? Needed for T7.
10. **NotebookLM.** The prep bot references NotebookLM (no public API). Confirm
    whether the "last 5 videos" analysis runs via NotebookLM manually or fully via
    the Anthropic API. Current build uses the Anthropic API.
11. **ClickUp workspace.** Need the API token, the list/space to watch, and what
    "hand to Claude Code" should concretely do (open a branch? run a command?).
12. **Recipients.** Dror's WhatsApp number + email, and per-client contact fields,
    for summaries, approvals, and forwards.
