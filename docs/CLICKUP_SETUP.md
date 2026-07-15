# ClickUp CRM — how to set up the clients list

ClickUp is the CRM: **one task per client**. The automations read the task to know
who the client is, and write their results back onto it.

**This is a one-time manual job.** ClickUp's API cannot create custom fields — it
can only read them and set values on fields that already exist. So the list has to
be built by hand in the ClickUp UI, once. After that everything is automatic.

**You do not need to copy any field ids.** The code matches fields, statuses and
dropdown options by *name*, in Hebrew or English. Name things as below and it just
works. Rename something and the checker will tell you.

## Current state

The workspace (`Drorbrk`, team `90182874674`) is still the default ClickUp
template: lists called `Project 1`, `Project 2` and `Get Started with ClickUp`,
with default statuses and no custom fields. **None of the CRM exists yet.**

## Step 1 — create the list

In space **Team Space**, create a list called **לקוחות** (or anything — you'll put
its id in `.env`).

## Step 2 — set the list's statuses

These are the **primary status**. In the list → `⋯` → **Statuses** → customise, so
the statuses are exactly these four (any of the accepted names works):

| Meaning | Name it | Also accepted |
|---|---|---|
| Lead | `ליד` | `lead` |
| Active client | `לקוח פעיל` | `פעיל`, `active` |
| Paused | `מושהה` | `בהמתנה`, `paused` |
| Finished | `הסתיים` | `סיום`, `finished`, `complete` |

> **`לקוח פעיל` is required.** The monthly billing run selects clients by it. Without
> it, that automation refuses to run rather than quietly bill nobody.

## Step 3 — add the custom fields

List → `+` in the header row → **New field**. Name and type matter; order doesn't.

### Required

| Field name | Type | Used by |
|---|---|---|
| `טלפון` | Phone | Saving the lead to Contacts, every WhatsApp message |
| `מחיר חודשי` | Number | The monthly payment request |
| `סטטוס משני` | **Dropdown** | Triggers the questionnaire and onboarding |

### The `סטטוס משני` dropdown options

Add these five, exactly (or their accepted alternatives):

| Meaning | Name it | Also accepted |
|---|---|---|
| Initial meeting held | `פגישה ראשונית` | `פגישת היכרות` |
| Questionnaire sent | `נשלח שאלון` | `שאלון נשלח` |
| Quote sent | `נשלחה הצעת מחיר` | `הצעת מחיר נשלחה` |
| Signed | `חתם` | `נחתם`, `חוזה חתום` |
| In work | `בעבודה` | `in work` |

### Optional

Skip any of these and the automations that write to them will log a "skipped"
line — nothing breaks, the value just has nowhere to go.

| Field name | Type | Used by |
|---|---|---|
| `מייל` | Email | Sending the quote |
| `סוג שירות` | Text | The strategy bot, the campaign report |
| `תיקיית Drive` | URL | Onboarding writes the client's folder here |
| `חוזה חתום` | URL | The signed contract link |
| `נתיב הקלטות` | Text | Meeting recordings |
| `סטטוס Morning` | Text | Whether the client exists in Morning |
| `מזהה Morning` | Text | The Morning client id |

You can keep any other fields of your own alongside these — anything not in this
table is ignored, never read and never written.

## Step 4 — point the code at the list

Open the list in the browser. The URL looks like:

```
https://app.clickup.com/90182874674/v/li/901819505305
                                          ^^^^^^^^^^^^ this is the list id
```

Put it in `.env`:

```
CLICKUP_LIST_ID=901819505305
```

Or find it without the browser:

```bash
python -m src.tools.check_clickup_crm --discover
```

## Step 5 — check the setup

```bash
python -m src.tools.check_clickup_crm
```

It reports every status, field and dropdown option — present, missing, or
misnamed — and exits non-zero until the required ones are there. It never writes
to ClickUp.

```
  [ok]  active    -> 'לקוח פעיל'
  [!!]  phone              -> MISSING. Name it one of: ['phone', 'mobile', 'טלפון']
```

## Step 6 — migrate the existing clients (optional)

If there is a Taskey export:

```bash
# Always look at the plan first — no writes, no credentials needed:
python -m src.tools.migrate_taskey_to_clickup --input taskey_export.csv --dry-run

# Then a few rows as a live smoke test:
python -m src.tools.migrate_taskey_to_clickup --input taskey_export.csv \
    --list-id 901819505305 --limit 3
```

The migration matches CSV columns by the same names as above, so a `מחיר חודשי`
column lands in the `מחיר חודשי` field.

## Step 7 — webhooks

So ClickUp triggers the automations on status change, register a webhook pointing
at the public webhook server:

| Event | Route | Fires |
|---|---|---|
| Task created | `POST /crm/new-lead` | Save the phone to Google Contacts |
| Status changed → `פגישה ראשונית` | `POST /crm/status` | Send the questionnaire |
| Status changed → `חתם` | `POST /crm/status` | Onboarding |

The webhook server has **no authentication of its own** — put it behind a proxy
with auth first. Anyone who can reach `/crm/status` can trigger onboarding for any
client.
