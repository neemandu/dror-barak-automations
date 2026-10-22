"""The strategy is written from the strategy agent's own instructions in ClickUp."""
from src.lib import workers


class _Click:
    def __init__(self, status="open", text="הוראות של דרור"):
        self.status, self.text = status, text

    def get_list_fields(self, list_id):
        return [{"name": "עובד", "type_config": {"subcategory_id": "L9"}}]

    def list_tasks(self, list_id):
        assert list_id == "L9"
        return [{"id": "a1", "name": "כותב תוכן"}, {"id": "a2", "name": workers.STRATEGY_AGENT}]

    def get_task(self, task_id):
        assert task_id == "a2"
        return {"id": "a2", "name": workers.STRATEGY_AGENT, "text_content": self.text,
                "status": {"type": self.status}}


def test_uses_the_agents_instructions_plus_delivery_rules(monkeypatch):
    monkeypatch.setenv("CLICKUP_TASKS_LIST_ID", "T1")
    w = workers.strategist(_Click())
    assert w.job.startswith("הוראות של דרור") and w.job.endswith(workers.STRATEGY_DELIVERY)
    assert w.name == workers.STRATEGY_AGENT and w.active and w.agent_id == "a2"


def test_a_closed_agent_stays_off(monkeypatch):
    monkeypatch.setenv("CLICKUP_TASKS_LIST_ID", "T1")
    assert workers.strategist(_Click(status="closed")).active is False


def test_falls_back_to_the_built_in_when_clickup_has_no_such_agent(monkeypatch):
    monkeypatch.setenv("CLICKUP_TASKS_LIST_ID", "T1")
    monkeypatch.setenv("STRATEGY_AGENT_NAME", "לא קיים")
    assert workers.strategist(_Click()) is workers.STRATEGIST

    class Broken(_Click):
        def get_list_fields(self, list_id):
            raise RuntimeError("down")

    assert workers.strategist(Broken()) is workers.STRATEGIST


def test_examples_come_from_docs_in_the_examples_subfolder(monkeypatch):
    from src.lib import strategy_examples as se

    monkeypatch.setenv("DRIVE_TEMPLATES_FOLDER_ID", "TPL")

    class G:
        def list_folder(self, parent):
            if parent == "TPL":
                return [{"id": "F", "name": se.FOLDER, "mimeType": "application/vnd.google-apps.folder"}]
            return [{"id": "D1", "name": "דוגמה א", "mimeType": se.DOC_MIME},
                    {"id": "X", "name": "גיליון", "mimeType": "application/vnd.google-apps.spreadsheet"}]

        def _headers(self):
            return {}

    class R:
        content = "תוכן הדוגמה".encode()

    monkeypatch.setattr(se, "request", lambda *a, **k: R())
    text = se.load(G())
    assert "דוגמה: דוגמה א" in text and "תוכן הדוגמה" in text and "גיליון" not in text
    monkeypatch.setenv("DRIVE_TEMPLATES_FOLDER_ID", "")
    assert se.load(G()) == ""
