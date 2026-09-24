"""The DynamoDB run-log reads a long range in one scan, not a query per day."""

from datetime import datetime, timedelta, timezone

from src.lib import run_log


class _Table:
    def __init__(self, items):
        self.items, self.queries, self.scans = items, 0, 0

    def query(self, **kwargs):
        self.queries += 1
        return {"Items": []}

    def scan(self, **kwargs):
        self.scans += 1
        return {"Items": list(self.items)}


def _store(items):
    store = run_log._DynamoStore.__new__(run_log._DynamoStore)
    store.table = _Table(items)
    return store


def test_everything_is_one_scan_and_still_sorted_and_windowed():
    now = datetime.now(timezone.utc)
    items = [{"day": "x", "ts_id": "1", "ts": (now - timedelta(days=2)).isoformat(), "action": "b"},
             {"day": "x", "ts_id": "2", "ts": (now - timedelta(days=500)).isoformat(), "action": "old"},
             {"day": "x", "ts_id": "3", "ts": (now - timedelta(days=90)).isoformat(), "action": "a"}]
    store = _store(items)
    out = store.read_since(now - timedelta(days=run_log.TTL_DAYS))
    assert store.table.scans == 1 and store.table.queries == 0, "read_all asked 401 day partitions in turn"
    assert [e["action"] for e in out] == ["a", "b"]


def test_a_week_is_still_a_query_per_day():
    store = _store([])
    store.read_since(datetime.now(timezone.utc) - timedelta(days=7))
    assert store.table.scans == 0 and store.table.queries == 8
