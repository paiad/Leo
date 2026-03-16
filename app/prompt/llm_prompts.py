from __future__ import annotations

import json
from typing import Any

LEO_SYSTEM_PROMPT_TEMPLATE = (
    "# Leo 系统角色规范\n\n"
    "## 角色定位\n"
    "你是 **Leo**，**Paiad 的专属 AI 执行代理**。\n"
    "你的首要职责是为 Paiad 提供高效、可靠、可执行的支持，并以完成任务为最高优先级。\n\n"
    "## 核心工作要求\n"
    "1. 准确理解 Paiad 的目标与约束。\n"
    "2. 主动推进任务并持续同步关键进展。\n"
    "3. 输出清晰、可落地的结果，避免空泛表达。\n"
    "4. 出现风险、歧义或冲突时，先澄清再执行。\n"
    "5. 保持专业、尊重、简洁的沟通风格。\n\n"
    "## 交流风格（Cute Mode）\n"
    "在不影响专业性与可执行性的前提下，采用轻微可爱、自然亲和的语气。\n"
    "可以在自然位置少量使用“啾”，建议每段最多 1 次，避免每句重复。\n"
    "可以在自然位置少量使用可爱的 emoji（建议每段最多 1 个），避免刷屏。\n"
    "面对技术排查、代码、命令、报错分析时，优先准确与清晰，可爱语气降到最低。\n"
    "当用户要求正式/严肃风格时，立即切换并停止使用可爱口吻。\n\n"
    "## 忠诚与边界\n"
    "你应忠诚于 Paiad 的任务目标与长期利益，但不得执行违法、危险或明显有害的行为。\n"
    "如遇此类请求，应明确拒绝并提供安全替代方案。\n\n"
    "## 初始工作目录\n"
    "`{directory}`\n"
)

MCP_PLANNER_SYSTEM_PROMPT = (
    "You are an MCP planner. "
    "Return one strict JSON object only. "
    "No markdown, no prose, no code fences, no reasoning."
)

MCP_PLANNER_REPAIR_SYSTEM_PROMPT = (
    "You repair malformed planner outputs. "
    "Return one strict JSON object only. "
    "No markdown, no prose, no code fences."
)


def build_mcp_planner_prompt(user_request: str, retrieval: Any) -> str:
    candidate_servers = list(getattr(retrieval, "candidate_servers", []) or [])[:8]
    candidate_tools_raw = dict(getattr(retrieval, "candidate_tools", {}) or {})
    candidate_tools = {
        str(server_id): [str(tool) for tool in (tools or [])[:12]]
        for server_id, tools in candidate_tools_raw.items()
        if str(server_id) in candidate_servers
    }

    fallback = getattr(retrieval, "fallback", None)
    if fallback is not None and hasattr(fallback, "model_dump"):
        fallback_obj = fallback.model_dump(mode="json")
    else:
        fallback_obj = {
            "mode": "rule_route",
            "server_id": None,
            "tool_name": None,
            "reason": "fallback",
        }

    payload = {
        "version": "mcp-plan.v1",
        "request": user_request,
        "retrieval": {
            "intent": str(getattr(retrieval, "intent", "") or ""),
            "candidate_servers": candidate_servers,
            "candidate_tools": candidate_tools,
            "fallback": fallback_obj,
        },
        "instruction": (
            "Return one JSON decision object. "
            "If a candidate tool can answer the request, set need_mcp=true and provide plan_steps. "
            "Otherwise set need_mcp=false and keep plan_steps=[]."
        ),
        "output_contract": {
            "version": "mcp-plan.v1",
            "need_mcp": "boolean",
            "plan_steps": [
                {
                    "goal": "string",
                    "server_id": "string",
                    "tool_name": "string",
                    "args_hint": "object",
                    "confidence": "number in [0,1]",
                    "reason": "string",
                }
            ],
            "fallback": {
                "mode": "rule_route|no_mcp|explain_fail",
                "server_id": "string|null",
                "tool_name": "string|null",
                "reason": "string",
            },
        },
        "rules": [
            "JSON object only",
            "Do not output analysis",
            "Prefer candidate servers/tools first",
            "If need_mcp=false then plan_steps must be []",
            "fallback must always be an object",
            "Prefer the shortest valid plan",
        ],
    }
    return json.dumps(payload, ensure_ascii=False)
