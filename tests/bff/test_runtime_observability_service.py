from bff.repositories.store import InMemoryStore
from bff.services.runtime.observability.runtime_observability_service import RuntimeObservabilityService


def _record(store: InMemoryStore, payload: dict):
    store.record_mcp_routing_event(payload)


def test_runtime_observability_dashboard_metrics():
    store = InMemoryStore(enable_persistence=False)
    service = RuntimeObservabilityService(store=store)

    _record(
        store,
        {
            "event_type": "gatekeeper",
            "prompt_hash": "a",
            "intent": "web_search",
            "scores": {"pass": False, "error_code": "E1002_SCHEMA_VIOLATION"},
        },
    )
    _record(
        store,
        {
            "event_type": "outcome",
            "prompt_hash": "a",
            "intent": "web_search",
            "selected_server_id": "trendradar",
            "used_servers": ["trendradar"],
            "success": True,
            "latency_ms": 1200,
        },
    )
    _record(
        store,
        {
            "event_type": "outcome",
            "prompt_hash": "b",
            "intent": "repo_ops",
            "selected_server_id": "github",
            "used_servers": ["playwright"],
            "success": False,
            "latency_ms": 1800,
        },
    )

    dashboard = service.dashboard(days=7)

    assert dashboard["counts"]["total"] == 3
    assert dashboard["metrics"]["routingAccuracy"] == 50.0
    assert dashboard["metrics"]["toolSuccessRate"] == 50.0
    assert dashboard["metrics"]["avgLatencyMs"] == 1500.0
    assert dashboard["metrics"]["fallbackTriggerRate"] == 100.0


def test_runtime_observability_list_events_filters_by_type():
    store = InMemoryStore(enable_persistence=False)
    service = RuntimeObservabilityService(store=store)

    _record(
        store,
        {
            "event_type": "planner",
            "prompt_hash": "1",
            "intent": "web_search",
        },
    )
    _record(
        store,
        {
            "event_type": "outcome",
            "prompt_hash": "2",
            "intent": "repo_ops",
            "scores": {"request_preview": "给我 github 最近提交"},
            "success": True,
        },
    )

    events = service.list_events(limit=10, event_type="outcome", days=7)

    assert len(events) == 1
    assert events[0]["event_type"] == "outcome"
    assert events[0]["request_preview"] == "给我 github 最近提交"
