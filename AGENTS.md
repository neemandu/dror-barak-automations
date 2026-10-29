# AGENTS.md

The rules for this repository are in [`CLAUDE.md`](CLAUDE.md). Read it in full
before any work and follow it exactly: it applies to every coding agent (Codex,
Claude Code, any other), not only to Claude. Then read `README.md` and
`docs\CREDENTIALS.md`, as it says.

The ones that cost the most when missed:

- **The repo is public.** Never commit `.env`, a key, or a client document.
- **Two people deploy the same AWS stack.** `git pull` before any work and before
  every deploy, and deploy only through `src\tools\deploy_stack.py`.
- **ClickUp is production.** Buttons and status changes run real automations that
  email clients. Ask Dror before writing anything there.

Where `CLAUDE.md` speaks of Claude Code itself (its memory, its connectors), skip
it. Where it speaks of Claude / Anthropic as part of the system (the strategy bot,
the social report, the ClickUp agent), that is the product's code and applies to
you as written.
