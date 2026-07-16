"""API client wrappers.

Each client wraps one external system (CRM/ClickUp, Google, Anthropic, ...).
Every client accepts ``dry_run``; in dry-run it performs no
network I/O and returns canned, documented responses so automations are fully
testable without credentials or production side effects.
"""
