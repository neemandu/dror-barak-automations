# Tasks

One task per automation, ordered by dependency. See `CLAUDE.md` for the full
description of each. Move items between sections as work progresses.

## Backlog

- [ ] **T1 — Lead → Google Contacts.** Webhook on new CRM lead saves the phone
  number to Google Contacts.
- [ ] **T2 — Send questionnaire (WhatsApp).** On CRM secondary status
  `initial_meeting`, send Green API WhatsApp message with the Google Forms link.
- [ ] **T3 — Social-media prep report (AI).** On questionnaire submit, AI analyzes
  each social profile (last 5 videos) and produces a prep report for Dror.
- [ ] **T4 — Send quote + capture signature.** "Send quote" issues a Fillout
  quote with a signature field; on signature, store signed PDF in Drive + link in CRM.
- [ ] **T5 — Onboarding (central).** On CRM `signed`: create Drive folder, copy
  templates, create Morning client, open WhatsApp channel, write links back to CRM.
- [ ] **T6 — Monthly payment requests.** 1st of month: active clients → Morning
  payment request → WhatsApp payment link.
- [ ] **T7 — Monthly campaign summary.** Month end: analyze campaigns, fill
  template, add AI recommendations, send to Dror → forward to client + Drive.
- [ ] **T8 — Strategy bot.** From questionnaire answers: audience + competitor +
  presence analysis → full strategy → templates → client Drive → notify Dror.
- [ ] **T9 — ClickUp → Claude Code (bonus).** ClickUp task webhook hands the task
  to Claude Code to start working on it.
- [ ] **T10 — Daily summary to Dror.** End of day: read run-log, send WhatsApp
  summary of everything that ran.

## In Progress

- [ ] **T0 — Shared infrastructure** (`src/lib`): config loader, structured JSON
  logging, retry/backoff HTTP client, run-log, WhatsApp template store, and API
  client wrappers (CRM, Morning, Green API, Fillout, Google, ClickUp, Anthropic)
  each with a dry-run/mock mode. Foundation for every task below.

## Done

_(none yet)_

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
