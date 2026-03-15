from __future__ import annotations

from dataclasses import dataclass
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


class PrefilterResult(BaseModel):
    intent: str
    need_mcp: bool
    candidate_servers: list[str] = Field(default_factory=list)
    candidate_tools: dict[str, list[str]] = Field(default_factory=dict)
    rule_fallback: PlannerFallback


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
    prefilter: PrefilterResult
    planner_plan: PlannerOutput | None = None
    planner_raw_json: dict[str, Any] | None = None
    gate_error_code: str | None = None
    gate_error_message: str | None = None
    shadow_only: bool = False
