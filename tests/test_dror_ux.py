"""Dror memorises nothing: what each thing he does in ClickUp causes is written down
where he looks, in Hebrew.

Small on purpose. It does not list every automation; it checks the handles Dror
actually touches: the buttons, the status that acts, the review tasks' first comments.
"""

import re
from pathlib import Path

from src.lib import actions, review_tasks, reminder_tasks

GUIDE = (Path(__file__).resolve().parents[1] / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
HEBREW = re.compile("[֐-׿]")


def _table() -> str:
    """The "what happens when I..." section of the guide."""
    start = GUIDE.index("## מה קורה כשאני")
    return GUIDE[start:GUIDE.index("## איך אתה עובד עם המערכת ביום-יום")]


def test_every_button_is_in_the_what_happens_table():
    table = _table()
    for action in actions.ACTIONS.values():
        assert f"`{action.label}`" in table, f"the button {action.label!r} is not explained in the guide"


def test_the_status_that_acts_is_in_the_table():
    assert "`חתם`" in _table() and "מייל ללקוח" in _table()


def test_what_a_button_says_back_is_hebrew_or_the_action_says_it_itself():
    sends = {"send_quote", "send_questionnaire"}  # they comment themselves: to whom, for how much
    for key, action in actions.ACTIONS.items():
        if key in sends:
            assert action.confirm == ""
        else:
            assert HEBREW.search(action.confirm), f"{key} confirms nothing Dror can read"


def test_a_review_task_explains_itself_in_its_first_comment():
    for kind in review_tasks.KINDS:
        assert HEBREW.search(kind.explain) and "עונים בשרשור" in kind.explain


def test_the_signing_follow_up_task_says_how_to_edit_or_cancel_it():
    assert "לבטל" in reminder_tasks._EXPLAIN and "תאריך היעד" in reminder_tasks._EXPLAIN
