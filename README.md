# Dror Barak — Sales & Service Automations

Automations that connect Dror Barak's workflow **from lead to active client** across
ClickUp (CRM), ManyChat (WhatsApp via the official Meta API),
Google Workspace, Meta Ads, and Claude/AI — plus a read-only dashboard and a daily
email report so Dror can see everything that ran.

See [`CLAUDE.md`](CLAUDE.md) for the full context, [`docs/CREDENTIALS.md`](docs/CREDENTIALS.md)
for how to obtain each credential, and [`TASKS.md`](TASKS.md) for status + open
questions.

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
| T1 | `lead_to_contacts` | Webhook: new ClickUp lead | `python -m src.automations.lead_to_contacts --client-id 42 --dry-run` |
| T3 | `social_prep` | Webhook: form submit / manual | `python -m src.automations.social_prep --client-id 42 --dry-run` |
| T4 | `send_quote` | Manual send + signing webhook | `python -m src.automations.send_quote --action send --client-id 42 --dry-run` |
| T5 | `onboarding` | Webhook: status → signed | `python -m src.automations.onboarding --client-id 42 --dry-run` |
| T7 | `campaign_summary` | Scheduled: 1st of month / manual | `python -m src.automations.campaign_summary --client-id 42 --dry-run` (or `--all`) |
| T8 | `strategy_bot` | Manual | `python -m src.automations.strategy_bot --client-id 42 --dry-run` |
| T9 | `clickup_to_claude` | Webhook: ClickUp task | `python -m src.automations.clickup_to_claude --task-id abc --dry-run` |
| T10 | `daily_email` | Scheduled: end of day | `python -m src.automations.daily_email --dry-run` |
| T11 | `dashboard` | Always on | `python -m src.dashboard --dry-run` |
| T12 | `smoove_to_manychat` | Webhook: Smoove lead | `python -m src.automations.smoove_to_manychat --first-name דנה --phone 0501234567 --msg ai_agents --dry-run` |

`daily_summary` (the WhatsApp version of T10) is superseded by `daily_email` — see
the 24-hour-window note in [`CLAUDE.md`](CLAUDE.md).

## Dashboard

A read-only page over the run-log, grouped into subjects (invoices, leads, campaign
reports), with links out to Drive / ClickUp. Failures are pinned to the
top. Nothing can be triggered from it.

```bash
python -m src.dashboard --dry-run     # sample data, no .env needed → http://localhost:8080
python -m src.dashboard               # live; requires DASHBOARD_PASSWORD in .env
```

It refuses to start without `DASHBOARD_PASSWORD` — it shows client names, phone
numbers and prices, so an open dashboard would publish all of it. Serve it over
HTTPS; only set `DASHBOARD_INSECURE_COOKIE=1` for local http development.

## The three run modes

**Manual** — run any module directly, as in the table above.

**Scheduled** — point cron / Windows Task Scheduler at the module. Examples:

```cron
# Daily report to Dror — every day 19:00
0 19 * * * cd /path/to/dror_barak && python -m src.automations.daily_email
# Campaign summaries — reports the previous (closed) month; run 1st 08:00.
# On AWS this is the CampaignReportFunction schedule; --all covers every client.
0 8 1 * * cd /path/to/dror_barak && python -m src.automations.campaign_summary --all
```

**Webhook** — start the receiver and point each system's webhook at the route:

```bash
python -m src.webhook_server            # live  (PORT via WEBHOOK_PORT, default 8000)
python -m src.webhook_server --dry-run  # dispatch automations in dry-run
```

| System event | Route | Automation |
|---|---|---|
| ClickUp: new lead | `POST /crm/new-lead` | T1 |
| ClickUp: status change | `POST /crm/status` | T2 (initial meeting) / T5 (signed) |
| ClickUp: task | `POST /clickup/task` | T9 |
| Smoove: lead | `POST /smoove` | T12 (find/create ManyChat contact → Flow) |

**The receiver has no authentication of its own.** Put it behind a reverse proxy
with auth before exposing it publicly — anyone who can reach `/crm/status` can
trigger onboarding for any client id.

## How it's built (and why it runs without credentials)

- All logic lives in `src/automations/*`; shared infra in `src/lib/*`.
- Every outbound call goes through API clients in `src/lib/clients/*` that support
  `dry_run`. In dry-run they return canned, documented responses and record the
  call they *would* have made — so the full pipeline is exercised and tested with
  no secrets. Live mode uses the real REST endpoints with retry + backoff.
- Config/secrets come only from `.env`; logs are structured JSON; every run writes
  to an append-only run-log (`logs/run_log.jsonl`) via `Automation.log_action`. That
  log is the only source for the ClickUp log, the dashboard and the daily email — so
  an automation that doesn't log through it is invisible to Dror.

## Conventions

Follows [`../_shared/CONVENTIONS.md`](../_shared/CONVENTIONS.md): retry/backoff on
all API calls, structured logging, secrets via `.env` only, and these run
instructions. Language: Python.

## Testing

```bash
python -m pytest        # 278 tests: infra, dashboard/auth, and a dry-run test per automation
```

Dry-run is not proof on its own. If you change something with a real runtime
surface, drive it — both dashboard bugs found so far passed the test suite.
