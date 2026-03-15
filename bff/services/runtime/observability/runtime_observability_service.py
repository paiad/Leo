from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from bff.repositories.store import InMemoryStore, PostgresStore


class RuntimeObservabilityService:
    def __init__(self, store: InMemoryStore | PostgresStore):
        self._store = store

    @staticmethod
    def _to_iso(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _safe_rate(numerator: int, denominator: int) -> float | None:
        if denominator <= 0:
            return None
        return round((numerator / denominator) * 100, 2)

    @staticmethod
    def _safe_avg(values: list[float]) -> float | None:
        if not values:
            return None
        return round(sum(values) / len(values), 2)

    @staticmethod
    def _event_date(event: dict[str, Any]) -> str:
        created_at = str(event.get("createdAt") or event.get("created_at") or "")
        return created_at[:10] if len(created_at) >= 10 else ""

    @staticmethod
    def _is_fallback_event(event: dict[str, Any]) -> bool:
        if str(event.get("event_type") or "").strip().lower() != "gatekeeper":
            return False
        scores = event.get("scores") if isinstance(event.get("scores"), dict) else {}
        return bool(scores.get("pass") is False)

    @staticmethod
    def _is_route_hit(event: dict[str, Any]) -> bool | None:
        if str(event.get("event_type") or "").strip().lower() != "outcome":
            return None
        selected = str(event.get("selected_server_id") or "").strip().lower()
        if not selected:
            return None
        used = [str(item).strip().lower() for item in (event.get("used_servers") or [])]
        return selected in used

    def list_events(
        self,
        *,
        limit: int = 100,
        event_type: str | None = None,
        days: int = 1,
    ) -> list[dict[str, Any]]:
        normalized_days = max(1, min(30, int(days or 1)))
        since = datetime.now(timezone.utc) - timedelta(days=normalized_days)
        getter = getattr(self._store, "list_mcp_routing_events", None)
        if not callable(getter):
            return []
        events = getter(limit=limit, event_type=event_type, since_iso=self._to_iso(since))
        normalized: list[dict[str, Any]] = []
        for item in events:
            event = dict(item or {})
            scores = event.get("scores") if isinstance(event.get("scores"), dict) else {}
            if "request_preview" in scores and "request_preview" not in event:
                event["request_preview"] = scores.get("request_preview")
            normalized.append(event)
        return normalized

    def dashboard(self, *, days: int = 7) -> dict[str, Any]:
        normalized_days = max(1, min(30, int(days or 7)))
        since = datetime.now(timezone.utc) - timedelta(days=normalized_days)
        getter = getattr(self._store, "list_mcp_routing_events", None)
        if not callable(getter):
            return {
                "window": {
                    "days": normalized_days,
                    "sinceIso": self._to_iso(since),
                    "nowIso": self._to_iso(datetime.now(timezone.utc)),
                },
                "counts": {"total": 0, "byEventType": {}},
                "metrics": {
                    "routingAccuracy": None,
                    "toolSuccessRate": None,
                    "avgLatencyMs": None,
                    "fallbackTriggerRate": None,
                },
                "daily": [],
            }

        events = getter(limit=5000, event_type=None, since_iso=self._to_iso(since))
        normalized_events = [dict(item or {}) for item in events]

        counts_by_type: dict[str, int] = defaultdict(int)
        outcome_events: list[dict[str, Any]] = []
        gatekeeper_events: list[dict[str, Any]] = []

        for event in normalized_events:
            event_type = str(event.get("event_type") or "unknown").strip().lower() or "unknown"
            counts_by_type[event_type] += 1
            if event_type == "outcome":
                outcome_events.append(event)
            elif event_type == "gatekeeper":
                gatekeeper_events.append(event)

        route_hits = [hit for hit in (self._is_route_hit(event) for event in outcome_events) if hit is not None]
        route_hit_count = sum(1 for hit in route_hits if hit)

        success_values = [bool(event.get("success")) for event in outcome_events if event.get("success") is not None]
        success_count = sum(1 for ok in success_values if ok)

        latency_values = [
            float(event.get("latency_ms"))
            for event in outcome_events
            if isinstance(event.get("latency_ms"), (int, float))
        ]

        fallback_count = sum(1 for event in gatekeeper_events if self._is_fallback_event(event))

        daily_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in normalized_events:
            event_date = self._event_date(event)
            if event_date:
                daily_bucket[event_date].append(event)

        daily_rows: list[dict[str, Any]] = []
        for day in sorted(daily_bucket.keys()):
            bucket = daily_bucket[day]
            day_outcomes = [e for e in bucket if str(e.get("event_type") or "").lower() == "outcome"]
            day_gatekeepers = [e for e in bucket if str(e.get("event_type") or "").lower() == "gatekeeper"]

            day_hits = [hit for hit in (self._is_route_hit(event) for event in day_outcomes) if hit is not None]
            day_hit_count = sum(1 for hit in day_hits if hit)
            day_success_values = [bool(e.get("success")) for e in day_outcomes if e.get("success") is not None]
            day_success_count = sum(1 for ok in day_success_values if ok)
            day_latency_values = [
                float(e.get("latency_ms"))
                for e in day_outcomes
                if isinstance(e.get("latency_ms"), (int, float))
            ]
            day_fallback_count = sum(1 for e in day_gatekeepers if self._is_fallback_event(e))

            daily_rows.append(
                {
                    "date": day,
                    "requests": len(day_outcomes),
                    "routingAccuracy": self._safe_rate(day_hit_count, len(day_hits)),
                    "toolSuccessRate": self._safe_rate(day_success_count, len(day_success_values)),
                    "avgLatencyMs": self._safe_avg(day_latency_values),
                    "fallbackTriggerRate": self._safe_rate(day_fallback_count, len(day_gatekeepers)),
                }
            )

        return {
            "window": {
                "days": normalized_days,
                "sinceIso": self._to_iso(since),
                "nowIso": self._to_iso(datetime.now(timezone.utc)),
            },
            "counts": {
                "total": len(normalized_events),
                "byEventType": dict(sorted(counts_by_type.items(), key=lambda item: item[0])),
            },
            "metrics": {
                "routingAccuracy": self._safe_rate(route_hit_count, len(route_hits)),
                "toolSuccessRate": self._safe_rate(success_count, len(success_values)),
                "avgLatencyMs": self._safe_avg(latency_values),
                "fallbackTriggerRate": self._safe_rate(fallback_count, len(gatekeeper_events)),
            },
            "daily": daily_rows,
        }

    def purge_legacy_events(self) -> int:
        deleter = getattr(self._store, "delete_legacy_mcp_routing_events", None)
        if not callable(deleter):
            return 0
        return int(deleter() or 0)
