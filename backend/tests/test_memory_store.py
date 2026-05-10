from app.services.memory_store import MemoryStore


def test_memory_store_add_get_clear_history() -> None:
    store = MemoryStore()
    store.add_trace("session-1", {"trace_id": "t1"})
    store.add_trace("session-1", {"trace_id": "t2"})

    assert store.get_history("session-1") == [{"trace_id": "t1"}, {"trace_id": "t2"}]
    assert store.get_history("session-1", limit=1) == [{"trace_id": "t2"}]

    store.clear_history("session-1")
    assert store.get_history("session-1") == []
