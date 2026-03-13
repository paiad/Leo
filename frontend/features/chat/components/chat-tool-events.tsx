import { ChevronRight, Wrench } from "lucide-react";
import type { ChatDecisionEvent, ChatToolEvent } from "@/features/chat/types/chat";

type ChatToolEventsProps = {
  events: ChatToolEvent[];
  decisionEvents?: ChatDecisionEvent[];
  isLoading?: boolean;
  loadingElapsedMs?: number;
  mcpServerNameByToolId?: Record<string, string>;
  mcpServerNameByToolName?: Record<string, string>;
};

function getStatusLabel(status: ChatToolEvent["status"]): string {
  if (status === "started") {
    return "执行中";
  }
  if (status === "success") {
    return "成功";
  }
  if (status === "failed") {
    return "失败";
  }
  return "已忽略";
}

export function ChatToolEvents({
  events,
  decisionEvents = [],
  isLoading = false,
  loadingElapsedMs = 0,
  mcpServerNameByToolId = {},
  mcpServerNameByToolName = {},
}: ChatToolEventsProps) {
  if (!isLoading && events.length === 0 && decisionEvents.length === 0) {
    return null;
  }

  const mergedEvents = events.reduce<ChatToolEvent[]>((acc, event) => {
    const last = acc[acc.length - 1];
    const canMerge =
      last &&
      (last.toolId ?? "") === (event.toolId ?? "") &&
      last.toolName === event.toolName &&
      (last.inputPreview ?? "") === (event.inputPreview ?? "") &&
      last.status === "started" &&
      event.status !== "started";

    if (canMerge) {
      acc[acc.length - 1] = {
        ...last,
        ...event,
        status: event.status,
      };
      return acc;
    }

    acc.push(event);
    return acc;
  }, []);

  const totalLatencyMs = mergedEvents.reduce((sum, event) => sum + (event.latencyMs ?? 0), 0);
  const summaryLatencyMs = totalLatencyMs > 0 ? totalLatencyMs : loadingElapsedMs;
  const resolveMcpLabel = (event: ChatToolEvent): string =>
    (event.toolId && mcpServerNameByToolId[event.toolId]) ||
    mcpServerNameByToolName[event.toolName] ||
    event.toolId ||
    "unknown";
  const firstEvent = mergedEvents[0];
  const summaryToolLabel =
    firstEvent && mergedEvents.length === 1
      ? `${resolveMcpLabel(firstEvent)} | ${firstEvent.toolName}`
      : null;

  return (
    <div className="mb-2 space-y-2">
      {decisionEvents.length > 0 ? (
        <div className="rounded-xl border border-indigo-200 bg-indigo-50/70 px-3 py-2 text-xs text-indigo-900">
          {decisionEvents.map((event, index) => (
            <p key={`${event.action}-${index}`}>
              决策：
              {event.action === "invoke"
                ? "调用 MCP"
                : event.action === "clarify"
                  ? "先确认再调用"
                  : "跳过 MCP"}{" "}
              · {event.reason}
              {typeof event.confidence === "number"
                ? `（置信度 ${(event.confidence * 100).toFixed(0)}%）`
                : ""}
              {event.action === "clarify" && event.prompt ? ` · ${event.prompt}` : ""}
            </p>
          ))}
        </div>
      ) : null}

      {isLoading ? (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white/85 px-3 py-2">
          <div className="flex items-center justify-between gap-2 text-sm text-slate-800">
            <span className="inline-flex items-center gap-2">
              <Wrench className="h-4 w-4 text-slate-600" />
              <span className="font-semibold">
                正在调用 MCP 工具{summaryToolLabel ? `（${summaryToolLabel}）` : ""}（用时{" "}
                {(loadingElapsedMs / 1000).toFixed(1)} 秒）
              </span>
            </span>
            <span className="inline-flex items-center gap-1">
              <span
                className="h-1.5 w-1.5 animate-pulse rounded-full bg-slate-400"
                style={{ animationDelay: "0ms" }}
              />
              <span
                className="h-1.5 w-1.5 animate-pulse rounded-full bg-slate-400"
                style={{ animationDelay: "140ms" }}
              />
              <span
                className="h-1.5 w-1.5 animate-pulse rounded-full bg-slate-400"
                style={{ animationDelay: "280ms" }}
              />
            </span>
          </div>
        </div>
      ) : null}

      {!isLoading && mergedEvents.length > 0 ? (
        <details className="group overflow-hidden rounded-xl border border-slate-200 bg-white/80">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2 text-sm text-slate-800">
            <span className="inline-flex items-center gap-2">
              <Wrench className="h-4 w-4 text-slate-600" />
              <span className="font-semibold">
                已调用 MCP 工具
                {summaryToolLabel ? `（${summaryToolLabel}）` : ""}
                （{mergedEvents.length} 次，耗时 {summaryLatencyMs} ms）
              </span>
            </span>
            <ChevronRight className="h-4 w-4 text-slate-500 transition-transform duration-200 group-open:rotate-90" />
          </summary>
          <div className="border-t border-slate-200 bg-slate-50/70 px-3 py-3">
            <div className="space-y-2">
              {mergedEvents.map((event, index) => {
                const output =
                  event.outputJson !== undefined
                    ? JSON.stringify(event.outputJson, null, 2)
                    : event.outputPreview;

                return (
                  <div key={`${event.toolId ?? "unknown"}-${event.toolName}-${event.status}-${index}`} className="rounded-lg border border-slate-200 bg-white p-3">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-600">
                      <span className="font-semibold text-slate-800">
                        MCP: {resolveMcpLabel(event)} | Tool: {event.toolName}
                      </span>
                      <span>状态: {getStatusLabel(event.status)}</span>
                      {typeof event.latencyMs === "number" ? <span>耗时: {event.latencyMs} ms</span> : null}
                    </div>
                    {event.inputPreview ? (
                      <pre className="mt-2 max-h-32 overflow-auto rounded-md bg-slate-50 p-2 text-xs text-slate-700">
                        {event.inputPreview}
                      </pre>
                    ) : null}
                    {output ? (
                      <div className="mt-2 overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
                        <div className="flex items-center gap-1.5 border-b border-slate-200 bg-slate-100 px-3 py-2">
                          <span className="h-2.5 w-2.5 rounded-full bg-red-400" />
                          <span className="h-2.5 w-2.5 rounded-full bg-amber-400" />
                          <span className="h-2.5 w-2.5 rounded-full bg-emerald-400" />
                        </div>
                        <pre className="max-h-56 overflow-auto bg-transparent p-3 text-xs text-slate-700">
                          {output}
                        </pre>
                      </div>
                    ) : null}
                    {event.errorMessage ? (
                      <p className="mt-2 text-xs text-red-600">{event.errorMessage}</p>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </div>
        </details>
      ) : null}
    </div>
  );
}
