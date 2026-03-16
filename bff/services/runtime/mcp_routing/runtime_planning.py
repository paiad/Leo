from __future__ import annotations

import hashlib
import os
import time
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
    RetrievalResult,
)
from bff.services.runtime.mcp_routing.runtime_planner import RuntimeMcpPlanner
from bff.services.runtime.mcp_routing.runtime_plan_validator import RuntimeMcpPlanValidator
from bff.services.runtime.mcp_routing.runtime_tool_retriever import RuntimeMcpToolRetriever


class RuntimeMcpPlanningOrchestrator:
    def __init__(self, *, router: RuntimeMcpRouter, store: Any | None = None):
        self._router = router
        self._store = store
        self._retriever = RuntimeMcpToolRetriever(router=router, store=store)
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

    def _should_render_timing_logs(self) -> bool:
        return self._is_truthy_env(os.getenv("BFF_RUNTIME_TIMING_LOG_ENABLED", "1"))

    def _log_timing_box(self, request_preview: str, timing_ms: dict[str, int]) -> None:
        if not self._should_render_timing_logs():
            return

        lines = [
            f"request: {request_preview or '<empty>'}",
            f"planning.total_ms: {timing_ms.get('planning_total_ms', 0)}",
            f"planning.retrieval_ms: {timing_ms.get('planning_retrieval_ms', 0)}",
            f"planning.planner_ms: {timing_ms.get('planning_planner_ms', 0)}",
            f"planning.gatekeeper_ms: {timing_ms.get('planning_gatekeeper_ms', 0)}",
        ]
        logger.info("\n" + render_ascii_box("MCP TIMING (PLAN)", lines))

    def _log_routing_box(
        self,
        *,
        request_preview: str,
        retrieval: RetrievalResult,
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
            f"intent: {retrieval.intent}",
            f"retrieval.candidates: {retrieval.candidate_servers or []}",
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

    def _build_rule_plan(self, retrieval: RetrievalResult) -> PlannerOutput:
        fallback = retrieval.fallback
        server_id = fallback.server_id or (retrieval.candidate_servers[0] if retrieval.candidate_servers else "")
        if not server_id:
            return PlannerOutput(
                need_mcp=False,
                plan_steps=[],
                fallback=PlannerFallback(mode="no_mcp", reason="no retrieval candidates"),
            )

        tool_name = fallback.tool_name or "auto"
        return PlannerOutput(
            need_mcp=True,
            plan_steps=[
                PlannerStep(
                    goal="Route with retrieval fallback",
                    server_id=server_id,
                    tool_name=tool_name,
                    args_hint={},
                    confidence=1.0,
                    reason="retrieval fallback",
                )
            ],
            fallback=fallback,
        )

    async def decide(self, prompt: str, *, session_id: str | None = None) -> PlanningDecision:
        started = time.perf_counter()
        planner_enabled = self._is_truthy_env(os.getenv("BFF_RUNTIME_PLANNER_ENABLED", "1"))
        strict_json = self._is_truthy_env(os.getenv("BFF_RUNTIME_STRICT_JSON_VALIDATION", "1"))
        shadow_only = self._is_truthy_env(os.getenv("BFF_RUNTIME_PLANNER_SHADOW_ONLY", "0"))

        request_text = self._router.current_user_request(prompt)
        prompt_text = self._router.normalized_current_user_request(prompt)
        prompt_hash = self._hash_prompt(prompt_text)
        request_preview = self._router.request_preview(prompt)

        retrieval_started = time.perf_counter()
        retrieval_output = self._retriever.retrieve(prompt)
        retrieval_ms = int((time.perf_counter() - retrieval_started) * 1000)
        retrieval = RetrievalResult(
            intent=retrieval_output.intent,
            candidate_servers=list(retrieval_output.candidate_servers),
            candidate_tools=dict(retrieval_output.candidate_tools),
            candidate_tool_profiles=dict(retrieval_output.candidate_tool_profiles),
            fallback=(
                PlannerFallback(
                    mode=("rule_route" if retrieval_output.fallback_server_id else "no_mcp"),
                    server_id=retrieval_output.fallback_server_id,
                    tool_name=retrieval_output.fallback_tool_name,
                    reason=("retrieval fallback" if retrieval_output.fallback_server_id else "no retrieval candidates"),
                )
            ),
        )
        rule_plan = self._build_rule_plan(retrieval)
        logger.info(
            "MCP planning retrieval: "
            f"intent={retrieval.intent}, "
            f"candidates={retrieval.candidate_servers}, "
            f"fallback={retrieval.fallback.server_id}/{retrieval.fallback.tool_name}, "
            f"request='{request_preview}'"
        )

        self._record(
            {
                "event_type": "retrieval",
                "session_id": session_id,
                "prompt_hash": prompt_hash,
                "intent": retrieval.intent,
                "selected_server_id": retrieval.fallback.server_id,
                "candidate_servers": list(retrieval.candidate_servers),
                "scores": {
                    "candidate_tools": retrieval.candidate_tools,
                    "candidate_tool_profiles": retrieval.candidate_tool_profiles,
                    "request_preview": request_preview,
                },
                "connected_servers": [],
            }
        )

        planner_ms = 0
        gatekeeper_ms = 0

        if not planner_enabled:
            logger.info("MCP planning: planner disabled, execute_source=rule/no_mcp")
            timing_ms = {
                "planning_total_ms": int((time.perf_counter() - started) * 1000),
                "planning_retrieval_ms": retrieval_ms,
                "planning_planner_ms": planner_ms,
                "planning_gatekeeper_ms": gatekeeper_ms,
            }
            decision = PlanningDecision(
                execute_plan=rule_plan,
                execute_source=("no_mcp" if not rule_plan.need_mcp else "rule"),
                retrieval=retrieval,
                timing_ms=timing_ms,
            )
            self._log_routing_box(
                request_preview=request_preview,
                retrieval=retrieval,
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
            self._log_timing_box(request_preview, timing_ms)
            return decision

        if not retrieval.candidate_servers:
            logger.info("MCP planning: retrieval has no candidates, execute_source=no_mcp")
            timing_ms = {
                "planning_total_ms": int((time.perf_counter() - started) * 1000),
                "planning_retrieval_ms": retrieval_ms,
                "planning_planner_ms": planner_ms,
                "planning_gatekeeper_ms": gatekeeper_ms,
            }
            decision = PlanningDecision(
                execute_plan=rule_plan,
                execute_source="no_mcp",
                retrieval=retrieval,
                timing_ms=timing_ms,
            )
            self._log_routing_box(
                request_preview=request_preview,
                retrieval=retrieval,
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
            self._log_timing_box(request_preview, timing_ms)
            return decision

        planner_started = time.perf_counter()
        planner_raw = await self._planner.create_plan(prompt, retrieval)
        planner_ms = int((time.perf_counter() - planner_started) * 1000)
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
                "session_id": session_id,
                "prompt_hash": prompt_hash,
                "intent": retrieval.intent,
                "selected_server_id": None,
                "candidate_servers": list(retrieval.candidate_servers),
                "scores": {
                    "raw_preview": planner_raw.raw_text[:800],
                    "error_code": planner_raw.error_code,
                    "request_preview": request_preview,
                },
                "connected_servers": [],
            }
        )

        if planner_raw.parsed_json is None:
            timing_ms = {
                "planning_total_ms": int((time.perf_counter() - started) * 1000),
                "planning_retrieval_ms": retrieval_ms,
                "planning_planner_ms": planner_ms,
                "planning_gatekeeper_ms": gatekeeper_ms,
            }
            self._record(
                {
                    "event_type": "gatekeeper",
                    "session_id": session_id,
                    "prompt_hash": prompt_hash,
                    "intent": retrieval.intent,
                    "selected_server_id": retrieval.fallback.server_id,
                    "candidate_servers": list(retrieval.candidate_servers),
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
                retrieval=retrieval,
                planner_raw_json=None,
                gate_error_code=planner_raw.error_code or ERROR_INVALID_JSON,
                gate_error_message=planner_raw.error_message,
                timing_ms=timing_ms,
            )
            self._log_routing_box(
                request_preview=request_preview,
                retrieval=retrieval,
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
            self._log_timing_box(request_preview, timing_ms)
            return decision

        gatekeeper_started = time.perf_counter()
        validation = (
            self._validator.validate(planner_raw.parsed_json, retrieval=retrieval)
            if strict_json
            else self._validator.validate_schema_only(planner_raw.parsed_json)
        )
        gatekeeper_ms = int((time.perf_counter() - gatekeeper_started) * 1000)
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
                "session_id": session_id,
                "prompt_hash": prompt_hash,
                "intent": retrieval.intent,
                "selected_server_id": (
                    validation.plan.plan_steps[0].server_id
                    if validation.plan and validation.plan.plan_steps
                    else retrieval.fallback.server_id
                ),
                "candidate_servers": list(retrieval.candidate_servers),
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
            timing_ms = {
                "planning_total_ms": int((time.perf_counter() - started) * 1000),
                "planning_retrieval_ms": retrieval_ms,
                "planning_planner_ms": planner_ms,
                "planning_gatekeeper_ms": gatekeeper_ms,
            }
            decision = PlanningDecision(
                execute_plan=rule_plan,
                execute_source=("no_mcp" if not rule_plan.need_mcp else "rule"),
                retrieval=retrieval,
                planner_raw_json=planner_raw.parsed_json,
                gate_error_code=validation.error_code,
                gate_error_message=validation.error_message,
                timing_ms=timing_ms,
            )
            self._log_routing_box(
                request_preview=request_preview,
                retrieval=retrieval,
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
            self._log_timing_box(request_preview, timing_ms)
            return decision

        if shadow_only:
            logger.info(
                "MCP planning: shadow-only enabled, planner accepted but execute_source remains rule/no_mcp"
            )
            timing_ms = {
                "planning_total_ms": int((time.perf_counter() - started) * 1000),
                "planning_retrieval_ms": retrieval_ms,
                "planning_planner_ms": planner_ms,
                "planning_gatekeeper_ms": gatekeeper_ms,
            }
            decision = PlanningDecision(
                execute_plan=rule_plan,
                execute_source=("no_mcp" if not rule_plan.need_mcp else "rule"),
                retrieval=retrieval,
                planner_plan=validation.plan,
                planner_raw_json=planner_raw.parsed_json,
                shadow_only=True,
                timing_ms=timing_ms,
            )
            self._log_routing_box(
                request_preview=request_preview,
                retrieval=retrieval,
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
            self._log_timing_box(request_preview, timing_ms)
            return decision

        logger.info(
            "MCP planning final: execute_source=planner, "
            f"need_mcp={validation.plan.need_mcp}, "
            f"steps={[{'server': s.server_id, 'tool': s.tool_name, 'confidence': s.confidence} for s in validation.plan.plan_steps]}"
        )
        timing_ms = {
            "planning_total_ms": int((time.perf_counter() - started) * 1000),
            "planning_retrieval_ms": retrieval_ms,
            "planning_planner_ms": planner_ms,
            "planning_gatekeeper_ms": gatekeeper_ms,
        }
        decision = PlanningDecision(
            execute_plan=validation.plan,
            execute_source=("no_mcp" if not validation.plan.need_mcp else "planner"),
            retrieval=retrieval,
            planner_plan=validation.plan,
            planner_raw_json=planner_raw.parsed_json,
            timing_ms=timing_ms,
        )
        self._log_routing_box(
            request_preview=request_preview,
            retrieval=retrieval,
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
        self._log_timing_box(request_preview, timing_ms)
        return decision
