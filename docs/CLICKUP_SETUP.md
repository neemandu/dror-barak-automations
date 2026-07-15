# ClickUp CRM — the design, and how to build it

ClickUp holds two different things, and the design turns on keeping them apart:

- **a client** — a long-lived record with a lifecycle (`ליד → לקוח פעיל`)
- **a task for a client** — a unit of work with an assignee and a due date

They get **one list each**, linked. Everything below follows from that.

```
Workspace: Drorbrk
└── Space: Team Space
    ├── List: לקוחות     ← THE CLIENT lives here. One task per client.
    │     status      = ליד / לקוח פעיל / מושהה / הסתיים   (the lifecycle)
    │     fields      = phone, price, Drive folder, contract, Morning...
    │     comments    = the automation log
    │     → .env: CLICKUP_LIST_ID
    │
    └── List: משימות     ← THE WORK lives here. One task per work item.
          status      = to do / בעבודה / בבדיקה / הושלם
          assignee    = campaign manager / general worker
          לקוח (Relationship) → points at a task in לקוחות
          → .env: CLICKUP_TASKS_LIST_ID
```

**To see one client's tasks:** open the client's task in `לקוחות` — the `לקוח`
relationship lists every work task pointing at it. Or open `משימות` and
**Group by → לקוח**.

---

## ⚠️ Read this first: the workspace is on Free Forever

The plan caps the whole workspace at **60 Custom Field uses**, where a "use" is
counted **each time a value is set on a task's custom field**, accumulating across
the workspace and never resetting.

The CRM design below puts ~10 fields on each client. That is **~6 clients before
ClickUp stops accepting custom field values** — and the automations write to those
fields constantly (Drive folder, contract link, Morning status, secondary status).

**This design needs a paid ClickUp tier.** Paid plans lift the cap to unlimited;
check ClickUp's current pricing for the per-member cost, and note it bills per
member, so adding the campaign managers adds seats. For a system that runs the
business this is a small line item — but it is a real one, and it wasn't in the
original proposal's budget.

**If Dror will not upgrade,** see [Plan B](#plan-b--staying-on-free-forever) at the
bottom. It works, but it's worse in specific ways, and you should read them before
choosing it.

Check the plan any time:

```bash
python -m src.tools.check_clickup_crm --plan
```

---

## Why two lists (and not the obvious alternatives)

**Why not subtasks under the client?** It's the first thing you'd reach for, and
ClickUp breaks it: subtasks live in their parent's list, so they share its
statuses. "Write an Instagram post" would have to be `ליד` / `לקוח פעיל` /
`מושהה` / `הסתיים`. There's no way to give subtasks a different status set. It also
breaks the ClickUp→Claude Code bridge, which watches a list for new tasks — every
client status change would fire it.

**Why not a list per client (a folder of clients)?** ClickUp lists cannot hold
custom fields, so the client's own data — phone, price, Morning status — would have
nowhere to live. Billing would also have to query every list instead of one, and
each new client would need its list wired up before anything worked.

**Why one `משימות` list rather than per-client lists?** Because "all of Ronen's
tasks, across every client" is a question Dror actually asks, and one list answers
it with a filter. The per-client view is not lost: it's the Relationship field.

---

## Step 1 — the `לקוחות` list (the CRM)

In space **Team Space**, create a list named **`לקוחות`**.

### The primary status — two ways, both supported

The code reads the primary status from a **`סטטוס ראשי` dropdown field** if one
exists, and otherwise from the **task status**. Either works.

**As it is built today:** a `סטטוס ראשי` dropdown with the options `ליד`,
`לקוח פעיל`, `מושהה`, `הסתיים`.

**The recommended layout** is the list's task statuses instead (List → `⋯` →
**Statuses**), with the same four names. It is better in three ways:

- **Free.** Statuses cost nothing; a dropdown burns one Custom Field use per
  client, which matters a lot on Free Forever (see the cap above).
- **It's the pipeline view.** ClickUp's Board view groups by status, so the
  lead→active pipeline becomes a kanban. It cannot do that with a field.
- **Triggers.** ClickUp automations and webhooks fire natively on status change.

Whichever is used, `לקוח פעיל` is **required** — the monthly billing run selects
clients by it, and refuses to run rather than quietly bill nobody.

### Required custom fields

| Field name | Type | Used by |
|---|---|---|
| `טלפון` | Phone | Google Contacts, every WhatsApp message |
| `מחיר חודשי` | Number | The monthly payment request |
| `סטטוס משני` | **Dropdown** | Triggers the questionnaire and onboarding |

`סטטוס משני` options, exactly these five:

| Meaning | Name it | Also accepted |
|---|---|---|
| Initial meeting held | `פגישה ראשונית` | `פגישת היכרות` |
| Questionnaire sent | `נשלח שאלון` | `שאלון נשלח` |
| Quote sent | `נשלחה הצעת מחיר` | `הצעת מחיר נשלחה` |
| Signed | `חתם` | `נחתם`, `חוזה חתום` |
| In work | `בעבודה` | `in work` |

### Optional custom fields

Skip any and the automation that writes it logs a "skipped" line — nothing breaks.

| Field name | Type | Used by |
|---|---|---|
| `מייל` / `אימייל` | Email | Sending the quote |
| `סוג שירות` | Text | Strategy bot, campaign report |
| `תיקיית Drive` / `נתיב לגוגל דרייב` | **URL** | Onboarding writes the client's folder here |
| `חוזה חתום` | **URL** | The signed contract link |
| `נתיב הקלטות` | Text | Meeting recordings |
| `סטטוס Morning` | Text | Whether the client exists in Morning |
| `מזהה Morning` / `מזהה מורנינג` | Text | The Morning client id |

Any other field of Dror's is ignored — never read, never written.

> **`חוזה חתום` as an Attachment field is supported.** The signed PDF is uploaded
> into ClickUp rather than linked, which keeps the contract visible on the task
> itself. It takes two API calls (upload to the field, then point the task at the
> returned attachment id) and goes through the **v3** API, which is
> workspace-scoped — so it needs `CLICKUP_TEAM_ID` in `.env`. A URL field works
> too; the code handles either.
>
> The PDF still lands in the client's Drive folder either way. On Free Forever mind
> the 100MB workspace storage cap; contracts are small, so it is unlikely to bite.

> **Field names are matched loosely**, in Hebrew or English — `מחיר חודשי`,
> `מחיר חודשי ללא מעמ` and `price` all resolve to the same thing. Add a new
> spelling to `ALIASES` in `src/lib/crm_fields.py` rather than renaming in ClickUp.
>
> **Dropdowns are matched by their options, not their name.** A field called plain
> `סטטוס` could be either status; its options settle it — only the secondary status
> has an option meaning "questionnaire sent". So `סטטוס`, `סטטוס משני` and
> `Secondary status` all work, and the name can't put the code wrong.

### What goes where on a client task

| Thing | Where |
|---|---|
| Client name | The task name (`מכללת אלפא`) |
| Lifecycle | The task **status** |
| Everything else | Custom fields above |
| What the automations did | Task **comments** — the automation log |

## Step 2 — the `משימות` list (work per client)

Create a second list named **`משימות`** in the same space.

**Statuses:** `to do` / `בעבודה` / `בבדיקה` / `הושלם`. These are ordinary work
statuses and are not read by the CRM code — name them however the team works.

**One custom field:**

| Field name | Type | Points at |
|---|---|---|
| `לקוח` | **Relationship** → tasks in `לקוחות` | The client this work is for |

That field is the whole link. It makes the client task show its work, and lets
`משימות` group by client.

**Assignee** on each task is the campaign manager or the general worker.

> **The people aren't in the workspace yet.** It currently has two members: Dror and
> `office@smartflows.academy`. The two campaign managers and the general worker are
> not there. A `משימות` list assigned to nobody is a to-do list Dror keeps for
> himself — the value only arrives when the team is in it. On a paid plan each of
> them is a billable seat.

> **Scope check.** Task assignment and hour logging live in Google Sheets today, and
> Dror asked for hour-tracking to be left alone. `משימות` does not touch hours, but
> it does move his team's task board out of the tool they use now. Confirm he wants
> that before building it.

## Step 2b — the buttons (הצעת מחיר and friends)

Some work isn't a lifecycle event: Dror decides *when* to send a quote or build a
strategy. That's a **Button Custom Field** — clicking it runs an Automation, and
the Automation calls our webhook.

Buttons are better than a checkbox or a tag for this: a button holds no value, so
there is nothing to clear afterwards and no chance of our own cleanup re-firing
the automation.

For each button below: on the `לקוחות` list, add a **Button** Custom Field, set
its text, then configure its Automation as **Call webhook**:

- **URL:** `<ActionUrl>?action=<key>` — `ActionUrl` is a stack output
- **Method:** POST
- **Header:** `X-Automation-Token` = the `AutomationToken` given to the stack

| Button text | `?action=` | Does |
|---|---|---|
| `שלח הצעת מחיר` | `send_quote` | Sends the quote with a signature link |
| `שלח שאלון` | `send_questionnaire` | Re-sends the questionnaire link |
| `בנה דוח רשתות` | `social_prep` | Builds the social-media prep report |
| `בנה אסטרטגיה` | `strategy_bot` | Builds the full strategy into Drive |
| `בנה דוח קמפיין` | `campaign_summary` | Builds the monthly campaign report |

Each press comments its result back on the task — `✅ נשלחה הצעת מחיר ללקוח`, or the
error if it failed. Dror pressed a button; he shouldn't have to wonder.

> **Why the header token.** ClickUp signs API-registered webhooks, but **not**
> Automation webhooks. That header is the only thing separating a real press from
> anyone who finds the URL — and this endpoint sends quotes to clients. The Lambda
> refuses to serve if `AUTOMATION_TOKEN` is unset rather than defaulting to open.

> **Pressing twice.** A retried delivery of one press is de-duplicated. A *second,
> deliberate* press sends again — which is correct: a revised quote must go out.

> **What is deliberately not a button.** Onboarding (fires on `חתם`, and is guarded
> so it can't create two Drive folders) and the monthly payment run — a button that
> bills every active client sits one mis-tap from invoicing the whole list, and the
> WhatsApp messages cannot be unsent. That stays CLI-only.

> **On Free Forever:** button presses run ClickUp Automations, capped at **100
> actions/month**. Fine for Dror's volume. Whether the button field itself consumes
> Custom Field uses is worth watching, given the 60 cap.

## Step 3 — point the code at both lists

```
CLICKUP_LIST_ID=901819505305         # לקוחות
CLICKUP_TASKS_LIST_ID=901819505306   # משימות
```

Find the ids without hunting through URLs:

```bash
python -m src.tools.check_clickup_crm --discover
```

## Step 4 — check the setup

```bash
python -m src.tools.check_clickup_crm
```

Reports every status, field and dropdown option — present, missing or misnamed —
plus the plan and how close the workspace is to the 60-use cap. Exits non-zero
until the required pieces exist. It never writes to ClickUp.

## Step 5 — webhooks

| Event | Route | Fires |
|---|---|---|
| Task created in `לקוחות` | `POST /crm/new-lead` | Save the phone to Google Contacts |
| `סטטוס משני` → `פגישה ראשונית` | `POST /crm/status` | Send the questionnaire |
| `סטטוס משני` → `חתם` | `POST /crm/status` | Onboarding |
| Task created in `משימות` | `POST /clickup/task` | Hand the task to Claude Code |

Point the ClickUp→Claude Code webhook at **`משימות` only**. Aimed at `לקוחות` it
would fire on every client status change.

The webhook server has **no authentication of its own** — put it behind a proxy
with auth first. Anyone who can reach `/crm/status` can trigger onboarding for any
client id.

## Step 6 — migrate existing clients (optional)

```bash
# Look at the plan first — no writes, no credentials needed:
python -m src.tools.migrate_taskey_to_clickup --input taskey_export.csv --dry-run

# Then a few rows as a live smoke test:
python -m src.tools.migrate_taskey_to_clickup --input taskey_export.csv \
    --list-id <לקוחות id> --limit 3
```

It matches CSV columns by the same names as above, and writes every column into the
task description as well — so nothing is lost even if a field doesn't exist yet.

> On Free Forever, migrating 6 clients with 10 fields each **is** the 60-use cap.
> Upgrade before migrating, or migrate with `--limit` and accept that fields stop
> saving partway through.

---

## Plan B — staying on Free Forever

Only if Dror won't upgrade. Both substitutions dodge the custom-field cap, because
**statuses, tags, comments and descriptions are all unlimited and free**.

**Client data → the task description.** A structured block the automations parse
instead of custom fields. The migration tool already writes this block, so the data
survives either way.

- Costs: no filtering or sorting by price/status in ClickUp's UI, no dropdown for
  `סטטוס משני` (it becomes a **tag**, which is free and unlimited), and a typo in
  the description is a parse failure rather than an impossible value.

**Client on a work task → a tag** (`לקוח:אלפא`) instead of the Relationship field.

- Costs: no clickable link from client to work, no backlink on the client task, and
  a renamed client silently orphans its tasks.

**Keep the two required fields only** — `טלפון` and `מחיר חודשי` — at 2 uses per
client, giving ~30 clients before the cap. Everything else goes in the description.

Plan B is real, and it works. It is meaningfully worse to use day to day, and it
trades a $-per-month line item for permanent fragility. Recommend the upgrade.
