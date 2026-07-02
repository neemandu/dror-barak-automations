# CLAUDE.md

> **Fixed instructions — always apply:**
> Before any work, read all files in `docs\`. Commit with a clear message after
> every working milestone. Never reference or use anything from other client folders.

## Client Overview

**Dror Barak — business consulting & campaign management.** Dror runs an agency
that advises colleges/academies on enrolling more students, largely through
webinars and marketing funnels, and also runs paid ad campaigns for clients. He
charges variable monthly retainers.

Today most of the sales, onboarding and service work is **manual**: coordinating
Zoom calls, saving lead phone numbers, sending questionnaires, preparing for
meetings by hand-reviewing each prospect's social media, editing quotes, opening
Drive folders, issuing invoices, and writing monthly campaign reports.

**Goal:** one scalable, uniform, automated workflow that connects every stage
**from lead to active client**, so Dror manages more clients in less time. The
CRM (Taskey) should be the single pane of glass: from it Dror sees every client's
Drive folder, an automation log of what ran, and he receives a **daily WhatsApp
summary** so he is never in the dark.

The team also includes two campaign managers and one general worker (copywriting,
market research). Task assignment + hourly logging for those workers lives in
Google Sheets / Google Forms today. **Dror explicitly does not want us to touch
employee hour-tracking** — that is out of scope.

## Systems & APIs

| System | Role | Status | Integration notes |
|---|---|---|---|
| **Taskey** | CRM — manages the whole lead→client lifecycle | Existing | API access **unconfirmed** (see Open Questions). We integrate behind a `CrmClient` abstraction so a REST/webhook adapter can drop in once access is known. |
| **Make** | Automation orchestration (proposal budgets $9–16/mo) | Existing | Original proposal assumed Make scenarios. We instead build **code** automations in `src\` (per these instructions) that can be triggered by cron/webhook/manual; Make can call them or be replaced. |
| **Fillout** | Digital signature on quotes/contracts | Paid | REST API + webhooks. Used to send a quote/contract with a signature field; on completion we fetch the signed PDF. |
| **Claude / AI** | Smart, uniform deliverables (social analysis, strategy, campaign recommendations) | Per-usage | Anthropic API (`claude-opus-4-8` / `claude-sonnet-5`). NotebookLM referenced for the "last 5 videos" prep bot. |
| **Green API** | WhatsApp messaging (questionnaire link, payment link, daily summary, onboarding) | Existing | REST API keyed by `idInstance` + `apiTokenInstance`. All message bodies come from an editable template store. |
| **Morning** (getmorning / חשבונית ירוקה) | Invoicing & monthly payment requests | Existing | REST API with token auth. Create client, create payment request (דרישת תשלום). |
| **Google Workspace** | Contacts (save lead phone), Drive (client folders + templates + signed PDFs), Forms (questionnaire), Sheets (task board — read-only for us) | Existing | Google APIs via a service account / OAuth. |
| **ClickUp** | Task system that should hand tasks to Claude Code (bonus module) | Existing | REST API + webhooks; a webhook receiver turns a new/updated task into a Claude Code run. |

Auth for every system is loaded **only** from `.env` (see `.env.example`). No
secret is ever hardcoded or read from another client's folder.

### Taskey CRM data model (from the proposal)

- **Primary status:** `lead` / `active` / `paused` / `finished`
- **Secondary status:** `initial_meeting` / `questionnaire_sent` / `quote_sent` /
  `signed` / `in_work`
- **Fields:** Drive folder path, signed-contract link, monthly price, service
  type, recordings path, Morning status.

## Automations To Build

Ordered by dependency. Each is code in `src\`, config via `.env`, with retry +
structured logging + a `--dry-run` mode. Trigger column: how it is meant to fire.

| # | Automation | Trigger | Summary |
|---|---|---|---|
| 0 | **Shared infra** (`src/lib`) | — | Config, structured logging, retry/backoff HTTP, run-log, WhatsApp template store, API client wrappers (all with dry-run/mock mode). |
| 1 | **Lead → Google Contacts** | Webhook (CRM: new lead) | On a new lead in the CRM, save the lead's phone number to Google Contacts. |
| 2 | **Send questionnaire** | Webhook (CRM: secondary status → `initial_meeting` done) | Send a WhatsApp (Green API) message with the questionnaire (Google Forms) link. |
| 3 | **Social-media prep report** | Webhook (Google Forms submit) / Manual | AI agent visits the social profiles from the questionnaire, produces a report per network (profile link, summary, recommendations from the last 5 videos) for Dror. Reused later by the strategy bot. |
| 4 | **Send quote + capture signature** | Manual (CRM "send quote" button) + Webhook (Fillout signed) | Send a quote with a Fillout digital-signature field; on signature, store the signed PDF in Drive and write the link back to the CRM. |
| 5 | **Onboarding** (central module) | Webhook (CRM: `signed`) | Create the client Drive folder, copy templates into it, create the client in Morning, open a WhatsApp channel, save the signed contract link + Drive path to the CRM, and ask Dror (via WhatsApp/email) which templates to copy. |
| 6 | **Monthly payment requests** | Scheduled (1st of month) | Pull active clients from the CRM, create a payment request in Morning for each, and send a WhatsApp message with the payment link. |
| 7 | **Monthly campaign summary** | Scheduled (month end) / Manual | Analyze the month's campaign results, fill Dror's report template, add AI recommendations, send to Dror for approval → forward to client + save to the client's Drive. |
| 8 | **Strategy bot** | Manual (Claude Code skill) | From the client's questionnaire answers, analyze audience + competitors + digital presence, produce a full strategy, inject into templates, save to the client's Drive, and email/notify Dror. Reuses automation #3's profile analysis. |
| 9 | **ClickUp → Claude Code** (bonus / gift) | Webhook (ClickUp task created/updated) | Each task defined in ClickUp is handed to Claude Code, which starts working on it automatically. |
| 10 | **Daily summary to Dror** | Scheduled (end of day) | Read the run-log written by every automation and send Dror a WhatsApp end-of-day summary of what ran. |

## How Automations Run

Three trigger modes (per `..\_shared\CONVENTIONS.md`), all supported by the same code:

- **Scheduled** — cron / Windows Task Scheduler invokes `python -m src.automations.<name>`
  (e.g. monthly payment requests on the 1st, daily summary at end of day, monthly
  campaign report at month end).
- **Webhook** — a small receiver (`src/webhook_server.py`) maps inbound webhooks
  (CRM status change, Fillout signature, Google Forms submit, ClickUp task) to the
  matching automation. Each automation is also importable/callable directly.
- **Manual** — every automation exposes a CLI entrypoint with `--dry-run`, so Dror
  (or we) can run any one on demand without hitting production.

Every automation writes a structured record to the **run-log**, which feeds both
the CRM automation log and the daily WhatsApp summary.

## Conventions

Follow the cross-client conventions in [`..\_shared\CONVENTIONS.md`](..\_shared\CONVENTIONS.md).
Language: **Python** (best-supported HTTP/SDK story for Morning, Green API,
Fillout, Google, ClickUp, and the Anthropic API; conventions prefer Python on a
toss-up). Retry + backoff on all outbound calls, JSON structured logging, secrets
from `.env` only, README run instructions per automation.

## How To Test

No production credentials are required to prove the code works:

- Every automation supports **`--dry-run`**, which swaps live API clients for mock
  clients that return canned, documented responses and records the actions it
  *would* take.
- `pytest` runs the full suite in dry-run mode plus unit tests for retry/backoff
  and the WhatsApp template store: `python -m pytest` from the project root.
- See `README.md` for per-automation run and dry-run commands.
