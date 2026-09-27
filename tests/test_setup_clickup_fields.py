"""Creating the missing optional ClickUp columns: only what is missing."""

from src.tools import setup_clickup_fields as setup


def test_only_the_missing_columns_are_planned():
    fields = [{"id": "1", "name": "תשובות שאלון", "type": "attachment"},
              {"id": "2", "name": "חוזה נשלח", "type": "date"},
              {"id": "3", "name": "מחיר חודשי ללא מעמ", "type": "currency"}]
    todo = setup.missing(fields)
    assert "questionnaire_answers" not in todo and "quote_sent_at" not in todo
    assert "strategy_pdf" in todo and "price_strategy" in todo
    assert setup.missing(fields, only=["price_strategy"]) == ["price_strategy"]


def test_every_column_it_creates_is_one_the_code_recognises():
    from src.lib import crm_fields

    for key, (name, _type, _cfg) in setup.SPECS.items():
        assert crm_fields.canonical_for(name) == key, name
