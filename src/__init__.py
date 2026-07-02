"""Dror Barak — sales & service automation workspace.

All automations live under ``src.automations`` and share infrastructure in
``src.lib`` (config, logging, retry, run-log, API clients). Every automation
supports a ``--dry-run`` mode that uses mock clients so it can be proven to work
without touching production systems or requiring real credentials.
"""
