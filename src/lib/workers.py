"""The bots as Dror's employees: who they are and how a task says who does it.

A task on the משימות list is given to an employee through the ``עובד`` dropdown.
An empty field means the task is for a person (the campaign managers keep their
work on the same list), so no bot touches it. Each employee is the same agent
(:mod:`src.automations.clickup_to_claude`) with its own job description, which is
added to the shared instructions.

The names here must match the dropdown's options exactly; ``docs/CLICKUP_SETUP.md``
step 2 lists them. The statuses are the list's own: the bot moves a task to
``in progress`` when it starts and to ``לבדיקה של דרור`` when its answer is in,
which is the inbox view Dror works from.
"""

from __future__ import annotations

from typing import Any, NamedTuple, Optional

from . import crm_fields

FIELD = "עובד"

#: The list's statuses the bot moves a task through.
STATUS_WORKING = "in progress"
STATUS_REVIEW = "לבדיקה של דרור"


class Worker(NamedTuple):
    name: str          # the dropdown option, and how the bot signs its comments
    job: str           # added to the shared system prompt


WRITER = Worker(
    "כותב תוכן",
    "Your role on the team: the copywriter. Posts, ads, emails, landing-page and "
    "webinar copy, video scripts, WhatsApp and email sequences. Deliver copy that is "
    "ready to publish. When the task asks for several options, give distinct angles, "
    "not rewordings of one idea.",
)

SOCIAL = Worker(
    "אנליסט רשתות",
    "Your role on the team: the social-media analyst. You study the Instagram and "
    "Facebook presence of the client and of their competitors (from the task, or "
    "from the client's questionnaire answers). Open the profiles and pages with web "
    "fetch and search, and report what you actually saw: recent posts and their "
    "formats, what gets visible engagement, recurring themes, what stands out. Then "
    "give 3 to 5 concrete ideas the client can use this week. Social sites often "
    "block automated reading: say plainly what you could not see, and never fill the "
    "gap with guesses.",
)

CAMPAIGNS = Worker(
    "מנהל קמפיינים",
    "Your role on the team: the campaign manager for Meta ads, working from this "
    "client's own ad account data. Unless the task says "
    "otherwise, compare the last 7 full days with the 7 before them. Look for spend "
    "pacing, changes in cost per lead, campaigns spending with no leads, and ad "
    "fatigue (rising cost, falling CTR); drill down to ad sets or ads where it "
    "matters. Recommend concrete changes, each with its reason and the number behind "
    "it. You never change budgets or ads yourself: Dror decides and does it. If you "
    "have no Meta Ads data, the client has no ad account linked; say so.",
)

ASSISTANT = Worker(
    "עוזר אישי",
    "Your role on the team: Dror's personal assistant. His inbox, meeting "
    "preparation, summaries, follow-ups and replies. Use Gmail and Drive, and the "
    "web to learn about a person or organisation before a meeting. Replies are Gmail "
    "drafts for Dror to review and send.",
)

WORKERS: dict[str, Worker] = {w.name: w for w in (WRITER, SOCIAL, CAMPAIGNS, ASSISTANT)}

#: Who does a task when the list has no ``עובד`` field at all (a workspace set up
#: before the field existed): the copywriter, as the bot always did.
DEFAULT = WRITER


def _field(task: dict[str, Any]) -> Optional[dict[str, Any]]:
    wanted = crm_fields.field_key(FIELD)
    for field in task.get("custom_fields") or []:
        if crm_fields.field_key(str(field.get("name") or "")) == wanted:
            return field
    return None


def has_field(task: dict[str, Any]) -> bool:
    return _field(task) is not None


def worker_of(task: dict[str, Any]) -> Optional[Worker]:
    """The employee the task is given to, or ``None`` (a task for a person).

    A list without the field at all gets :data:`DEFAULT`, so nothing changes for
    a workspace that has not added it yet.
    """
    field = _field(task)
    if field is None:
        return DEFAULT
    value = field.get("value")
    if value is None or value == "":
        return None
    label = crm_fields.dropdown_label(field, value)
    return WORKERS.get(label or "")
