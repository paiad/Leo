from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

PLANNER_VERSION = "mcp-plan.v1"

ERROR_INVALID_JSON = "E1001_INVALID_JSON"
ERROR_SCHEMA_VIOLATION = "E1002_SCHEMA_VIOLATION"
ERROR_SERVER_NOT_ALLOWED = "E1003_SERVER_NOT_ALLOWED"
ERROR_TOOL_NOT_ALLOWED = "E1004_TOOL_NOT_ALLOWED"
ERROR_POLICY_BLOCKED = "E1005_POLICY_BLOCKED"
ERROR_EMPTY_PLAN_FOR_MCP = "E1006_EMPTY_PLAN_WHEN_NEED_MCP"
ERROR_CONFIDENCE_RANGE = "E1007_CONFIDENCE_OUT_OF_RANGE"


class PlannerStep(BaseModel):
    goal: str
    server_id: str
    tool_name: str
    args_hint: dict[str, Any] = Field(default_factory=dict)
    confidence: float
    reason: str

    @model_validator(mode="after")
    def _normalize(self) -> "PlannerStep":
        self.server_id = (self.server_id or "").strip().lower()
        self.tool_name = (self.tool_name or "").strip().lower()
        return self


class PlannerFallback(BaseModel):
    mode: Literal["rule_route", "no_mcp", "explain_fail"] = "rule_route"
    server_id: str | None = None
    tool_name: str | None = None
    reason: str

    @model_validator(mode="after")
    def _normalize(self) -> "PlannerFallback":
        self.server_id = (
            (self.server_id or "").strip().lower() if self.server_id is not None else None
        )
        self.tool_name = (
            (self.tool_name or "").strip().lower() if self.tool_name is not None else None
        )
        return self


class PlannerOutput(BaseModel):
    version: Literal[PLANNER_VERSION] = PLANNER_VERSION
    need_mcp: bool
    plan_steps: list[PlannerStep] = Field(default_factory=list)
    fallback: PlannerFallback

    @model_validator(mode="before")
    @classmethod
    def _repair_fallback(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        need_mcp = bool(normalized.get("need_mcp"))
        fallback = normalized.get("fallback")

        mode_default = "rule_route" if need_mcp else "no_mcp"
        reason_default = (
            "auto_fallback_for_need_mcp"
            if need_mcp
            else "auto_fallback_for_no_mcp"
        )

        if not isinstance(fallback, dict):
            fallback = {"mode": mode_default, "reason": reason_default}
        else:
            fallback = dict(fallback)
            mode = str(fallback.get("mode") or "").strip().lower()
            if mode not in {"rule_route", "no_mcp", "explain_fail"}:
                fallback["mode"] = mode_default
            reason = str(fallback.get("reason") or "").strip()
            if not reason:
                fallback["reason"] = reason_default

        if need_mcp:
            first_step = normalized.get("plan_steps")
            if isinstance(first_step, list) and first_step and isinstance(first_step[0], dict):
                first_server = str(first_step[0].get("server_id") or "").strip().lower()
                first_tool = str(first_step[0].get("tool_name") or "").strip().lower()
                if first_server and not str(fallback.get("server_id") or "").strip():
                    fallback["server_id"] = first_server
                if first_tool and not str(fallback.get("tool_name") or "").strip():
                    fallback["tool_name"] = first_tool

        normalized["fallback"] = fallback
        return normalized

    @model_validator(mode="after")
    def _validate_steps(self) -> "PlannerOutput":
        if self.need_mcp and not self.plan_steps:
            raise ValueError(ERROR_EMPTY_PLAN_FOR_MCP)
        if not self.need_mcp and self.plan_steps:
            raise ValueError("plan_steps must be empty when need_mcp=false")
        for step in self.plan_steps:
            if step.confidence < 0 or step.confidence > 1:
                raise ValueError(ERROR_CONFIDENCE_RANGE)
        return self


class RetrievalResult(BaseModel):
    intent: str
    candidate_servers: list[str] = Field(default_factory=list)
    server_scores: dict[str, float] = Field(default_factory=dict)
    score_mode: str = "computed"
    score_note: str = ""
    candidate_tools: dict[str, list[str]] = Field(default_factory=dict)
    candidate_tool_profiles: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict)
    fallback: PlannerFallback


@dataclass
class PlannerRawResult:
    raw_text: str
    parsed_json: dict[str, Any] | None
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class PlanValidationResult:
    plan: PlannerOutput | None
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class PlanningDecision:
    execute_plan: PlannerOutput
    execute_source: Literal["planner", "rule", "no_mcp"]
    retrieval: RetrievalResult
    planner_plan: PlannerOutput | None = None
    planner_raw_json: dict[str, Any] | None = None
    gate_error_code: str | None = None
    gate_error_message: str | None = None
    shadow_only: bool = False
    timing_ms: dict[str, int] = field(default_factory=dict)
