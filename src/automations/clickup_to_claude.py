"""T9 — ClickUp → Claude Code (bonus / gift module).

Trigger: webhook when a task is created/updated in ClickUp.
Action: turn the task into a work brief and hand it to Claude Code so it starts
working automatically, then comment back on the ClickUp task.

How "hand to Claude Code" runs is deployment-specific (Open Question #11). This
module builds the brief and dispatches it via a configurable command
``CLAUDE_CODE_CMD`` (the brief file path is appended as the final argument). If no
command is configured, it just drops the brief into ``CLAUDE_TASK_QUEUE_DIR`` for a
watcher/human to pick up. Dry-run records the intended brief without writing files
or spawning processes.

Manual/dry-run:
    python -m src.automations.clickup_to_claude --task-id abc123 --dry-run
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any

from ..lib import config
from ..lib.clients.clickup import ClickUpClient
from .base import Automation, build_arg_parser, run_cli

NAME = "clickup_to_claude"


def _build_brief(task: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"# ClickUp task {task.get('id')}",
            f"Title: {task.get('name','')}",
            f"Status: {task.get('status', {}).get('status','')}",
            "",
            "## Description",
            task.get("description", "") or "(no description)",
            "",
            "## Instruction to Claude Code",
            "Start working on this task. Report progress back on the ClickUp task.",
        ]
    )


def run(task_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    auto = Automation(NAME, dry_run=dry_run)
    clickup = ClickUpClient(dry_run=dry_run)

    task = clickup.get_task(task_id)
    brief = _build_brief(task)

    if dry_run:
        auto.log_action("brief_built", client_id=task_id, detail=task.get("name"))
        clickup.comment(task_id, "Claude Code picked up this task (dry-run).")
        return {"brief": brief, "dispatched": False}

    queue_dir = Path(config.get("CLAUDE_TASK_QUEUE_DIR", "claude_task_queue"))
    queue_dir.mkdir(parents=True, exist_ok=True)
    brief_path = queue_dir / f"task_{task_id}.md"
    brief_path.write_text(brief, encoding="utf-8")

    cmd = config.get("CLAUDE_CODE_CMD")
    dispatched = False
    if cmd:
        subprocess.Popen(shlex.split(cmd) + [str(brief_path)])  # fire-and-forget
        dispatched = True
        detail = f"dispatched via '{cmd}'"
    else:
        detail = f"queued at {brief_path}"

    clickup.comment(task_id, "Claude Code picked up this task.")
    auto.log_action("task_dispatched", client_id=task_id, detail=detail)
    return {"brief_path": str(brief_path), "dispatched": dispatched}


def main() -> None:
    parser = build_arg_parser(__doc__ or NAME)
    parser.add_argument("--task-id", required=True, help="ClickUp task id")
    run_cli(parser, lambda a: run(a.task_id, dry_run=a.dry_run))


if __name__ == "__main__":
    main()
