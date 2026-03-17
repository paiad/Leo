"use client";

import { MessageCircle, Monitor } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ChatComposer } from "@/features/chat/components/chat-composer";
import { ChatMessages } from "@/features/chat/components/chat-messages";
import {
  clearChatSessionMessages,
  createChatSession,
  deleteChatSessionMessage,
  fetchChatSessionMessages,
  fetchChatSessions,
  fetchPlatformSystemPrompt,
  sendChatMessage,
  streamChatMessage,
  updateChatMessageTimeline,
} from "@/features/chat/services/chat-api";
import type { ChatMessage, ChatRuntimeConfig, ChatTimelineEvent } from "@/features/chat/types/chat";
import { useModelContext } from "@/features/models/context/model-context";

const CHAT_BROWSER_SESSION_STORAGE_KEY = "leo.chat.sessionId.browser";
const CHAT_LARK_SESSION_STORAGE_KEY = "leo.chat.sessionId.lark";
const CHAT_ACTIVE_SOURCE_STORAGE_KEY = "leo.chat.activeSource";
const SUCCESS_NOTICE_AUTO_DISMISS_MS = 3000;
const ERROR_NOTICE_AUTO_DISMISS_MS = 5000;
const DEFAULT_PLATFORM_SYSTEM_PROMPT =
  "你是 OpenLeo，AI Agent 工作台助手。你的目标是帮助用户在同一平台内完成聊天、工具调用、知识检索、Agent 协作与工作流执行。";
type ChatSource = "browser" | "lark";

type StreamProgressDisplayPayload = {
  phase?: string;
  message?: string;
  step?: number;
  maxSteps?: number;
  reason?: string;
  toolName?: string;
  ok?: boolean;
  durationMs?: number;
  error?: string | null;
};

const MAX_TIMELINE_EVENTS = 120;

function generateClientMessageId(prefix: string): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) {
    return `${prefix}-${uuid}`;
  }
  const fallback = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  return `${prefix}-${fallback}`;
}

function nowTimeLabel(): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date());
}

function formatProgressText(
  eventType: "progress" | "tool_start" | "tool_done",
  payload: StreamProgressDisplayPayload,
): string {
  if (eventType === "progress") {
    if (payload.phase === "accepted") {
      return "请求已接收";
    }
    if (payload.phase === "runtime_start") {
      return payload.reason?.trim() ? `运行中：${payload.reason.trim()}` : "运行中";
    }
    if (payload.phase === "step_start") {
      if (payload.step && payload.maxSteps) {
        return `执行步骤 ${payload.step}/${payload.maxSteps}`;
      }
      return payload.step ? `执行步骤 ${payload.step}` : "执行步骤";
    }
    if (payload.phase === "step_done") {
      if (payload.step && payload.maxSteps) {
        return `步骤 ${payload.step}/${payload.maxSteps} 完成`;
      }
      return payload.step ? `步骤 ${payload.step} 完成` : "步骤完成";
    }
    if (payload.phase === "runtime_done") {
      return "正在整理答案";
    }
    if (payload.phase === "terminated") {
      return "流程已停止";
    }
    if (payload.message?.trim()) {
      const sanitized = payload.message.replace(/\s+/g, " ").trim();
      return sanitized || "处理中";
    }
    return "处理中";
  }

  if (eventType === "tool_start") {
    return `开始调用工具：${payload.toolName ?? "unknown"}`;
  }

  const durationText = payload.durationMs ? `（${payload.durationMs}ms）` : "";
  if (payload.ok) {
    return `工具完成：${payload.toolName ?? "unknown"}${durationText}`;
  }
  const errorText = payload.error ? `，错误：${payload.error.slice(0, 80)}` : "";
  return `工具失败：${payload.toolName ?? "unknown"}${durationText}${errorText}`;
}

export function ChatShell() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isSending, setIsSending] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [requestNotice, setRequestNotice] = useState<{
    type: "success" | "error";
    text: string;
  } | null>(null);
  const [pendingTime, setPendingTime] = useState<string | null>(null);
  const [hasStreamStarted, setHasStreamStarted] = useState(false);
  const [activeSource, setActiveSource] = useState<ChatSource>("browser");
  const [sessionBySource, setSessionBySource] = useState<Record<ChatSource, string | null>>({
    browser: null,
    lark: null,
  });
  const [platformSystemPrompt, setPlatformSystemPrompt] = useState(DEFAULT_PLATFORM_SYSTEM_PROMPT);
  const [runtimeConfig] = useState<ChatRuntimeConfig>({
    workspacePrompt: "",
  });
  const [isClearingMessages, setIsClearingMessages] = useState(false);
  const [pendingAssistantMessageId, setPendingAssistantMessageId] = useState<string | null>(null);
  const streamAbortControllerRef = useRef<AbortController | null>(null);
  const { activeModel } = useModelContext();
  const activeModelLabel = activeModel?.name?.trim() || "unknown";
  const activeSessionId = sessionBySource[activeSource];

  const isAbortError = (error: unknown): boolean => {
    if (error instanceof DOMException && error.name === "AbortError") {
      return true;
    }
    if (error instanceof Error) {
      return error.name === "AbortError" || error.message === "The operation was aborted.";
    }
    return false;
  };

  const inferSessionSource = (session: { source?: ChatSource; title: string }): ChatSource => {
    if (session.source === "lark" || session.source === "browser") {
      return session.source;
    }
    return session.title.trim().toLowerCase().startsWith("feishu-") ? "lark" : "browser";
  };

  useEffect(() => {
    let cancelled = false;

    const loadModelConfig = async () => {
      try {
        const systemPrompt = await fetchPlatformSystemPrompt();
        if (cancelled) {
          return;
        }
        setPlatformSystemPrompt(systemPrompt);
      } catch {
        // Keep defaults if backend config request fails.
      }
    };

    void loadModelConfig();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!requestNotice) {
      return;
    }
    const timeoutMs =
      requestNotice.type === "success"
        ? SUCCESS_NOTICE_AUTO_DISMISS_MS
        : ERROR_NOTICE_AUTO_DISMISS_MS;
    const timer = window.setTimeout(() => {
      setRequestNotice((prev) => {
        if (prev?.text === requestNotice.text && prev?.type === requestNotice.type) {
          return null;
        }
        return prev;
      });
    }, timeoutMs);

    return () => {
      window.clearTimeout(timer);
    };
  }, [requestNotice]);

  useEffect(() => {
    let cancelled = false;

    const initSession = async () => {
      const storedActiveSource = window.localStorage.getItem(CHAT_ACTIVE_SOURCE_STORAGE_KEY);
      const preferredSource: ChatSource = storedActiveSource === "lark" ? "lark" : "browser";
      try {
        const sessions = await fetchChatSessions();

        const storedBrowserSessionId = window.localStorage.getItem(CHAT_BROWSER_SESSION_STORAGE_KEY);
        const storedLarkSessionId = window.localStorage.getItem(CHAT_LARK_SESSION_STORAGE_KEY);

        const browserSessions = sessions.filter((session) => inferSessionSource(session) === "browser");
        const larkSessions = sessions.filter((session) => inferSessionSource(session) === "lark");

        const browserSessionId =
          (storedBrowserSessionId &&
          browserSessions.some((session) => session.id === storedBrowserSessionId)
            ? storedBrowserSessionId
            : null) ?? browserSessions[0]?.id ?? null;
        const larkSessionId =
          (storedLarkSessionId && larkSessions.some((session) => session.id === storedLarkSessionId)
            ? storedLarkSessionId
            : null) ?? larkSessions[0]?.id ?? null;

        let ensuredBrowserSessionId = browserSessionId;
        if (!ensuredBrowserSessionId) {
          const created = await createChatSession("browser");
          ensuredBrowserSessionId = created.id;
        }

        if (cancelled) {
          return;
        }
        setSessionBySource({
          browser: ensuredBrowserSessionId,
          lark: larkSessionId,
        });
        if (ensuredBrowserSessionId) {
          window.localStorage.setItem(CHAT_BROWSER_SESSION_STORAGE_KEY, ensuredBrowserSessionId);
        }
        if (larkSessionId) {
          window.localStorage.setItem(CHAT_LARK_SESSION_STORAGE_KEY, larkSessionId);
        } else {
          window.localStorage.removeItem(CHAT_LARK_SESSION_STORAGE_KEY);
        }

        const nextSource: ChatSource =
          preferredSource === "lark" && larkSessionId ? "lark" : "browser";
        setActiveSource(nextSource);
        window.localStorage.setItem(CHAT_ACTIVE_SOURCE_STORAGE_KEY, nextSource);
        return;
      } catch (error) {
        if (!cancelled) {
          const message = error instanceof Error ? error.message : "初始化会话失败";
          setRequestError(message);
        }
      }
    };

    void initSession();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    const loadCurrentSessionMessages = async () => {
      const sleep = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));
      const loadWithRetry = async (targetSessionId: string): Promise<ChatMessage[]> => {
        let lastError: unknown;
        for (let attempt = 0; attempt < 2; attempt += 1) {
          try {
            return await fetchChatSessionMessages(targetSessionId);
          } catch (error) {
            lastError = error;
            if (attempt === 0) {
              await sleep(250);
            }
          }
        }
        throw lastError;
      };

      if (!activeSessionId) {
        setMessages([]);
        return;
      }
      try {
        const historyMessages = await loadWithRetry(activeSessionId);
        if (cancelled) {
          return;
        }
        setMessages(historyMessages);
      } catch (error) {
        if (cancelled) {
          return;
        }
        const message = error instanceof Error ? error.message : "历史消息加载失败，请稍后重试";
        setRequestError(message);
      }
    };

    void loadCurrentSessionMessages();
    return () => {
      cancelled = true;
    };
  }, [activeSessionId]);

  useEffect(() => {
    if (activeSource !== "lark" || isSending) {
      return;
    }

    let cancelled = false;
    const resolveLatestLarkSession = async () => {
      try {
        const sessions = await fetchChatSessions();
        if (cancelled) {
          return;
        }
        const latestLarkSession = sessions
          .filter((session) => inferSessionSource(session) === "lark")
          .sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt))[0];
        if (!latestLarkSession) {
          return;
        }
        setSessionBySource((prev) => {
          if (prev.lark === latestLarkSession.id) {
            return prev;
          }
          window.localStorage.setItem(CHAT_LARK_SESSION_STORAGE_KEY, latestLarkSession.id);
          return { ...prev, lark: latestLarkSession.id };
        });
      } catch {
        // best-effort discovery; ignore transient network errors
      }
    };

    void resolveLatestLarkSession();
    const timer = window.setInterval(() => {
      void resolveLatestLarkSession();
    }, 4000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeSource, isSending]);

  useEffect(() => {
    if (activeSource !== "lark" || !activeSessionId || isSending) {
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const latest = await fetchChatSessionMessages(activeSessionId);
        if (cancelled) {
          return;
        }
        setMessages((prev) => {
          const sameLength = prev.length === latest.length;
          const prevLastId = prev[prev.length - 1]?.id;
          const nextLastId = latest[latest.length - 1]?.id;
          if (sameLength && prevLastId === nextLastId) {
            return prev;
          }
          return latest;
        });
      } catch {
        // keep current messages; polling is best-effort only
      }
    };

    const timer = window.setInterval(() => {
      void poll();
    }, 4000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeSource, activeSessionId, isSending]);

  const appendMessage = (message: ChatMessage) => {
    setMessages((prev) => [...prev, message]);
  };

  const displayMessages = useMemo(() => {
    const workspacePrompt = runtimeConfig.workspacePrompt.trim();
    const systemContent = workspacePrompt
      ? `${platformSystemPrompt}\n\n【Workspace Prompt】\n${workspacePrompt}`
      : platformSystemPrompt;

    const baseMessages = messages.filter((message) => message.role !== "system");
    const systemMessage: ChatMessage = {
      id: "system-prompt-display",
      role: "system",
      content: systemContent,
      createdAt: "System",
    };

    return [systemMessage, ...baseMessages];
  }, [messages, platformSystemPrompt, runtimeConfig.workspacePrompt]);

  const handleDeleteMessage = async (message: ChatMessage) => {
    if (message.role === "system") {
      return;
    }

    if (!activeSessionId) {
      setMessages((prev) => prev.filter((item) => item.id !== message.id));
      return;
    }

    try {
      await deleteChatSessionMessage(activeSessionId, message.id);
      setMessages((prev) => prev.filter((item) => item.id !== message.id));
    } catch (error) {
      const messageText = error instanceof Error ? error.message : "删除失败，请稍后重试";
      setRequestError(messageText);
    }
  };

  const handleClearMessages = async () => {
    if (isClearingMessages) {
      return;
    }

    setIsClearingMessages(true);
    setRequestNotice(null);
    try {
      const sessions = await fetchChatSessions();
      const scopedSessions = sessions.filter(
        (session) => inferSessionSource(session) === activeSource,
      );

      if (scopedSessions.length === 0) {
        setMessages([]);
        setRequestError(null);
        setRequestNotice({
          type: "success",
          text: `${activeSource === "lark" ? "Lark" : "Browser"} 当前没有可清空的会话消息`,
        });
        return;
      }

      const results = await Promise.allSettled(
        scopedSessions.map((session) => clearChatSessionMessages(session.id)),
      );
      const failedResults = results.filter(
        (result): result is PromiseRejectedResult => result.status === "rejected",
      );
      const failedCount = failedResults.length;
      if (failedCount > 0) {
        const reasons = failedResults
          .map((result) =>
            result.reason instanceof Error
              ? result.reason.message
              : String(result.reason ?? "未知错误"),
          )
          .filter((reason) => reason.trim().length > 0);
        const reasonText =
          reasons.length > 0 ? `：${reasons.slice(0, 2).join("；")}` : "";
        throw new Error(`部分会话清空失败（${failedCount}/${results.length}）${reasonText}`);
      }
      const deletedCount = results.reduce((total, result) => {
        if (result.status === "fulfilled") {
          return total + result.value;
        }
        return total;
      }, 0);
      setMessages([]);
      setRequestError(null);
      setRequestNotice({
        type: "success",
        text: `清空成功：已删除 ${deletedCount} 条消息`,
      });
    } catch (error) {
      const messageText = error instanceof Error ? error.message : "清空失败，请稍后重试";
      setRequestError(null);
      setRequestNotice({
        type: "error",
        text: messageText,
      });
    } finally {
      setIsClearingMessages(false);
    }
  };

  const upsertAssistantMessage = (
    id: string,
    content: string,
    createdAt: string,
    model?: string,
  ) => {
    setMessages((prev) => {
      const index = prev.findIndex((message) => message.id === id);
      if (index === -1) {
        return [
          ...prev,
          {
            id,
            role: "assistant",
            content,
            createdAt,
            model,
          },
        ];
      }

      const next = [...prev];
      next[index] = { ...next[index], content, model: model ?? next[index].model };
      return next;
    });
  };

  const replaceMessageId = (sourceId: string, targetId: string) => {
    if (!targetId || sourceId === targetId) {
      return;
    }
    setMessages((prev) =>
      prev.map((message) => (message.id === sourceId ? { ...message, id: targetId } : message)),
    );
  };

  const removeMessageById = (messageId: string) => {
    setMessages((prev) => prev.filter((message) => message.id !== messageId));
  };

  const appendTimelineEvent = (messageId: string, event: ChatTimelineEvent) => {
    setMessages((prev) =>
      prev.map((message) => {
        if (message.id !== messageId || message.role !== "assistant") {
          return message;
        }
        const history = message.timelineEvents ?? [];
        const last = history[history.length - 1];
        if (
          last &&
          last.phase === event.phase &&
          last.text === event.text &&
          last.status === event.status
        ) {
          return message;
        }
        const nextTimeline = [...history, event].slice(-MAX_TIMELINE_EVENTS);
        return { ...message, timelineEvents: nextTimeline };
      }),
    );
  };

  const handleSend = async (content: string) => {
    if (isSending) {
      return;
    }
    let targetSessionId = activeSessionId;
    if (!targetSessionId) {
      try {
        const session = await createChatSession(activeSource);
        targetSessionId = session.id;
        setSessionBySource((prev) => ({ ...prev, [activeSource]: session.id }));
        if (activeSource === "browser") {
          window.localStorage.setItem(CHAT_BROWSER_SESSION_STORAGE_KEY, session.id);
        } else {
          window.localStorage.setItem(CHAT_LARK_SESSION_STORAGE_KEY, session.id);
        }
      } catch {
        setRequestError("创建会话失败，请检查后端和数据库连接");
        return;
      }
    }

    const requestConfig = {
      workspacePrompt: runtimeConfig.workspacePrompt.trim(),
      requestId: generateClientMessageId("req"),
      model: activeModel?.name,
      baseUrl: activeModel?.baseUrl,
      apiKey: activeModel?.apiKey,
    };

    const now = new Date();
    const sentAt = now.toTimeString().slice(0, 5);
    const userMessage: ChatMessage = {
      id: generateClientMessageId("msg-user"),
      role: "user",
      content,
      createdAt: sentAt,
    };

    appendMessage(userMessage);
    setRequestError(null);
    setRequestNotice(null);
    setIsSending(true);
    setPendingTime(sentAt);
    setHasStreamStarted(false);

    const assistantMessageId = generateClientMessageId("msg-assistant");
    setPendingAssistantMessageId(assistantMessageId);
    let finalAssistantMessageId = assistantMessageId;
    let timelineMessageId = assistantMessageId;
    let timelineForPersistence: ChatTimelineEvent[] = [];
    let streamedContent = "";
    let hasStreamChunk = false;
    let hasReplyStartEvent = false;
    let shouldRemoveAssistantPlaceholder = false;
    let hasPersistedTimeline = false;
    const pushTimelineEvent = (
      messageId: string,
      event: ChatTimelineEvent,
    ) => {
      timelineMessageId = messageId;
      appendTimelineEvent(messageId, event);
      const last = timelineForPersistence[timelineForPersistence.length - 1];
      if (
        last &&
        last.phase === event.phase &&
        last.text === event.text &&
        last.status === event.status
      ) {
        return;
      }
      timelineForPersistence = [...timelineForPersistence, event].slice(-MAX_TIMELINE_EVENTS);
    };
    appendMessage({
      id: assistantMessageId,
      role: "assistant",
      content: "",
      createdAt: sentAt,
      model: activeModelLabel,
      timelineEvents: [],
    });

    try {
      const abortController = new AbortController();
      streamAbortControllerRef.current = abortController;
      await streamChatMessage(
        content,
        {
          onChunk: (chunk) => {
            if (!hasReplyStartEvent) {
              pushTimelineEvent(timelineMessageId, {
                id: generateClientMessageId("timeline"),
                phase: "reply_start",
                text: "开始回复消息",
                createdAt: nowTimeLabel(),
                status: "running",
              });
              hasReplyStartEvent = true;
            }
            streamedContent += chunk;
            hasStreamChunk = true;
            setPendingAssistantMessageId(null);
            setHasStreamStarted(true);
            upsertAssistantMessage(assistantMessageId, streamedContent, sentAt, activeModelLabel);
            setPendingTime(null);
          },
          onDone: ({ messageId }) => {
            if (messageId) {
              replaceMessageId(assistantMessageId, messageId);
              finalAssistantMessageId = messageId;
              timelineMessageId = messageId;
              setPendingAssistantMessageId((prev) =>
                prev === assistantMessageId ? messageId : prev,
              );
            }
            const targetMessageId = messageId || finalAssistantMessageId;
            pushTimelineEvent(targetMessageId, {
              id: generateClientMessageId("timeline"),
              phase: "reply_done",
              text: "回复完成",
              createdAt: nowTimeLabel(),
              status: "success",
            });
          },
          onProgress: (eventType, payload) => {
            const line = formatProgressText(eventType, payload);
            if (!line) {
              return;
            }
            const status =
              eventType === "tool_done"
                ? payload.ok
                  ? "success"
                  : "failed"
                : eventType === "tool_start"
                  ? "running"
                  : undefined;
            pushTimelineEvent(timelineMessageId, {
              id: generateClientMessageId("timeline"),
              phase: eventType,
              text: line,
              createdAt: nowTimeLabel(),
              status,
            });
            setHasStreamStarted(true);
            setPendingTime(null);
          },
        },
        {
          ...requestConfig,
          sessionId: targetSessionId,
          source: activeSource,
        },
        abortController.signal,
      );

      if (!hasStreamChunk) {
        throw new Error("流式未返回有效内容");
      }
    } catch (streamError) {
      if (isAbortError(streamError)) {
        shouldRemoveAssistantPlaceholder = !hasStreamChunk;
        return;
      }
      if (hasStreamChunk) {
        const message = streamError instanceof Error ? streamError.message : "流式输出中断";
        setRequestError(message);
      } else {
        const streamErrorMessage =
          streamError instanceof Error ? streamError.message : "流式调用失败，已回退普通请求";
        setRequestError(streamErrorMessage);
        pushTimelineEvent(timelineMessageId, {
          id: generateClientMessageId("timeline"),
          phase: "progress",
          text: "流式失败，回退普通请求",
          createdAt: nowTimeLabel(),
          status: "failed",
        });
        try {
          const assistantMessage = await sendChatMessage(content, {
            ...requestConfig,
            sessionId: targetSessionId,
            source: activeSource,
          });
          replaceMessageId(assistantMessageId, assistantMessage.message.id);
          finalAssistantMessageId = assistantMessage.message.id;
          timelineMessageId = assistantMessage.message.id;
          setPendingAssistantMessageId(null);
          upsertAssistantMessage(
            assistantMessage.message.id,
            assistantMessage.message.content,
            assistantMessage.message.createdAt,
            assistantMessage.message.model || activeModelLabel,
          );
          pushTimelineEvent(assistantMessage.message.id, {
            id: generateClientMessageId("timeline"),
            phase: "reply_done",
            text: "普通请求回复完成",
            createdAt: nowTimeLabel(),
            status: "success",
          });
        } catch (invokeError) {
          const message =
            invokeError instanceof Error ? invokeError.message : "发送失败，请稍后重试";
          setRequestError(message);
          shouldRemoveAssistantPlaceholder = true;
        }
      }
    } finally {
      streamAbortControllerRef.current = null;
      if (
        targetSessionId &&
        timelineForPersistence.length > 0 &&
        timelineMessageId &&
        !shouldRemoveAssistantPlaceholder
      ) {
        try {
          await updateChatMessageTimeline(targetSessionId, timelineMessageId, timelineForPersistence);
          hasPersistedTimeline = true;
        } catch (error) {
          console.warn("Failed to persist timeline events", error);
        }
      }
      if (hasStreamChunk) {
        upsertAssistantMessage(finalAssistantMessageId, streamedContent, sentAt, activeModelLabel);
      } else if (shouldRemoveAssistantPlaceholder) {
        removeMessageById(finalAssistantMessageId);
        setPendingAssistantMessageId(null);
      }
      if (hasPersistedTimeline) {
        setMessages((prev) =>
          prev.map((message) =>
            message.id === timelineMessageId && message.role === "assistant"
              ? { ...message, timelineEvents: timelineForPersistence }
              : message,
          ),
        );
      }
      if (hasStreamChunk) {
        setPendingAssistantMessageId(null);
      }
      setIsSending(false);
      setPendingTime(null);
      setHasStreamStarted(false);
    }
  };

  const handleStopGeneration = () => {
    if (!isSending) {
      return;
    }
    streamAbortControllerRef.current?.abort();
  };

  const handleSwitchSource = (source: ChatSource) => {
    if (isSending) {
      return;
    }
    setActiveSource(source);
    window.localStorage.setItem(CHAT_ACTIVE_SOURCE_STORAGE_KEY, source);
    setRequestError(null);
    setRequestNotice(null);
  };

  return (
    <div className="relative -mb-8 -ml-4 -mr-4 -mt-6 h-[calc(100vh-4rem)] overflow-hidden md:-ml-6 md:-mr-6">
      <div className="absolute left-3 top-2 z-10 inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white p-1 shadow-sm md:left-5">
        <button
          type="button"
          onClick={() => handleSwitchSource("browser")}
          className={`rounded-md px-3 py-1.5 text-xs font-semibold transition-colors ${
            activeSource === "browser"
              ? "bg-slate-900 text-white"
              : "text-slate-600 hover:bg-slate-100"
          }`}
        >
          <span className="inline-flex items-center gap-1.5">
            <Monitor className="h-3.5 w-3.5" />
            Browser
          </span>
        </button>
        <button
          type="button"
          onClick={() => handleSwitchSource("lark")}
          className={`rounded-md px-3 py-1.5 text-xs font-semibold transition-colors ${
            activeSource === "lark"
              ? "bg-slate-900 text-white"
              : "text-slate-600 hover:bg-slate-100"
          }`}
        >
          <span className="inline-flex items-center gap-1.5">
            <MessageCircle className="h-3.5 w-3.5" />
            Lark
          </span>
        </button>
      </div>
      <section className="flex h-[calc(100vh-4rem)] min-w-0 flex-1 flex-col overflow-hidden">
        <div className="min-h-0 flex flex-1 flex-col px-0 pt-0">
          <ChatMessages
            messages={displayMessages}
            sessionSource={activeSource}
            isLoading={isSending && !hasStreamStarted && pendingAssistantMessageId === null}
            loadingTime={pendingTime}
            loadingModel={activeModelLabel}
            pendingAssistantMessageId={pendingAssistantMessageId}
            onDeleteMessage={handleDeleteMessage}
          />
        </div>
        <div className="sticky bottom-0 z-10 bg-[rgb(var(--background))] pb-2 pt-2">
          <div className="mx-auto w-full max-w-3xl">
            {requestError ? (
              <p className="mb-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
                {requestError}
              </p>
            ) : null}
            {requestNotice ? (
              <p
                className={`mb-2 rounded-lg border px-3 py-2 text-xs ${
                  requestNotice.type === "success"
                    ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                    : "border-red-200 bg-red-50 text-red-600"
                }`}
              >
                {requestNotice.text}
              </p>
            ) : null}
          </div>
          <ChatComposer
            isSending={isSending}
            onSend={handleSend}
            onStop={handleStopGeneration}
            onClearMessages={handleClearMessages}
            isClearingMessages={isClearingMessages}
            clearScopeLabel={activeSource === "lark" ? "Lark" : "Browser"}
            placeholder={
              activeSource === "lark"
                ? "Lark 会话（已持久化）"
                : "Browser 会话（独立提问）"
            }
          />
        </div>
      </section>
    </div>
  );
}
