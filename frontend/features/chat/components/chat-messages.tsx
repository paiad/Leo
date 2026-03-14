import { Bot, Check, Copy, Shield, Trash2, UserRound } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ChatToolEvents } from "@/features/chat/components/chat-tool-events";
import { MarkdownMessage } from "@/features/chat/components/markdown-message";
import type { ChatMessage } from "@/features/chat/types/chat";
import type { ChatDecisionEvent } from "@/features/chat/types/chat";
import type { ChatToolEvent } from "@/features/chat/types/chat";

type ChatMessagesProps = {
  messages: ChatMessage[];
  sessionSource?: "browser" | "lark";
  isLoading?: boolean;
  loadingTime?: string | null;
  loadingModel?: string | null;
  toolEvents?: ChatToolEvent[];
  decisionEvents?: ChatDecisionEvent[];
  isToolCallLoading?: boolean;
  toolCallElapsedMs?: number;
  toolCallTime?: string | null;
  pendingAssistantMessageId?: string | null;
  mcpServerNameByToolId?: Record<string, string>;
  mcpServerNameByToolName?: Record<string, string>;
  onDeleteMessage?: (message: ChatMessage) => void;
};

const roleBadge = {
  system: {
    label: "System",
    icon: Shield,
    bubbleClass: "bg-amber-50 border-amber-200 text-amber-900",
  },
  user: {
    label: "You",
    icon: UserRound,
    bubbleClass: "bg-slate-700 border-slate-700 text-white",
  },
  assistant: {
    label: "Leo",
    icon: Bot,
    bubbleClass: "bg-white border-slate-200 text-slate-800",
  },
} as const;

function formatShanghaiTimeToMinute(value: string | null | undefined): string {
  const raw = value?.trim();
  if (!raw) {
    return "";
  }
  if (raw === "System") {
    return raw;
  }
  if (/^\d{2}:\d{2}$/.test(raw)) {
    return raw;
  }
  if (/^\d{2}:\d{2}:\d{2}$/.test(raw)) {
    return raw.slice(0, 5);
  }
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) {
    return raw;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function timelinePhaseLabel(event: NonNullable<ChatMessage["timelineEvents"]>[number]): string {
  const phase = event.phase;
  if (phase === "progress") {
    const matched = event.text.match(/(?:执行步骤|步骤)\s*(\d+)/);
    if (matched?.[1]) {
      return `Step ${matched[1]}`;
    }
    return "Step";
  }
  if (phase === "tool_start") {
    return "TOOL_START";
  }
  if (phase === "tool_done") {
    return "TOOL_DONE";
  }
  if (phase === "reply_start") {
    return "REPLY_START";
  }
  return "REPLY_DONE";
}

function timelinePhaseBadgeClass(
  phase: NonNullable<ChatMessage["timelineEvents"]>[number]["phase"],
  status?: NonNullable<ChatMessage["timelineEvents"]>[number]["status"],
): string {
  if (phase === "progress") {
    // Keep STEP as light blue.
    return "bg-sky-50 text-sky-700";
  }
  if (phase === "tool_start") {
    return "bg-orange-50 text-orange-700";
  }
  if (phase === "tool_done") {
    if (status === "failed") {
      return "bg-rose-50 text-rose-700";
    }
    return "bg-emerald-50 text-emerald-700";
  }
  if (phase === "reply_start") {
    return "bg-amber-50 text-amber-700";
  }
  return "bg-pink-50 text-pink-700";
}

function timelinePhaseTextClass(
  phase: NonNullable<ChatMessage["timelineEvents"]>[number]["phase"],
  status?: NonNullable<ChatMessage["timelineEvents"]>[number]["status"],
): string {
  if (phase === "progress") {
    return "text-sky-700";
  }
  if (phase === "tool_start") {
    return "text-orange-700";
  }
  if (phase === "tool_done") {
    if (status === "failed") {
      return "text-rose-700";
    }
    return "text-emerald-700";
  }
  if (phase === "reply_start") {
    return "text-amber-700";
  }
  return "text-pink-700";
}

function timelinePhaseDotClass(
  phase: NonNullable<ChatMessage["timelineEvents"]>[number]["phase"],
  status?: NonNullable<ChatMessage["timelineEvents"]>[number]["status"],
): string {
  if (phase === "progress") {
    return "border-sky-500";
  }
  if (phase === "tool_start") {
    return "border-orange-500";
  }
  if (phase === "tool_done") {
    if (status === "failed") {
      return "border-rose-500";
    }
    return "border-emerald-500";
  }
  if (phase === "reply_start") {
    return "border-amber-500";
  }
  return "border-pink-500";
}

export function ChatMessages({
  messages,
  sessionSource = "browser",
  isLoading = false,
  loadingTime = null,
  loadingModel = null,
  toolEvents = [],
  decisionEvents = [],
  isToolCallLoading = false,
  toolCallElapsedMs = 0,
  toolCallTime = null,
  pendingAssistantMessageId = null,
  mcpServerNameByToolId = {},
  mcpServerNameByToolName = {},
  onDeleteMessage,
}: ChatMessagesProps) {
  const containerRef = useRef<HTMLElement | null>(null);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [pendingDeleteMessageId, setPendingDeleteMessageId] = useState<string | null>(null);

  const handleCopy = async (message: ChatMessage) => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopiedMessageId(message.id);
      setTimeout(() => {
        setCopiedMessageId((prev) => (prev === message.id ? null : prev));
      }, 1200);
    } catch {
      setCopiedMessageId(null);
    }
  };

  const handleDelete = (message: ChatMessage) => {
    if (pendingDeleteMessageId === message.id) {
      onDeleteMessage?.(message);
      setPendingDeleteMessageId(null);
      return;
    }

    setPendingDeleteMessageId(message.id);
  };

  useEffect(() => {
    const element = containerRef.current;
    if (!element) {
      return;
    }
    element.scrollTop = element.scrollHeight;
  }, [messages, isLoading]);

  useEffect(() => {
    setPendingDeleteMessageId((prev) => {
      if (!prev) {
        return prev;
      }
      const exists = messages.some((message) => message.id === prev);
      return exists ? prev : null;
    });

    setCopiedMessageId((prev) => {
      if (!prev) {
        return prev;
      }
      const exists = messages.some((message) => message.id === prev);
      return exists ? prev : null;
    });
  }, [messages]);

  const showToolEventsPanel = isToolCallLoading || toolEvents.length > 0 || decisionEvents.length > 0;
  let lastUserMessageIndex = -1;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index]?.role === "user") {
      lastUserMessageIndex = index;
      break;
    }
  }
  let firstAssistantAfterLastUserIndex = -1;
  if (lastUserMessageIndex >= 0) {
    for (let index = lastUserMessageIndex + 1; index < messages.length; index += 1) {
      if (messages[index]?.role === "assistant") {
        firstAssistantAfterLastUserIndex = index;
        break;
      }
    }
  }

  return (
    <section ref={containerRef} className="min-h-0 flex-1 overflow-y-auto pr-1">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 pb-0 pt-6">
        {messages.map((message, currentIndex) => {
          const shouldRenderToolPanelInAssistant =
            showToolEventsPanel &&
            message.role === "assistant" &&
            currentIndex === firstAssistantAfterLastUserIndex;
          const role = roleBadge[message.role];
          const Icon = role.icon;
          const displayLabel =
            message.role === "assistant" && message.model
              ? `${role.label} | ${message.model} | ${sessionSource === "lark" ? "Lark" : "Browser"}`
              : `${role.label} | ${sessionSource === "lark" ? "Lark" : "Browser"}`;
          const isPendingAssistantBubble =
            message.role === "assistant" &&
            message.id === pendingAssistantMessageId &&
            message.content.trim().length === 0;
          const timelineEvents = message.timelineEvents ?? [];
          const showTimeline = message.role === "assistant" && timelineEvents.length > 0;

          return (
            <div key={message.id}>
              <article className="space-y-2">
                <div className="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">
                  <Icon className="h-3.5 w-3.5" />
                  {displayLabel}
                  <span className="text-slate-400">{formatShanghaiTimeToMinute(message.createdAt)}</span>
                </div>
                {shouldRenderToolPanelInAssistant ? (
                  <ChatToolEvents
                    events={toolEvents}
                    decisionEvents={decisionEvents}
                    isLoading={isToolCallLoading}
                    loadingElapsedMs={toolCallElapsedMs}
                    mcpServerNameByToolId={mcpServerNameByToolId}
                    mcpServerNameByToolName={mcpServerNameByToolName}
                  />
                ) : null}
                {showTimeline ? (
                  <details className="overflow-hidden rounded-xl border border-slate-200 bg-white/85" open>
                    <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-slate-700">
                      Timeline ({timelineEvents.length})
                    </summary>
                    <div className="border-t border-slate-200 px-3 py-2">
                      <div className="relative">
                        <div className="pointer-events-none absolute bottom-2 left-[11px] top-2 w-px bg-slate-300" />
                        <div className="space-y-3">
                          {timelineEvents.map((event) => (
                            <div key={event.id} className="grid grid-cols-[24px_1fr] items-start gap-x-2">
                              <div className="relative flex justify-center pt-[2px]">
                                <span
                                  className={`h-3 w-3 rounded-full border-2 bg-white ${timelinePhaseDotClass(
                                    event.phase,
                                    event.status,
                                  )}`}
                                />
                              </div>
                              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-600">
                                <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-500">
                                  {formatShanghaiTimeToMinute(event.createdAt)}
                                </span>
                              <span
                                className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${timelinePhaseBadgeClass(
                                  event.phase,
                                  event.status,
                                )}`}
                              >
                                {timelinePhaseLabel(event)}
                              </span>
                              <span className={timelinePhaseTextClass(event.phase, event.status)}>
                                {event.text}
                              </span>
                            </div>
                          </div>
                        ))}
                        </div>
                      </div>
                    </div>
                  </details>
                ) : null}
                <div className="relative mb-5">
                  <div
                    className={`rounded-2xl border px-4 py-3 text-sm leading-7 shadow-sm ${role.bubbleClass}`}
                  >
                    {isPendingAssistantBubble ? (
                      <div className="inline-flex items-center gap-1 px-1 py-1">
                        <span
                          className="h-2 w-2 animate-pulse rounded-full bg-slate-400"
                          style={{ animationDelay: "0ms" }}
                        />
                        <span
                          className="h-2 w-2 animate-pulse rounded-full bg-slate-400"
                          style={{ animationDelay: "180ms" }}
                        />
                        <span
                          className="h-2 w-2 animate-pulse rounded-full bg-slate-400"
                          style={{ animationDelay: "360ms" }}
                        />
                      </div>
                    ) : (
                      <MarkdownMessage content={message.content} inverse={message.role === "user"} />
                    )}
                  </div>
                  {message.role === "system" || isPendingAssistantBubble ? null : (
                    <div className="absolute -bottom-7 right-2 inline-flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => void handleCopy(message)}
                        className="inline-flex h-6 w-6 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600"
                        aria-label="复制消息"
                      >
                        {copiedMessageId === message.id ? (
                          <Check className="h-3.5 w-3.5 text-emerald-500" />
                        ) : (
                          <Copy className="h-3.5 w-3.5" />
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDelete(message)}
                        className="inline-flex h-6 w-6 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600"
                        aria-label="删除消息"
                      >
                        {pendingDeleteMessageId === message.id ? (
                          <Check className="h-3.5 w-3.5 text-red-500" />
                        ) : (
                          <Trash2 className="h-3.5 w-3.5" />
                        )}
                      </button>
                    </div>
                  )}
                </div>
              </article>
            </div>
          );
        })}
        {showToolEventsPanel && firstAssistantAfterLastUserIndex === -1 ? (
          <article className="mb-4 space-y-2">
            <div className="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">
              <Bot className="h-3.5 w-3.5" />
              {loadingModel ? `Leo | ${loadingModel}` : "Leo"}
              {toolCallTime ? (
                <span className="text-slate-400">{formatShanghaiTimeToMinute(toolCallTime)}</span>
              ) : null}
            </div>
            <ChatToolEvents
              events={toolEvents}
              decisionEvents={decisionEvents}
              isLoading={isToolCallLoading}
              loadingElapsedMs={toolCallElapsedMs}
              mcpServerNameByToolId={mcpServerNameByToolId}
              mcpServerNameByToolName={mcpServerNameByToolName}
            />
          </article>
        ) : null}
        {isLoading ? (
          <article className="flex flex-col items-start gap-2">
            <div className="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">
              <Bot className="h-3.5 w-3.5" />
              {loadingModel ? `Leo | ${loadingModel}` : "Leo"}
              {loadingTime ? (
                <span className="text-slate-400">{formatShanghaiTimeToMinute(loadingTime)}</span>
              ) : null}
            </div>
            <div className="ml-2 inline-flex self-start items-center gap-1 px-1 py-1">
              <span
                className="h-2 w-2 animate-pulse rounded-full bg-slate-400"
                style={{ animationDelay: "0ms" }}
              />
              <span
                className="h-2 w-2 animate-pulse rounded-full bg-slate-400"
                style={{ animationDelay: "180ms" }}
              />
              <span
                className="h-2 w-2 animate-pulse rounded-full bg-slate-400"
                style={{ animationDelay: "360ms" }}
              />
            </div>
          </article>
        ) : null}
      </div>
    </section>
  );
}
