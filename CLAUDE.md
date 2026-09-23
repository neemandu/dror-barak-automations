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
| **Google Workspace** | Contacts (lead phones), Drive (client folders, templates, signed PDFs), Sheets (task board — read-only) | Service account with domain-wide delegation. |
| **Meta Ads** | Campaign numbers for the monthly report | Graph API, system-user token with `ads_read`. |
| **Claude / Anthropic** | Social analysis, strategy, campaign recommendations | Official `anthropic` SDK (`src\lib\clients\anthropic_ai.py`), `claude-opus-4-8`; `web=True` adds server-side web search/fetch, `thinking=True` adaptive thinking. |
| **Signing + questionnaire pages** | Digital signature on quotes/contracts; the strategy questionnaire form | **Ours**, served by the webhook Lambda (`/sign`, `/questionnaire`). Replaced Fillout and Google Forms. |

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
decision for Dror, not a refactor to slip in. The one write surface is the
questionnaire admin (`/admin`, `src\questionnaire_admin.py`): it edits questionnaire
*content* and mints client links — it never sends anything or runs an automation.

**Questionnaire questions are data, not code.** They live in the questionnaire store
(`src\lib\questionnaire_store.py`, DynamoDB `QuestionnaireTable`) and Dror edits them in
the admin. Code must never find a question by its wording: answers are keyed by the
question's immutable `key`, and meaning comes from its `role` (`instagram`, `website`, …).
Each submission stores a snapshot of the questions it answered.

**Long work runs in the background.** API Gateway cuts requests at 30 s; the strategy,
the social analysis (real web research) and a campaign report take minutes. Button presses
and the questionnaire submit hand them to `src\lib\tasks.py::dispatch`, which re-invokes
the webhook Lambda asynchronously; a failed task is logged and commented on the task.

**Text in Dror's name reads like a person wrote it.** No long dashes (— or –) in any
string the code emits, in the templates, or in what Claude writes: `src\lib\text_style.py`
adds the rule to every prompt and cleans the output, and `tests\test_house_style.py` fails on
a dash in a runtime string. No "תמלא/י" slashes, no emoji in a client's subject line. Emails
are wrapped by `email_templates.layout` (brand band, Dror's signature); documents are branded
`.docx` files from `src\lib\branded_doc.py` that Drive converts into Google Docs (band header on
every page, footer with the page number). Drive's HTML import has no header or footer, and the
Docs API is not enabled in the Google project.

**`send_quote` is CLI/button-only, never automatic** — sending a client a
contract is Dror's decision, not something a status change should trigger.

**Both statuses matter, and they answer different questions.** The *secondary*
status drives the funnel; the **primary** status is what `list_active_clients`
filters on, and that is the list the monthly campaign report iterates. Onboarding
therefore sets both (`active` + `in_work`) — a client advanced only to `in_work`
looks fine on the task and silently never gets a report.

## Automations

Each is code in `src\automations\`, configured via `.env`, with retry, structured
logging, and a `--dry-run` mode.

| # | Automation | Trigger | Summary |
|---|---|---|---|
| 0 | **Shared infra** (`src\lib`) | — | Config, logging, retry/backoff HTTP, run-log, subjects, message templates, API clients (all with dry-run/mock mode). |
| 1 | **Lead → Google Contacts** | Webhook (ClickUp: new lead) | Save the lead's phone number to Google Contacts. |
| 2 | **Send questionnaire** | Button (`שלח שאלון`) / CLI | Email the client the link to the default questionnaire (our own branded form, `src\questionnaire_page.py`) and restart the chase; records who was sent what. Onboarding (#5) sends it automatically after signing; the button re-sends a lost link; the admin can mint a link without sending anything. Nothing fires on `initial_meeting` — see History. |
| 3 | **Social-media prep report** | Questionnaire submitted (background task) / Button (`בנה דוח רשתות`) | Claude **opens** each profile link the client gave (server-side web fetch/search) and writes what it actually saw — and says what it could not see. Google Doc in the client's `אסטרטגיה` folder. Reused by #8. |
| 4 | **Send quote + capture signature** | Manual + our signing page | Send a quote with a signature link; on signing, store the PDF in Drive and write the link back to ClickUp. |
| 5 | **Onboarding** (central) | Webhook (ClickUp: `signed`) | Create the client Drive folder + its standard subfolders, copy templates, email the strategy questionnaire (and chase it), send the WhatsApp welcome Flow when `MANYCHAT_FLOW_ONBOARDING` names an approved one (a logged skip until then), flag a missing Meta ad account, promote the client to `active`/`in_work`, and summarise on the task. |
| 5b | **Questionnaire chase** | Scheduled (daily) | Nudges an onboarded client who hasn't filled the questionnaire, at 3 and 7 days, then tells Dror and stops. Runs in the same daily job as the signature reminders (`src\scheduled.py::reminders_handler`). |
| ~~6~~ | ~~Monthly payment requests~~ | — | **Removed.** Dror invoices clients himself; the system does not touch Morning. |
| 7 | **Monthly campaign summary** | Scheduled (1st of month, `CampaignReportFunction`) / Button (`בנה דוח קמפיין`) | Pull the month's Meta Ads results, fill Dror's report template, add AI recommendations, send to Dror to approve → forward to client + save to Drive. The PDF renders in headless Chromium from a Lambda **layer** (`src\tools\publish_chromium_layer.py`), which `src\lib\pdf_chromium.py` inflates and drives over the DevTools protocol — no Playwright on Lambda (it renders only on a laptop, from `requirements-dev.txt`); prove it with a `{"check": "chromium"}` invoke. Emailing it still needs SMTP on the stack. |
| 8 | **Strategy bot** | Button (`בנה אסטרטגיה`, background task) / CLI | From the stored questionnaire answers + a live look at the digital presence (#3), Claude (adaptive thinking) writes the full strategy → Google Doc in `אסטרטגיה` → email Dror. Refuses without answers. |
| 9 | **ClickUp → Claude Code** (bonus) | Webhook (ClickUp task) | Turns a ClickUp task into a Claude Code work brief. |
| 10 | **Daily report** | Scheduled (daily 16:30 UTC, `DailyEmailFunction`) | Emails Dror everything the automations did that day (`daily_email`). Needs `DrorEmail` + SMTP on the stack; until then it logs one skipped line a day. |
| 11 | **Dashboard** | Always on (`DashboardFunction`, its own API) | Read-only web page over the run-log, grouped by subject, with links out — plus the questionnaire admin (`/admin`: editor, preview, responses, links, CSV). `src\dashboard.py` renders; `src\dashboard_lambda.py` serves it behind API Gateway with the session in a signed cookie. Password = the `DashboardPassword` parameter. Also runs locally: `python -m src.dashboard`. |
| 12 | **Smoove → ManyChat** | Webhook (Smoove: lead) | Standalone AWS Lambda (`src\smoove_handler.py`). Smoove POSTs `{f_name, cellphone, msg}`; find/create the ManyChat contact by phone and trigger the **Flow** named by `msg` (`msg`→`MANYCHAT_FLOW_<MSG>`, unmapped is rejected). Flow because the message is business-initiated → Meta-approved template only. |

## How they run

- **Production is the AWS stack** (`infra\template.yaml`, deployed as
  `dror-automations-dev` in eu-central-1): API Gateway → `src\lambda_handler.py`
  for the ClickUp webhook (`/clickup`), the buttons (`/action`), the signing page
  (`/sign`) and the questionnaire (`/questionnaire`); a separate Lambda for Smoove
  (`/smoove`); the dashboard on its own API (`src\dashboard_lambda.py`); and
  EventBridge schedules in `src\scheduled.py` — daily reminders, the daily email,
  the monthly campaign report. `WEBHOOK_DRY_RUN=1` turns every one of them into a
  mock run, schedules included.
- **Deploying.** Build from a clean checkout (`CodeUri: ../` packages the whole
  working folder), then `python -m src.tools.deploy_stack --stack <name>
  --env-file <.env>`: every parameter comes from the env file, the rest keep
  their stack values, no secret touches a command line. A single value:
  `python -m src.tools.push_stack_params <ParameterName>`. A second stack,
  `dror-automations-test` (`Stage=test`, `WEBHOOK_DRY_RUN=1`, fake tokens in
  `.env.test`), exercises the real wiring against mocks.
- **Webhook (local)** — `src\webhook_server.py` is the stdlib equivalent for
  development. It has no auth.
- **Scheduled (local)** — cron / Task Scheduler can run
  `python -m src.automations.<name>` instead of the EventBridge schedules.
- **Manual** — every automation has a CLI entrypoint with `--dry-run`; the buttons
  are those entrypoints with a ClickUp face.

Every automation writes to the **run-log** (`src\lib\run_log.py`) via
`Automation.log_action`. That log is the only data source for both the dashboard
and the daily email, so **if you add an automation, log through `log_action`** or it
will be invisible to Dror. Pass links as `url=` rather than burying them in
`detail=`.

## Conventions

Follow [`..\_shared\CONVENTIONS.md`](..\_shared\CONVENTIONS.md). Language:
**Python**. Retry + backoff on all outbound calls, JSON structured logging, secrets
from `.env` only, run instructions per automation in `README.md`.

**Document every automation for Dror.** [`docs\OPERATIONS.md`](docs\OPERATIONS.md)
is the Hebrew operator's guide — what each automation does, how Dror uses it, and
the hands-on procedures (e.g. the Meta partner + system-user setup for a new
client). When you **add or change an automation**, update `OPERATIONS.md` in the
same change: add its row to the automations table, and if it introduces an operator
step (a new credential, a manual action, a gotcha), write that step up under the
relevant section. The guide is meant to stay complete — an automation Dror can't
find there is one he doesn't know he has.

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
- **Google Forms → our own questionnaire page.** #2 originally WhatsApped a Google
  Forms link when a lead reached `initial_meeting`. The questionnaire is now our
  own form at `/questionnaire`, emailed by onboarding *after signing* (its answers
  seed the strategy), and submitting it is what runs #3. The `initial_meeting`
  trigger was retired with that move; `send_questionnaire` survives as the
  re-send button. Whether a pre-quote questionnaire is wanted back is Dror's call.
- **Make.** The original proposal budgeted Make as the orchestrator. This build
  stands alone on the AWS stack; Make can call it or be dropped.
