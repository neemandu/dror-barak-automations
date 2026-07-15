# Google service account — setup guide

The automations touch Google in three places:

- **Contacts** — save a new lead's phone number
- **Drive** — create the client folder, copy templates, store signed PDFs and reports
- **Forms** — read the questionnaire answers

This sets up a **service account** that acts **as Dror**, so tokens never expire
and the files it creates are genuinely his.

**Time:** ~15 minutes. Most of it is one-time.

Check your work at any point:

```bash
python -m src.tools.check_google
```

---

## Before you start: two things that decide everything

**1. Is `drorbrk.co.il` a Google Workspace domain?**

This guide needs **Google Workspace** — the paid business Google. Steps 5–6 happen
in the Workspace **Admin console**, which a personal `@gmail.com` account does not
have.

Dror's address is `dror@drorbrk.co.il`, a custom domain, so it almost certainly is
Workspace. Confirm by visiting **admin.google.com** signed in as him: if it opens,
you're fine. If it doesn't, stop and read [Plan B](#plan-b--no-google-workspace).

**2. You need Workspace super-admin.** Step 5 is an admin-only screen. If Dror is
not the admin, that step has to go to whoever is.

---

## Why a service account and not "just log in"

A service account is a robot user with a private key. It can get a token whenever
it likes, with nobody present — which is what a 3am cron job needs.

But it is **not a person**. On its own:

- it has **no Contacts**, so "save the lead's phone" has nowhere to go
- Drive files it creates are owned by **it**, not Dror — they never appear in his
  Drive, and they vanish with the key
- it cannot read Dror's Forms

**Domain-wide delegation** fixes that: it lets the service account act *as*
`dror@drorbrk.co.il`. The contact lands in his Contacts; the folder appears in his
Drive; he keeps everything if we disappear. That is what step 5 sets up, and it is
the step people skip.

> **Be aware what you're granting.** Domain-wide delegation lets this key act as any
> user in the domain, for the scopes you list. That is why step 5 lists exactly
> three scopes and no more, and why the key is a secret on the level of a password.

---

## Step 1 — create a project

1. Go to **console.cloud.google.com**, signed in as Dror (or an account in his org)
2. Project dropdown, top left → **New Project**
3. Name it `dror-automations` → **Create**
4. Make sure it's selected before continuing

## Step 2 — enable the three APIs

**APIs & Services → Library**, search for and **Enable** each:

| API | For |
|---|---|
| **Google Drive API** | Client folders, templates, signed PDFs |
| **People API** | Saving a lead's phone to Contacts |
| **Google Forms API** | Reading questionnaire answers |

> Miss one and it fails only when that automation first runs — with a
> "API has not been used in project..." error that names the API and a link to
> enable it. `check_google` catches Drive and People up front.

## Step 3 — create the service account

1. **APIs & Services → Credentials → Create Credentials → Service account**
2. Name: `dror-automations` → **Create and continue**
3. **Skip** the "Grant this service account access to project" step — it grants
   *cloud* roles, which is not what we need. Click **Done**.
4. You now have an account like
   `dror-automations@dror-automations.iam.gserviceaccount.com`

## Step 4 — create a key

1. Click the service account → **Keys** tab → **Add key → Create new key**
2. Choose **JSON** → **Create**. A `.json` file downloads.
3. **This file is a password.** It grants everything below, with no expiry.
   - Keep it out of the repo — the repo is public
   - Don't email it or put it in Slack
   - If it leaks: delete the key here, create a new one

## Step 5 — domain-wide delegation (the one that matters)

This is admin-only, and the step that makes the rest work.

1. Copy the service account's **Client ID** (a long number, on its Details tab —
   `check_google` prints it too)
2. Go to **admin.google.com** as a super-admin
3. **Security → Access and data control → API controls**
4. Under *Domain-wide delegation* → **Manage domain-wide delegation** → **Add new**
5. **Client ID:** paste it
6. **OAuth scopes:** paste all three, comma-separated, exactly:

```
https://www.googleapis.com/auth/contacts,https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/forms.responses.readonly
```

7. **Authorize**

> Delegation can take a few minutes to take effect. If step 8 fails right away,
> wait five minutes and retry before changing anything.

## Step 6 — tell the code

In `.env`:

```bash
# The whole JSON file's contents on one line — this is what runs on Lambda,
# which has no filesystem to read a key from.
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"..."}

# Or, for local work only, a path to the file:
GOOGLE_SERVICE_ACCOUNT_FILE=C:\path\outside\the\repo\key.json

# The user the service account acts as. Contacts and Drive files end up here.
GOOGLE_IMPERSONATE_SUBJECT=dror@drorbrk.co.il
```

## Step 7 — share the clients folder

Strictly, impersonating Dror means the service account already reaches everything
he can. Sharing is still worth doing if the folder lives on a **Shared Drive**, or
if you later stop impersonating.

1. Open the Drive folder that holds client folders
2. **Share** → paste the service account's email (`...iam.gserviceaccount.com`)
3. Give it **Editor**. It won't send an email — that's normal.
4. Copy the folder id from the URL:
   `drive.google.com/drive/folders/`**`1AbC...`** → `DRIVE_CLIENTS_PARENT_ID=1AbC...`

## Step 8 — check it

```bash
python -m src.tools.check_google
```

Walks the whole chain and tells you which link is broken:

```
1. the key
  [ok]  service account: dror-automations@....iam.gserviceaccount.com
  [ok]  client id:       1234567890...   <- used in step 5
2. who it acts as
  [ok]  impersonating: dror@drorbrk.co.il
3. minting a token (this is where delegation is proven)
  [ok]  got a token
4. the scopes actually granted
  [ok]  contacts / drive / forms.responses.readonly
5. the APIs respond as Dror
  [ok]  Drive: responding as dror@drorbrk.co.il
```

Then prove writes work — creates one folder and deletes it:

```bash
python -m src.tools.check_google --test-drive
```

---

## When it goes wrong

| Error | What it means |
|---|---|
| `unauthorized_client` | Step 5 is missing, wrong Client ID, or a scope isn't listed. **The most common failure by far.** Scopes must match exactly. |
| `Invalid impersonation "sub"` | `GOOGLE_IMPERSONATE_SUBJECT` isn't a real user in the domain. Check the spelling. |
| `Precondition check failed` | Usually a personal Gmail account — delegation needs Workspace. |
| `API has not been used in project` | Step 2: that API isn't enabled. The error links straight to the fix. |
| `File not found: <id>` | Wrong folder id, or the folder is on a Shared Drive the account can't see. Do step 7. |
| Works, but Dror can't see the files | Impersonation isn't happening — the service account owns them. Check `GOOGLE_IMPERSONATE_SUBJECT` is set. |

## Plan B — no Google Workspace

If `drorbrk.co.il` is not Workspace, domain-wide delegation is impossible and this
guide does not apply. The alternative is **OAuth with a refresh token**: Dror
consents once in a browser, and we store the refresh token to mint access tokens
forever.

Trade-offs: it needs a one-time interactive consent (so it can't be scripted
end-to-end), the token breaks if he changes his password or revokes access, and an
unverified app's refresh token can expire after 7 days until the app is verified.

Tell me if that's the situation and I'll build it — it's a different auth module,
not a config change.

## On the key, once more

`GOOGLE_SERVICE_ACCOUNT_JSON` can act as Dror across Contacts, Drive and Forms,
indefinitely, with no MFA and no prompt. Treat it like his password:

- never in git — the repo is public, and `.gitignore` blocks `*.json` keys only if
  you put them where it looks. Keep the file outside the repo entirely.
- in AWS it belongs in the Lambda's environment (or Secrets Manager), which is
  where the deploy puts it.
- rotate it by creating a new key and deleting the old one in the Keys tab. Nothing
  else needs to change.
