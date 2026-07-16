# CLAUDE.md

> **Fixed instructions — always apply:**
> Before any work, read `docs\CREDENTIALS.md` and `README.md`. Commit with a clear
> message after every working milestone. Never reference or use anything from other
> client folders. This repository is **public** — never commit a secret, and never
> commit a client-supplied document (see "Public repo" below).

## What this is

An automation system for **Dror Barak**, a consultancy that advises colleges and
academies on student enrolment (webinars, marketing funnels) and runs paid ad
campaigns for them.

The sales, onboarding and service work used to be manual: saving lead phone
numbers, sending questionnaires, researching each prospect's social media by hand,
editing quotes, opening Drive folders, issuing invoices, writing monthly campaign
reports. This system automates that path **from lead to active client** so Dror
handles more clients in less time.

**ClickUp is the single pane of glass.** From it Dror sees every client's Drive
folder and what the automations did. He also gets a **daily email report** and a
**dashboard**, so he is never in the dark about what ran.

**Out of scope:** employee hour-tracking (the campaign managers' Google Forms and
Sheets). Dror asked for that to be left alone. Don't automate it.

## Systems

| System | Role | Integration notes |
|---|---|---|
| **ClickUp** | **The CRM.** Manages the whole lead→client lifecycle and triggers automations on status change | REST API + webhooks. One task per client. Replaced Taskey — see "History" below. |
<!-- Morning (חשבונית ירוקה) billing was removed: Dror handles invoicing himself.
     The monthly-billing automation and the Morning API client are gone. The
     מזהה מורנינג / סטטוס Morning columns may still exist on his ClickUp list;
     they are simply ignored. -->

| **ManyChat** | WhatsApp to clients, over the **official Meta Business API** | REST API. Read the 24-hour-window constraint below before touching any messaging code. |
| **Google Workspace** | Contacts (lead phones), Drive (client folders, templates, signed PDFs), Forms (questionnaire), Sheets (task board — read-only) | Service account with domain-wide delegation. |
| **Meta Ads** | Campaign numbers for the monthly report | Graph API, system-user token with `ads_read`. |
| **Claude / Anthropic** | Social analysis, strategy, campaign recommendations | Anthropic API (`claude-opus-4-8` / `claude-sonnet-5`). |
| **Signing page** | Digital signature on quotes/contracts | **Ours**, served by us. Replaced Fillout. |

Auth for every system is loaded **only** from `.env` (see `.env.example`, and
`docs\CREDENTIALS.md` for how each value is obtained). No secret is ever hardcoded
or read from another client's folder.

### ClickUp data model

- **Primary status:** `lead` / `active` / `paused` / `finished`
- **Secondary status:** `initial_meeting` / `questionnaire_sent` / `quote_sent` /
  `signed` / `in_work`
- **Custom fields:** Drive folder path, signed-contract link, monthly price,
  service type, recordings path.

## Constraints that are easy to get wrong

**The WhatsApp 24-hour window.** On the official Meta API, a free-form message can
only be sent within 24 hours of the client's *last inbound message*. Every one of
Dror's flows is business-initiated and therefore outside it, so each outbound
message must be a **Meta-approved template**, sent through ManyChat as a Flow.
Consequences you must respect:

- Message wording is **not** freely editable. Only the template's variables change;
  rewording means resubmitting to Meta for approval.
- Conversations are **billed per message** by category. Don't add chatty messages.
- **Groups/channels cannot be created** via the official API. Anything that wants a
  "WhatsApp channel per client" is not possible as originally proposed.
- Dror's own daily digest therefore goes by **email**, not WhatsApp — it would
  otherwise need its own approved template and be billed every day.

**The dashboard is read-only.** Nothing is triggered from it, deliberately: a
misclick that fires onboarding would create a duplicate Drive folder. Adding triggers is a
decision for Dror, not a refactor to slip in.

**`send_quote` is CLI/button-only, never automatic** — sending a client a
contract is Dror's decision, not something a status change should trigger.

## Automations

Each is code in `src\automations\`, configured via `.env`, with retry, structured
logging, and a `--dry-run` mode.

| # | Automation | Trigger | Summary |
|---|---|---|---|
| 0 | **Shared infra** (`src\lib`) | — | Config, logging, retry/backoff HTTP, run-log, subjects, message templates, API clients (all with dry-run/mock mode). |
| 1 | **Lead → Google Contacts** | Webhook (ClickUp: new lead) | Save the lead's phone number to Google Contacts. |
| 2 | **Send questionnaire** | Webhook (ClickUp: `initial_meeting`) | WhatsApp the Google Forms questionnaire link. |
| 3 | **Social-media prep report** | Webhook (Forms submit) / Manual | AI reads the social profiles from the questionnaire and writes a per-network prep report for Dror. Reused by #8. |
| 4 | **Send quote + capture signature** | Manual + our signing page | Send a quote with a signature link; on signing, store the PDF in Drive and write the link back to ClickUp. |
| 5 | **Onboarding** (central) | Webhook (ClickUp: `signed`) | Create the client Drive folder, copy templates, open a WhatsApp channel, advance the status. |
| ~~6~~ | ~~Monthly payment requests~~ | — | **Removed.** Dror invoices clients himself; the system does not touch Morning. |
| 7 | **Monthly campaign summary** | Scheduled (month end) / Manual | Pull the month's Meta Ads results, fill Dror's report template, add AI recommendations, send to Dror to approve → forward to client + save to Drive. |
| 8 | **Strategy bot** | Manual | From the questionnaire answers: audience + competitors + digital presence → full strategy → Drive → notify Dror. Reuses #3. |
| 9 | **ClickUp → Claude Code** (bonus) | Webhook (ClickUp task) | Turns a ClickUp task into a Claude Code work brief. |
| 10 | **Daily report** | Scheduled (end of day) | Emails Dror everything the automations did that day (`daily_email`). |
| 11 | **Dashboard** | Always on | Read-only web page over the run-log, grouped by subject, with links out. `src\dashboard.py`. |

## How they run

- **Scheduled** — cron / Task Scheduler runs `python -m src.automations.<name>`.
- **Webhook** — `src\webhook_server.py` maps inbound webhooks to automations.
- **Manual** — every automation has a CLI entrypoint with `--dry-run`.

Every automation writes to the **run-log** (`src\lib\run_log.py`) via
`Automation.log_action`. That log is the only data source for both the dashboard
and the daily email, so **if you add an automation, log through `log_action`** or it
will be invisible to Dror. Pass links as `url=` rather than burying them in
`detail=`.

## Conventions

Follow [`..\_shared\CONVENTIONS.md`](..\_shared\CONVENTIONS.md). Language:
**Python**. Retry + backoff on all outbound calls, JSON structured logging, secrets
from `.env` only, run instructions per automation in `README.md`.

## Public repo

This repository is public so Dror can work in it with his own Claude Code. Two
rules follow:

1. **Never commit a secret.** `.env` is gitignored. Only `.env.example` — key names,
   no values — belongs in git.
2. **Never commit a client-supplied document.** Dror's proposal, call notes and any
   client file stay on local disk. `.gitignore` blocks `docs\*.pdf`, `*.docx` and
   `*.xlsx`. If you need a new document type, add it to `.gitignore` first.

## How to test

No production credentials are required to prove the code works:

- Every automation supports **`--dry-run`**, swapping live clients for mocks that
  return canned responses and record what they *would* do.
- `python -m pytest` runs the whole suite in dry-run.
- `python -m src.dashboard --dry-run` serves the dashboard on sample data with no
  `.env` at all.
- Dry-run is not proof on its own. If you change something with a real runtime
  surface, drive it — the last two dashboard bugs both passed the tests.

## History — things that changed, so old code makes sense

- **Taskey → ClickUp.** Taskey was the original CRM; its API was never confirmed.
  ClickUp replaced it. `src\tools\migrate_taskey_to_clickup.py` migrates a Taskey
  CSV export. If you find a `CrmClient` still shaped around Taskey, that's the
  leftover — ClickUp is the truth.
- **Green API → ManyChat.** Green API was an unofficial WhatsApp bridge with no
  template rules and the ability to make groups. The official API has neither.
- **Fillout → our own signing page.** Dropped to remove the monthly fee. The cost is
  that we now need a public HTTPS host and we own the signature audit trail.
- **Make.** The original proposal budgeted Make as the orchestrator. This build
  stands alone on cron + the webhook server; Make can call it or be dropped.
