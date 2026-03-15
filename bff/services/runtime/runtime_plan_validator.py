from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from bff.services.runtime.runtime_plan_models import (
    ERROR_SCHEMA_VIOLATION,
    ERROR_SERVER_NOT_ALLOWED,
    ERROR_TOOL_NOT_ALLOWED,
    ERROR_POLICY_BLOCKED,
    PlanValidationResult,
    PlannerOutput,
    PrefilterResult,
)


class RuntimeMcpPlanValidator:
    def __init__(self, store: Any | None = None):
        self._store = store

    def validate(
        self,
        planner_json: dict[str, Any],
        *,
        prefilter: PrefilterResult,
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

        allowed_servers = set(prefilter.candidate_servers)
        allowed_tools = {
            sid: {self._canonical_tool_name(sid, name) for name in names}
            for sid, names in prefilter.candidate_tools.items()
        }

        for step in plan.plan_steps:
            if step.server_id not in allowed_servers:
                return PlanValidationResult(
                    plan=None,
                    error_code=ERROR_SERVER_NOT_ALLOWED,
                    error_message=f"server not allowed: {step.server_id}",
                )

            whitelist = allowed_tools.get(step.server_id, set())
            if whitelist:
                canonical = self._canonical_tool_name(step.server_id, step.tool_name)
                if canonical not in whitelist:
                    return PlanValidationResult(
                        plan=None,
                        error_code=ERROR_TOOL_NOT_ALLOWED,
                        error_message=(
                            f"tool not allowed: {step.tool_name} for server {step.server_id}"
                        ),
                    )

            if self._is_policy_blocked(intent=prefilter.intent, server_id=step.server_id):
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
