from bff.repositories.store import InMemoryStore
from bff.services.runtime.observability.runtime_observability_service import RuntimeObservabilityService


def test_delete_legacy_mcp_routing_events_removes_unscoped_rows():
    store = InMemoryStore(enable_persistence=False)
    store.record_mcp_routing_event(
        {"event_type": "outcome", "prompt_hash": "a", "intent": "web_search", "success": True}
    )
    store.record_mcp_routing_event(
        {
            "event_type": "outcome",
            "session_id": None,
            "prompt_hash": "b",
            "intent": "web_search",
            "success": True,
        }
    )
    store.record_mcp_routing_event(
        {
            "event_type": "outcome",
            "session_id": "",
            "prompt_hash": "c",
            "intent": "web_search",
            "success": True,
        }
    )
    store.record_mcp_routing_event(
        {
            "event_type": "outcome",
            "session_id": "session-1",
            "prompt_hash": "d",
            "intent": "web_search",
            "success": True,
        }
    )

    deleted = store.delete_legacy_mcp_routing_events()

    assert deleted == 3
    assert len(store.mcp_routing_events) == 1
    assert store.mcp_routing_events[0].get("session_id") == "session-1"


def test_runtime_observability_service_purges_legacy_events():
    store = InMemoryStore(enable_persistence=False)
    service = RuntimeObservabilityService(store=store)
    store.record_mcp_routing_event(
        {"event_type": "outcome", "prompt_hash": "a", "intent": "web_search", "success": True}
    )
    store.record_mcp_routing_event(
        {
            "event_type": "outcome",
            "session_id": "session-1",
            "prompt_hash": "b",
            "intent": "web_search",
            "success": True,
        }
    )

    deleted = service.purge_legacy_events()

    assert deleted == 1
    assert len(store.mcp_routing_events) == 1
