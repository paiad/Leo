from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from bff.services.runtime.mcp_routing.runtime_plan_models import (
    ERROR_SCHEMA_VIOLATION,
    ERROR_SERVER_NOT_ALLOWED,
    ERROR_TOOL_NOT_ALLOWED,
    ERROR_POLICY_BLOCKED,
    PlanValidationResult,
    PlannerOutput,
    RetrievalResult,
)


class RuntimeMcpPlanValidator:
    def __init__(self, store: Any | None = None):
        self._store = store

    def validate(
        self,
        planner_json: dict[str, Any],
        *,
        retrieval: RetrievalResult,
    ) -> PlanValidationResult:
        try:
            plan = PlannerOutput.model_validate(planner_json)
        except ValidationError as exc:
            return PlanValidationResult(
                plan=None,
                error_code=ERROR_SCHEMA_VIOLATION,
                error_message=str(exc),
            )
        except ValueError as exc:
            return PlanValidationResult(
                plan=None,
                error_code=ERROR_SCHEMA_VIOLATION,
                error_message=str(exc),
            )

        for step in plan.plan_steps:
            if not self._server_exists_and_enabled(step.server_id):
                return PlanValidationResult(
                    plan=None,
                    error_code=ERROR_SERVER_NOT_ALLOWED,
                    error_message=f"server not allowed: {step.server_id}",
                )

            if not self._tool_exists(step.server_id, step.tool_name):
                return PlanValidationResult(
                    plan=None,
                    error_code=ERROR_TOOL_NOT_ALLOWED,
                    error_message=f"tool not allowed: {step.tool_name} for server {step.server_id}",
                )

            if self._is_policy_blocked(intent=retrieval.intent, server_id=step.server_id):
                return PlanValidationResult(
                    plan=None,
                    error_code=ERROR_POLICY_BLOCKED,
                    error_message=f"policy blocked server {step.server_id}",
                )

        return PlanValidationResult(plan=plan)

    def validate_schema_only(self, planner_json: dict[str, Any]) -> PlanValidationResult:
        try:
            plan = PlannerOutput.model_validate(planner_json)
        except ValidationError as exc:
            return PlanValidationResult(
                plan=None,
                error_code=ERROR_SCHEMA_VIOLATION,
                error_message=str(exc),
            )
        except ValueError as exc:
            return PlanValidationResult(
                plan=None,
                error_code=ERROR_SCHEMA_VIOLATION,
                error_message=str(exc),
            )
        return PlanValidationResult(plan=plan)

    def _is_policy_blocked(self, *, intent: str, server_id: str) -> bool:
        if not self._store:
            return False

        getter = getattr(self._store, "get_mcp_routing_policy", None)
        if not callable(getter):
            return False

        try:
            policy = getter(intent, server_id)
        except Exception:
            return False

        if policy is None:
            return False

        return not bool(getattr(policy, "enabled", True))

    @staticmethod
    def _canonical_tool_name(server_id: str, tool_name: str) -> str:
        sid = (server_id or "").strip().lower()
        name = (tool_name or "").strip().lower()
        prefix = f"mcp_{sid}_"
        if sid and name.startswith(prefix):
            return name[len(prefix) :]
        return name

    def _server_exists_and_enabled(self, server_id: str) -> bool:
        if not self._store:
            return True
        servers = getattr(self._store, "mcp_servers", {}) or {}
        server = servers.get(server_id)
        if not server:
            return False
        return bool(getattr(server, "enabled", False))

    def _tool_exists(self, server_id: str, tool_name: str) -> bool:
        canonical = self._canonical_tool_name(server_id, tool_name)
        if not canonical or canonical == "auto":
            return True
        if not self._store:
            return True
        servers = getattr(self._store, "mcp_servers", {}) or {}
        server = servers.get(server_id)
        if not server:
            return False
        discovered = getattr(server, "discoveredTools", []) or []
        for tool in discovered:
            if getattr(tool, "enabled", True) is False:
                continue
            name = str(getattr(tool, "name", "") or "").strip().lower()
            if name == canonical:
                return True
        return False
