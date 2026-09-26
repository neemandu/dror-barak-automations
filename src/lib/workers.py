"""The bots as Dror's employees: who they are and how a task says who does it.

A task on the משימות list is given to an employee through its ``עובד`` field. An
empty field means the task is for a person (the campaign managers keep their work
on the same list), so no bot touches it. Each employee is the same agent
(:mod:`src.automations.clickup_to_claude`) with its own job description, which is
added to the shared instructions.

``עובד`` is a **Relationship** field to the ``סוכנים`` list, where each task is an
agent: its name is the agent's name, its **description is its instructions**
(read on every run, so Dror edits them in ClickUp and the next task uses them),
and moving it to a closed status (``complete``) switches the agent off. A new
agent is a new task in that list; nothing to keep in sync.

A ``עובד`` *dropdown* (how the list was first set up) still works, with the four
built-in employees below, whose names must match its options. The statuses are the list's own: the bot moves a task to
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
    name: str          # how the bot signs its comments; the guard key per hand-over
    job: str           # added to the shared system prompt
    active: bool = True
    agent_id: str = ""  # the agent's task in סוכנים ("" for a built-in)


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

#: Not on the עובד list: revises the strategy task the strategy bot opens
#: (:mod:`src.lib.review_tasks`). The same brief writes the first version
#: (:mod:`src.automations.strategy_bot`).
STRATEGIST = Worker(
    "אסטרטג",
    "אתה אסטרטג שיווק בכיר בחברת הייעוץ של דרור ברק, שמלווה מכללות, אקדמיות ויוצרי "
    "קורסים בהגדלת הרשמות: וובינרים, משפכי שיווק, תוכן, וקמפיינים ממומנים במטא. "
    "אתה כותב מסמך אסטרטגיה בעברית, מעשי ומותאם ללקוח הספציפי - לא תבנית כללית.\n\n"
    "מבנה המסמך (כותרות Markdown ברמה 2):\n"
    "1. תקציר מנהלים\n2. קהל היעד והפרסונות\n3. שוק, מתחרים ובידול\n"
    "4. מסר ומיצוב\n5. תוכנית ערוצים ומשפך (כולל וובינר, תוכן וקמפיינים ממומנים)\n"
    "6. תוכנית פעולה ל-90 יום (לפי שבועות או חודשים)\n7. מדדי הצלחה ויעדים\n"
    "8. הנחות ושאלות פתוחות ללקוח\n\n"
    "בסס כל טענה על תשובות השאלון ועל ניתוח הנוכחות הדיגיטלית שקיבלת. כשאתה מניח הנחה "
    "שלא נאמרה - סמן אותה בסעיף 8 ולא כעובדה. אל תמציא מספרים על הלקוח. "
    "זה מסמך, לא שיחה: בלי הקדמה, ובלי שאלה או הצעה להמשך בסוף.",
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


def worker_of(task: dict[str, Any], clickup: Any = None) -> Optional[Worker]:
    """The employee the task is given to, or ``None`` (a task for a person).

    A list without the field at all gets :data:`DEFAULT`, so nothing changes for
    a workspace that has not added it yet. With the Relationship field, the agent's
    task is read (``clickup``) for its instructions and whether it is switched on.
    """
    field = _field(task)
    if field is None:
        return DEFAULT
    value = field.get("value")
    if value is None or value == "" or value == []:
        return None
    if field.get("type") == "list_relationship":
        linked = value[0] if isinstance(value, list) else None
        if not isinstance(linked, dict) or not linked.get("id"):
            return None
        return agent(str(linked["id"]), clickup)
    label = crm_fields.dropdown_label(field, value)
    return WORKERS.get(label or "")


def agent(agent_id: str, clickup: Any = None) -> Worker:
    """An agent from its task in the סוכנים list, as it reads right now."""
    if clickup is None:
        from .clients.clickup import ClickUpClient

        clickup = ClickUpClient()
    task = clickup.get_task(agent_id)
    closed = str((task.get("status") or {}).get("type") or "") == "closed"
    job = str(task.get("text_content") or task.get("description") or "").strip()
    return Worker(name=str(task.get("name") or agent_id).strip(), job=job,
                  active=not closed, agent_id=agent_id)


def names(task: dict[str, Any], clickup: Any = None) -> list[str]:
    """Every employee name the task's ``עובד`` could hold: the built-ins, and the
    agents in the list the Relationship field points at. Used to free the other
    agents' hand-over guards when the task changes hands."""
    out = list(WORKERS)
    field = _field(task)
    list_id = ((field or {}).get("type_config") or {}).get("subcategory_id")
    if field and field.get("type") == "list_relationship" and list_id:
        if clickup is None:
            from .clients.clickup import ClickUpClient

            clickup = ClickUpClient()
        out += [str(t.get("name") or "").strip() for t in clickup.list_tasks(str(list_id))]
    return out
