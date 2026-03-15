from __future__ import annotations

import hashlib
import os
from typing import Any

from app.logger import logger
from bff.services.runtime.mcp_routing.runtime_mcp_router import RuntimeMcpRouter
from bff.services.runtime.mcp_routing.runtime_logviz import render_ascii_box
from bff.services.runtime.mcp_routing.runtime_plan_models import (
    ERROR_INVALID_JSON,
    PlanningDecision,
    PlannerFallback,
    PlannerOutput,
    PlannerStep,
    PrefilterResult,
)
from bff.services.runtime.mcp_routing.runtime_planner import RuntimeMcpPlanner
from bff.services.runtime.mcp_routing.runtime_prefilter import RuntimeMcpPrefilter
from bff.services.runtime.mcp_routing.runtime_plan_validator import RuntimeMcpPlanValidator


class RuntimeMcpPlanningOrchestrator:
    def __init__(self, *, router: RuntimeMcpRouter, store: Any | None = None):
        self._router = router
        self._store = store
        self._prefilter = RuntimeMcpPrefilter(router=router, store=store)
        self._planner = RuntimeMcpPlanner()
        self._validator = RuntimeMcpPlanValidator(store=store)

    @staticmethod
    def _is_truthy_env(value: str | None) -> bool:
        return (value or "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _format_plan_steps(plan: PlannerOutput) -> str:
        if not plan.need_mcp or not plan.plan_steps:
            return "none"
        return " -> ".join(
            f"{step.server_id}/{step.tool_name}@{step.confidence:.2f}"
            for step in plan.plan_steps
        )

    def _should_render_box_logs(self) -> bool:
        return self._is_truthy_env(os.getenv("BFF_RUNTIME_BOX_LOG_ENABLED", "1"))

    def _log_routing_box(
        self,
        *,
        request_preview: str,
        prefilter: PrefilterResult,
        planner_enabled: bool,
        strict_json: bool,
        shadow_only: bool,
        execute_source: str,
        execute_plan: PlannerOutput,
        planner_json_ok: bool | None,
        planner_error: str | None,
        gate_ok: bool | None,
        gate_error: str | None,
    ) -> None:
        if not self._should_render_box_logs():
            return

        fallback = execute_plan.fallback
        lines = [
            f"request: {request_preview or '<empty>'}",
            f"intent: {prefilter.intent}",
            f"prefilter.need_mcp: {prefilter.need_mcp}",
            f"prefilter.candidates: {prefilter.candidate_servers or []}",
            (
                "planner.flags: "
                f"enabled={planner_enabled}, strict_json={strict_json}, shadow_only={shadow_only}"
            ),
            f"planner.json_ok: {planner_json_ok if planner_json_ok is not None else '-'}",
            f"planner.error: {planner_error or '-'}",
            f"gatekeeper.pass: {gate_ok if gate_ok is not None else '-'}",
            f"gatekeeper.error: {gate_error or '-'}",
            f"execute.source: {execute_source}",
            f"execute.need_mcp: {execute_plan.need_mcp}",
            f"execute.steps: {self._format_plan_steps(execute_plan)}",
            (
                "fallback: "
                f"mode={fallback.mode}, server={fallback.server_id or '-'}, tool={fallback.tool_name or '-'}"
            ),
        ]
        logger.info("\n" + render_ascii_box("MCP ROUTING", lines))

    @staticmethod
    def _hash_prompt(prompt_text: str) -> str:
        return hashlib.sha256((prompt_text or "").encode("utf-8")).hexdigest()

    def _record(self, payload: dict[str, Any]) -> None:
        if not self._store:
            return
        recorder = getattr(self._store, "record_mcp_routing_event", None)
        if not callable(recorder):
            return
        try:
            recorder(payload)
        except Exception as exc:
            logger.warning(f"Failed to persist planner routing event: {exc}")

    def _build_rule_plan(self, prefilter: PrefilterResult) -> PlannerOutput:
        fallback = prefilter.rule_fallback
        if not prefilter.need_mcp:
            return PlannerOutput(
                need_mcp=False,
                plan_steps=[],
                fallback=PlannerFallback(mode="no_mcp", reason="prefilter indicates no MCP required"),
            )

        server_id = fallback.server_id or (prefilter.candidate_servers[0] if prefilter.candidate_servers else "")
        if not server_id:
            return PlannerOutput(
                need_mcp=False,
                plan_steps=[],
                fallback=PlannerFallback(mode="no_mcp", reason="no enabled MCP candidates"),
            )

        tool_name = fallback.tool_name or "auto"
        return PlannerOutput(
            need_mcp=True,
            plan_steps=[
                PlannerStep(
                    goal="Route with rule fallback",
                    server_id=server_id,
                    tool_name=tool_name,
                    args_hint={},
                    confidence=1.0,
                    reason="rule prefilter fallback",
                )
            ],
            fallback=fallback,
        )

    async def decide(self, prompt: str) -> PlanningDecision:
        planner_enabled = self._is_truthy_env(os.getenv("BFF_RUNTIME_PLANNER_ENABLED", "1"))
        strict_json = self._is_truthy_env(os.getenv("BFF_RUNTIME_STRICT_JSON_VALIDATION", "1"))
        shadow_only = self._is_truthy_env(os.getenv("BFF_RUNTIME_PLANNER_SHADOW_ONLY", "0"))

        request_text = self._router.current_user_request(prompt)
        prompt_text = self._router.normalized_current_user_request(prompt)
        prompt_hash = self._hash_prompt(prompt_text)
        request_preview = self._router.request_preview(prompt)

        prefilter = self._prefilter.build(prompt)
        rule_plan = self._build_rule_plan(prefilter)
        logger.info(
            "MCP planning prefilter: "
            f"intent={prefilter.intent}, "
            f"need_mcp={prefilter.need_mcp}, "
            f"candidates={prefilter.candidate_servers}, "
            f"fallback={prefilter.rule_fallback.server_id}/{prefilter.rule_fallback.tool_name}, "
            f"request='{request_preview}'"
        )

        self._record(
            {
                "event_type": "prefilter",
                "prompt_hash": prompt_hash,
                "intent": prefilter.intent,
                "selected_server_id": prefilter.rule_fallback.server_id,
                "candidate_servers": list(prefilter.candidate_servers),
                "scores": {
                    "need_mcp": prefilter.need_mcp,
                    "candidate_tools": prefilter.candidate_tools,
                    "request_preview": request_preview,
                },
                "connected_servers": [],
            }
        )

        if not planner_enabled:
            logger.info("MCP planning: planner disabled, execute_source=rule/no_mcp")
            decision = PlanningDecision(
                execute_plan=rule_plan,
                execute_source=("no_mcp" if not rule_plan.need_mcp else "rule"),
                prefilter=prefilter,
            )
            self._log_routing_box(
                request_preview=request_preview,
                prefilter=prefilter,
                planner_enabled=planner_enabled,
                strict_json=strict_json,
                shadow_only=shadow_only,
                execute_source=decision.execute_source,
                execute_plan=decision.execute_plan,
                planner_json_ok=None,
                planner_error=None,
                gate_ok=None,
                gate_error=None,
            )
            return decision

        if not prefilter.need_mcp:
            logger.info("MCP planning: prefilter indicates no MCP needed, execute_source=no_mcp")
            decision = PlanningDecision(
                execute_plan=rule_plan,
                execute_source="no_mcp",
                prefilter=prefilter,
            )
            self._log_routing_box(
                request_preview=request_preview,
                prefilter=prefilter,
                planner_enabled=planner_enabled,
                strict_json=strict_json,
                shadow_only=shadow_only,
                execute_source=decision.execute_source,
                execute_plan=decision.execute_plan,
                planner_json_ok=None,
                planner_error=None,
                gate_ok=None,
                gate_error=None,
            )
            return decision

        planner_raw = await self._planner.create_plan(prompt, prefilter)
        if planner_raw.parsed_json is None:
            logger.warning(
                "MCP planning: planner JSON invalid, will fail-closed to rule fallback; "
                f"error_code={planner_raw.error_code}, error={planner_raw.error_message}"
            )
        else:
            logger.info(
                "MCP planning: planner JSON parsed successfully; "
                f"keys={list(planner_raw.parsed_json.keys())}"
            )
        self._record(
            {
                "event_type": "planner",
                "prompt_hash": prompt_hash,
                "intent": prefilter.intent,
                "selected_server_id": None,
                "candidate_servers": list(prefilter.candidate_servers),
                "scores": {
                    "raw_preview": planner_raw.raw_text[:800],
                    "error_code": planner_raw.error_code,
                    "request_preview": request_preview,
                },
                "connected_servers": [],
            }
        )

        if planner_raw.parsed_json is None:
            self._record(
                {
                    "event_type": "gatekeeper",
                    "prompt_hash": prompt_hash,
                    "intent": prefilter.intent,
                    "selected_server_id": prefilter.rule_fallback.server_id,
                    "candidate_servers": list(prefilter.candidate_servers),
                    "scores": {
                        "pass": False,
                        "error_code": planner_raw.error_code or ERROR_INVALID_JSON,
                        "error_message": planner_raw.error_message,
                        "request_preview": request_preview,
                    },
                    "connected_servers": [],
                }
            )
            decision = PlanningDecision(
                execute_plan=rule_plan,
                execute_source=("no_mcp" if not rule_plan.need_mcp else "rule"),
                prefilter=prefilter,
                planner_raw_json=None,
                gate_error_code=planner_raw.error_code or ERROR_INVALID_JSON,
                gate_error_message=planner_raw.error_message,
            )
            self._log_routing_box(
                request_preview=request_preview,
                prefilter=prefilter,
                planner_enabled=planner_enabled,
                strict_json=strict_json,
                shadow_only=shadow_only,
                execute_source=decision.execute_source,
                execute_plan=decision.execute_plan,
                planner_json_ok=False,
                planner_error=f"{planner_raw.error_code or ERROR_INVALID_JSON}: {planner_raw.error_message or ''}".strip(),
                gate_ok=False,
                gate_error=f"{planner_raw.error_code or ERROR_INVALID_JSON}: {planner_raw.error_message or ''}".strip(),
            )
            return decision

        validation = (
            self._validator.validate(planner_raw.parsed_json, prefilter=prefilter)
            if strict_json
            else self._validator.validate_schema_only(planner_raw.parsed_json)
        )
        if validation.plan is None:
            logger.warning(
                "MCP planning gatekeeper: rejected planner output, fail-closed to rule fallback; "
                f"error_code={validation.error_code}, error={validation.error_message}"
            )
        else:
            logger.info("MCP planning gatekeeper: validation passed")

        self._record(
            {
                "event_type": "gatekeeper",
                "prompt_hash": prompt_hash,
                "intent": prefilter.intent,
                "selected_server_id": (
                    validation.plan.plan_steps[0].server_id
                    if validation.plan and validation.plan.plan_steps
                    else prefilter.rule_fallback.server_id
                ),
                "candidate_servers": list(prefilter.candidate_servers),
                "scores": {
                    "pass": validation.plan is not None,
                    "error_code": validation.error_code,
                    "error_message": validation.error_message,
                    "request_preview": request_preview,
                },
                "connected_servers": [],
            }
        )

        if validation.plan is None:
            decision = PlanningDecision(
                execute_plan=rule_plan,
                execute_source=("no_mcp" if not rule_plan.need_mcp else "rule"),
                prefilter=prefilter,
                planner_raw_json=planner_raw.parsed_json,
                gate_error_code=validation.error_code,
                gate_error_message=validation.error_message,
            )
            self._log_routing_box(
                request_preview=request_preview,
                prefilter=prefilter,
                planner_enabled=planner_enabled,
                strict_json=strict_json,
                shadow_only=shadow_only,
                execute_source=decision.execute_source,
                execute_plan=decision.execute_plan,
                planner_json_ok=True,
                planner_error=planner_raw.error_code,
                gate_ok=False,
                gate_error=f"{validation.error_code or ''}: {validation.error_message or ''}".strip(),
            )
            return decision

        if shadow_only:
            logger.info(
                "MCP planning: shadow-only enabled, planner accepted but execute_source remains rule/no_mcp"
            )
            decision = PlanningDecision(
                execute_plan=rule_plan,
                execute_source=("no_mcp" if not rule_plan.need_mcp else "rule"),
                prefilter=prefilter,
                planner_plan=validation.plan,
                planner_raw_json=planner_raw.parsed_json,
                shadow_only=True,
            )
            self._log_routing_box(
                request_preview=request_preview,
                prefilter=prefilter,
                planner_enabled=planner_enabled,
                strict_json=strict_json,
                shadow_only=shadow_only,
                execute_source=decision.execute_source,
                execute_plan=decision.execute_plan,
                planner_json_ok=True,
                planner_error=planner_raw.error_code,
                gate_ok=True,
                gate_error=None,
            )
            return decision

        logger.info(
            "MCP planning final: execute_source=planner, "
            f"need_mcp={validation.plan.need_mcp}, "
            f"steps={[{'server': s.server_id, 'tool': s.tool_name, 'confidence': s.confidence} for s in validation.plan.plan_steps]}"
        )
        decision = PlanningDecision(
            execute_plan=validation.plan,
            execute_source=("no_mcp" if not validation.plan.need_mcp else "planner"),
            prefilter=prefilter,
            planner_plan=validation.plan,
            planner_raw_json=planner_raw.parsed_json,
        )
        self._log_routing_box(
            request_preview=request_preview,
            prefilter=prefilter,
            planner_enabled=planner_enabled,
            strict_json=strict_json,
            shadow_only=shadow_only,
            execute_source=decision.execute_source,
            execute_plan=decision.execute_plan,
            planner_json_ok=True,
            planner_error=planner_raw.error_code,
            gate_ok=True,
            gate_error=None,
        )
        return decision
