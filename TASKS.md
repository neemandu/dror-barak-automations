# Tasks

See `CLAUDE.md` for the full description of each automation. "Done" means **logic
complete + dry-run verified**; live runs additionally need the credentials in
`docs/CREDENTIALS.md`.

`python -m pytest` → 50 passing.

## Backlog

Work created by the ClickUp / ManyChat / signing decisions. The automations' logic
is unaffected — these are the adapters underneath them.

- [ ] **Rewrite `CrmClient` against the ClickUp API.** `src/lib/clients/crm.py` is
  still shaped around Taskey with provisional endpoints. ClickUp is the CRM now.
  Needs the custom-field ids, which only exist once the clients list is built.
- [ ] **Replace `GreenApiClient` with a ManyChat client.** Different model: messages
  become Meta-approved templates sent as ManyChat Flows, addressed to subscribers
  rather than raw phone numbers, with `consent_phrase` on contact creation.
- [ ] **Rework `whatsapp_templates.py`.** Free-text bodies are no longer possible;
  it becomes a map of approved template names → variables.
- [ ] **Build the signing page** (replacing Fillout): render the quote, capture the
  signature, store the PDF + audit trail (IP, timestamp, hash) in Drive, write the
  link back to ClickUp. Retire `src/lib/clients/fillout.py`.
- [ ] **Replace onboarding's "open a WhatsApp channel" step.** The official API
  cannot create groups; `onboarding.py` still calls `create_group`.
- [ ] **Point `campaign_summary` at the Meta Ads API** for real campaign numbers.
- [ ] **Add auth to `webhook_server.py`** before it is exposed publicly.

## Done

- [x] **T0 — Shared infrastructure** (`src/lib`): config, structured logging,
  retry/backoff, HTTP helper, run-log, subjects, template store, API clients — each
  with a dry-run/mock mode.
- [x] **T1 — Lead → Google Contacts.** `src/automations/lead_to_contacts.py`.
- [x] **T2 — Send questionnaire.** `src/automations/send_questionnaire.py`.
- [x] **T3 — Social-media prep report (AI).** `src/automations/social_prep.py`.
- [x] **T4 — Send quote + capture signature.** `src/automations/send_quote.py`.
- [x] **T5 — Onboarding (central).** `src/automations/onboarding.py`.
- [x] **T6 — Monthly payment requests.** `src/automations/monthly_payment_requests.py`.
- [x] **T7 — Monthly campaign summary.** `src/automations/campaign_summary.py`.
- [x] **T8 — Strategy bot.** `src/automations/strategy_bot.py`.
- [x] **T9 — ClickUp → Claude Code (bonus).** `src/automations/clickup_to_claude.py`.
- [x] **T10 — Daily report to Dror.** `src/automations/daily_email.py` (email;
  supersedes the WhatsApp `daily_summary.py`).
- [x] **T11 — Dashboard.** `src/dashboard.py` — read-only, password-protected.
- [x] **Webhook receiver.** `src/webhook_server.py`.
- [x] **Taskey → ClickUp migration.** `src/tools/migrate_taskey_to_clickup.py`.
- [x] **Credentials guide.** `docs/CREDENTIALS.md`.

---

## Decided

Previously open, now settled:

1. **CRM** — ClickUp replaces Taskey, whose API was never confirmed.
2. **E-signature** — our own signing page replaces Fillout, to drop the monthly fee.
3. **WhatsApp** — ManyChat on the official Meta Business API replaces Green API.
4. **Campaign data** — the Meta Ads API (system-user token, `ads_read`).
5. **Google auth** — service account with domain-wide delegation.
6. **Dror's daily digest** — email, not WhatsApp.
7. **Dashboard scope** — read-only, shared password. Triggers deferred until Dror
   has used it.
8. **Repo** — public, so Dror can work in it with his own Claude Code. Client
   documents and secrets stay out (see `CLAUDE.md` → Public repo).

## Open Questions

1. **Hosting.** The signing page and the dashboard both need a public HTTPS home
   with a real domain and certificate — Fillout used to provide this for signing.
   Blocking both features.
2. **WhatsApp templates.** Who writes the Hebrew and submits them to Meta for
   approval? Nothing can send until they exist and are approved.
3. **The per-client WhatsApp channel.** Impossible on the official API. What
   replaces it in onboarding?
4. **ClickUp custom fields.** The field ids for status, price, Drive path, contract
   link and Morning status — needed by the CRM client and the migration tool.
5. **Template inventory.** Which Drive template files exist (contract, quote,
   strategy, campaign report) and their ids. Is the set fixed or per-client?
6. **Anthropic billing.** Whose account and card.
7. **Morning payment requests.** Confirm the דרישת תשלום payload and the
   client-creation fields.
8. **NotebookLM.** The "last 5 videos" prep runs via the Anthropic API today.
   NotebookLM has no public API — confirm it stays out.
9. **Make.** Orchestrator, or dropped? The build stands alone on cron + webhooks.
