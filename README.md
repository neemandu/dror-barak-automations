# Dror Barak — Sales & Service Automations

Automations that connect Dror Barak's workflow **from lead to active client** across
Taskey (CRM), Morning (billing), Green API (WhatsApp), Fillout (e-signature),
Google Workspace, ClickUp, and Claude/AI. See [`CLAUDE.md`](CLAUDE.md) for the full
context and [`TASKS.md`](TASKS.md) for status + open questions.

## Quick start

```bash
python -m pip install -r requirements.txt   # only needed for LIVE runs
cp .env.example .env                         # fill in real credentials for live runs
python -m pytest                             # run everything in dry-run (no creds needed)
```

Every automation has a `--dry-run` flag that uses mock clients — **no credentials,
no network, no production side effects.** Drop `--dry-run` (and provide `.env`) to
run live.

> On Windows, if the console shows garbled Hebrew, run with UTF-8:
> `set PYTHONUTF8=1` (cmd) or `$env:PYTHONUTF8=1` (PowerShell).

## Automations

| Task | Module | Trigger | Manual / dry-run command |
|---|---|---|---|
| T1 | `lead_to_contacts` | Webhook: new CRM lead | `python -m src.automations.lead_to_contacts --client-id 42 --dry-run` |
| T2 | `send_questionnaire` | Webhook: status → initial meeting | `python -m src.automations.send_questionnaire --client-id 42 --dry-run` |
| T3 | `social_prep` | Webhook: form submit / manual | `python -m src.automations.social_prep --client-id 42 --dry-run` |
| T4 | `send_quote` | Manual send + Fillout webhook | `python -m src.automations.send_quote --action send --client-id 42 --dry-run` |
| T5 | `onboarding` | Webhook: status → signed | `python -m src.automations.onboarding --client-id 42 --dry-run` |
| T6 | `monthly_payment_requests` | Scheduled: 1st of month | `python -m src.automations.monthly_payment_requests --dry-run` |
| T7 | `campaign_summary` | Scheduled: month end / manual | `python -m src.automations.campaign_summary --client-id 42 --dry-run` |
| T8 | `strategy_bot` | Manual | `python -m src.automations.strategy_bot --client-id 42 --dry-run` |
| T9 | `clickup_to_claude` | Webhook: ClickUp task | `python -m src.automations.clickup_to_claude --task-id abc --dry-run` |
| T10 | `daily_summary` | Scheduled: end of day | `python -m src.automations.daily_summary --dry-run` |

## The three run modes

**Manual** — run any module directly, as in the table above.

**Scheduled** — point cron / Windows Task Scheduler at the module. Examples:

```cron
# Monthly payment requests — 1st of month, 09:00
0 9 1 * *  cd /path/to/dror_barak && python -m src.automations.monthly_payment_requests
# Daily summary to Dror — every day 19:00
0 19 * * * cd /path/to/dror_barak && python -m src.automations.daily_summary
# Campaign summaries — last-day handling done in-script; run 28th 08:00
0 8 28 * * cd /path/to/dror_barak && python -m src.automations.campaign_summary --client-id <id>
```

**Webhook** — start the receiver and point each system's webhook at the route:

```bash
python -m src.webhook_server            # live  (PORT via WEBHOOK_PORT, default 8000)
python -m src.webhook_server --dry-run  # dispatch automations in dry-run
```

| System event | Route | Automation |
|---|---|---|
| CRM: new lead | `POST /crm/new-lead` | T1 |
| CRM: status change | `POST /crm/status` | T2 (initial meeting) / T5 (signed) |
| Google Forms: submit | `POST /forms/submit` | T3 |
| Fillout: signed | `POST /fillout/signed` | T4 (capture) |
| ClickUp: task | `POST /clickup/task` | T9 |

Put the receiver behind a reverse proxy with auth before exposing it publicly.

## How it's built (and why it runs without credentials)

- All logic lives in `src/automations/*`; shared infra in `src/lib/*`.
- Every outbound call goes through API clients in `src/lib/clients/*` that support
  `dry_run`. In dry-run they return canned, documented responses and record the
  call they *would* have made — so the full pipeline is exercised and tested with
  no secrets. Live mode uses the real REST endpoints with retry + backoff.
- Config/secrets come only from `.env`; logs are structured JSON; every run writes
  to an append-only run-log (`logs/run_log.jsonl`) that feeds the CRM log and the
  daily WhatsApp summary.

## Conventions

Follows [`../_shared/CONVENTIONS.md`](../_shared/CONVENTIONS.md): retry/backoff on
all API calls, structured logging, secrets via `.env` only, and these run
instructions. Language: Python.

## Testing

```bash
python -m pytest        # 24 tests: infra unit tests + a dry-run test per automation
```
