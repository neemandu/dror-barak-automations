"""Configuration loading.

All configuration and secrets come from environment variables / a ``.env`` file
(never hardcoded). ``.env`` is gitignored; ``.env.example`` documents every key.

In ``--dry-run`` mode, automations use mock clients and do not require real
credentials, so ``require()`` is only enforced on live runs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


class ConfigError(RuntimeError):
    """Raised when a required configuration value is missing on a live run."""


def load_dotenv(path: Optional[str] = None) -> None:
    """Load KEY=VALUE lines from a ``.env`` file into ``os.environ``.

    A tiny, dependency-free parser (so the project runs with only the stdlib).
    Existing environment variables win over ``.env`` so real deployments can
    override the file. Lines that are blank or start with ``#`` are ignored;
    surrounding quotes on values are stripped.
    """
    dotenv_path = Path(path) if path else _find_project_root() / ".env"
    if not dotenv_path.exists():
        return
    for raw in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _find_project_root() -> Path:
    """Walk up from this file to the folder that holds ``.env.example``."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".env.example").exists():
            return parent
    return here.parents[2]


def get(name: str, default: Optional[str] = None) -> Optional[str]:
    """Return an environment variable, or ``default`` if unset/empty."""
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def require(name: str) -> str:
    """Return a required environment variable or raise ``ConfigError``.

    Used by live API clients. Missing credentials are a genuine blocker, so the
    error names the exact ``.env`` key that must be provided.
    """
    value = get(name)
    if value is None:
        raise ConfigError(
            f"Missing required configuration '{name}'. "
            f"Set it in .env (see .env.example)."
        )
    return value


def get_bool(name: str, default: bool = False) -> bool:
    value = get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")
