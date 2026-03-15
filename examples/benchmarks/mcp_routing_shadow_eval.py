from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from bff.domain.models import McpServerRecord
from bff.repositories.store import InMemoryStore, PostgresStore, create_store
from bff.services.runtime.mcp_routing.runtime_mcp_router import RuntimeMcpRouter
from bff.services.runtime.mcp_routing.runtime_planning import RuntimeMcpPlanningOrchestrator
from bff.services.runtime.mcp_routing.runtime_prefilter import RuntimeMcpPrefilter
from bff.services.tooling.tooling_service import ToolingService

DEFAULT_EVAL_FILE = Path("docs/plans/templates/mcp-routing-eval-samples.csv")
DEFAULT_BENCHMARK_SERVERS = ["playwright", "github", "rag", "trendradar", "exa"]


@dataclass
class EvalSample:
    sample_id: str
    user_request: str
    expected_servers_raw: str
    expected_tools_raw: str
    multi_step: bool


@dataclass
class EvalOutcome:
    sample: EvalSample
    predicted_servers: list[str]
    execute_source: str
    gate_error_code: str | None
    first_step_match: bool
    sequence_match: bool


@dataclass
class EvalContext:
    store: InMemoryStore | PostgresStore
    active_servers: list[str]
    server_source: str


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_samples(path: Path) -> list[EvalSample]:
    rows: list[EvalSample] = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for item in reader:
            rows.append(
                EvalSample(
                    sample_id=str(item.get("id") or ""),
                    user_request=str(item.get("user_request") or "").strip(),
                    expected_servers_raw=str(item.get("expected_servers") or "none").strip(),
                    expected_tools_raw=str(item.get("expected_tools") or "none").strip(),
                    multi_step=_truthy(str(item.get("multi_step") or "false")),
                )
            )
    return rows


def _expected_groups(raw: str) -> list[set[str]]:
    normalized = (raw or "none").strip().lower()
    if not normalized:
        return [set(["none"])]
    ordered = normalized.split(">")
    groups: list[set[str]] = []
    for segment in ordered:
        options = {part.strip() for part in segment.split("|") if part.strip()}
        groups.append(options or {"none"})
    return groups


def _first_step_match(predicted: Sequence[str], expected: list[set[str]]) -> bool:
    first_pred = predicted[0] if predicted else "none"
    first_group = expected[0] if expected else {"none"}
    return first_pred in first_group


def _sequence_match(predicted: Sequence[str], expected: list[set[str]]) -> bool:
    if not expected:
        return not predicted
    if len(predicted) < len(expected):
        return False
    for idx, group in enumerate(expected):
        if predicted[idx] not in group:
            return False
    return True


def _bootstrap_benchmark_store() -> InMemoryStore:
    store = InMemoryStore(enable_persistence=False)
    for server_id in DEFAULT_BENCHMARK_SERVERS:
        store.mcp_servers[server_id] = McpServerRecord(
            serverId=server_id,
            name=server_id,
            type="stdio",
            command="dummy",
            enabled=True,
        )
    return store


def _bootstrap_live_store() -> InMemoryStore | PostgresStore:
    store = create_store()
    # Apply the same MCP bootstrap logic as BFF runtime startup.
    ToolingService(store=store)
    return store


def _enabled_server_ids(store: InMemoryStore | PostgresStore) -> list[str]:
    enabled: list[str] = []
    for server_id, server in (store.mcp_servers or {}).items():
        if not getattr(server, "enabled", False):
            continue
        sid = str(server_id or "").strip().lower()
        if sid:
            enabled.append(sid)
    return sorted(set(enabled))


def _build_eval_context(server_source: str) -> EvalContext:
    normalized = (server_source or "live").strip().lower()
    if normalized == "benchmark":
        store = _bootstrap_benchmark_store()
        return EvalContext(
            store=store,
            active_servers=DEFAULT_BENCHMARK_SERVERS,
            server_source="benchmark",
        )

    store = _bootstrap_live_store()
    active = _enabled_server_ids(store)
    if not active:
        return EvalContext(
            store=store,
            active_servers=[],
            server_source="live",
        )
    return EvalContext(
        store=store,
        active_servers=active,
        server_source="live",
    )


async def _evaluate_prefilter(samples: list[EvalSample], context: EvalContext) -> list[EvalOutcome]:
    store = context.store
    router = RuntimeMcpRouter(store=store)
    prefilter = RuntimeMcpPrefilter(router=router, store=store)

    outcomes: list[EvalOutcome] = []
    for sample in samples:
        prompt = f"[Current User Request]\n{sample.user_request}"
        result = prefilter.build(prompt)
        predicted = result.candidate_servers if result.need_mcp else ["none"]
        expected = _expected_groups(sample.expected_servers_raw)
        outcomes.append(
            EvalOutcome(
                sample=sample,
                predicted_servers=list(predicted),
                execute_source="prefilter",
                gate_error_code=None,
                first_step_match=_first_step_match(predicted, expected),
                sequence_match=_sequence_match(predicted, expected),
            )
        )
    return outcomes


async def _evaluate_planner_shadow(samples: list[EvalSample], context: EvalContext) -> list[EvalOutcome]:
    store = context.store
    router = RuntimeMcpRouter(store=store)
    orchestrator = RuntimeMcpPlanningOrchestrator(router=router, store=store)

    old_env = {
        "BFF_RUNTIME_PLANNER_ENABLED": os.getenv("BFF_RUNTIME_PLANNER_ENABLED"),
        "BFF_RUNTIME_PLANNER_SHADOW_ONLY": os.getenv("BFF_RUNTIME_PLANNER_SHADOW_ONLY"),
        "BFF_RUNTIME_STRICT_JSON_VALIDATION": os.getenv("BFF_RUNTIME_STRICT_JSON_VALIDATION"),
        "BFF_RUNTIME_MULTI_STEP_EXECUTION": os.getenv("BFF_RUNTIME_MULTI_STEP_EXECUTION"),
    }

    os.environ["BFF_RUNTIME_PLANNER_ENABLED"] = "1"
    os.environ["BFF_RUNTIME_PLANNER_SHADOW_ONLY"] = "1"
    os.environ["BFF_RUNTIME_STRICT_JSON_VALIDATION"] = "1"
    os.environ["BFF_RUNTIME_MULTI_STEP_EXECUTION"] = "1"

    outcomes: list[EvalOutcome] = []
    try:
        for sample in samples:
            prompt = f"[Current User Request]\n{sample.user_request}"
            decision = await orchestrator.decide(prompt)
            plan = decision.planner_plan or decision.execute_plan
            if plan.need_mcp:
                predicted = [step.server_id for step in plan.plan_steps]
            else:
                predicted = ["none"]

            expected = _expected_groups(sample.expected_servers_raw)
            outcomes.append(
                EvalOutcome(
                    sample=sample,
                    predicted_servers=predicted,
                    execute_source=decision.execute_source,
                    gate_error_code=decision.gate_error_code,
                    first_step_match=_first_step_match(predicted, expected),
                    sequence_match=_sequence_match(predicted, expected),
                )
            )
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    return outcomes


def _summarize(
    outcomes: list[EvalOutcome],
    *,
    mode: str,
    server_source: str,
    active_servers: list[str],
) -> dict[str, object]:
    total = len(outcomes)
    first_hit = sum(1 for item in outcomes if item.first_step_match)
    seq_hit = sum(1 for item in outcomes if item.sequence_match)
    avg_steps = round(sum(len(item.predicted_servers) for item in outcomes) / total, 2) if total else 0.0
    fallback_count = sum(1 for item in outcomes if item.execute_source == "rule" or item.gate_error_code)

    return {
        "mode": mode,
        "server_source": server_source,
        "active_servers": active_servers,
        "total": total,
        "first_step_accuracy": round((first_hit / total) * 100, 2) if total else 0.0,
        "sequence_accuracy": round((seq_hit / total) * 100, 2) if total else 0.0,
        "avg_predicted_steps": avg_steps,
        "fallback_rate": round((fallback_count / total) * 100, 2) if total else 0.0,
    }


def _print_report(outcomes: list[EvalOutcome], summary: dict[str, object], *, show_details: bool) -> None:
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not show_details:
        return

    print("\nDetails:")
    for item in outcomes:
        print(
            json.dumps(
                {
                    "id": item.sample.sample_id,
                    "request": item.sample.user_request,
                    "expected_servers": item.sample.expected_servers_raw,
                    "predicted_servers": item.predicted_servers,
                    "execute_source": item.execute_source,
                    "gate_error_code": item.gate_error_code,
                    "first_step_match": item.first_step_match,
                    "sequence_match": item.sequence_match,
                },
                ensure_ascii=False,
            )
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description="MCP routing shadow evaluator")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_EVAL_FILE,
        help="Path to CSV evaluation dataset",
    )
    parser.add_argument(
        "--mode",
        choices=["prefilter", "planner-shadow"],
        default="prefilter",
        help="Evaluation mode",
    )
    parser.add_argument(
        "--server-source",
        choices=["live", "benchmark"],
        default="live",
        help="Server list source: live (current MCP state) or benchmark (fixed server set).",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Print per-sample details",
    )
    args = parser.parse_args()

    if not args.dataset.exists():
        raise FileNotFoundError(f"Dataset not found: {args.dataset}")

    samples = _parse_samples(args.dataset)
    context = _build_eval_context(args.server_source)
    if args.mode == "planner-shadow":
        outcomes = await _evaluate_planner_shadow(samples, context)
    else:
        outcomes = await _evaluate_prefilter(samples, context)

    summary = _summarize(
        outcomes,
        mode=args.mode,
        server_source=context.server_source,
        active_servers=context.active_servers,
    )
    _print_report(outcomes, summary, show_details=args.details)


if __name__ == "__main__":
    asyncio.run(main())
