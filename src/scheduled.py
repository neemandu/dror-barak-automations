"""EventBridge entrypoints for the scheduled automations.

Kept apart from lambda_handler (the HTTP/webhook entry): a scheduled invoke has no
request, no signature, no idempotency key — just "run the job". Mixing the two
entrypoints would blur which env and which guards each needs.
"""

from __future__ import annotations

from typing import Any

from .lib import config


def reminders_handler(event: dict[str, Any] | None = None, context: Any = None) -> dict[str, Any]:
    """Daily: chase clients who were sent a contract but haven't signed."""
    config.load_dotenv()
    from .automations import sign_reminders

    return sign_reminders.run()
