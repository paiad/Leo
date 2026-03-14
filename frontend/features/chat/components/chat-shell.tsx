"use client";

import { SlidersHorizontal } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ChatComposer } from "@/features/chat/components/chat-composer";
import { ChatMessages } from "@/features/chat/components/chat-messages";
import { ChatSettingsPanel } from "@/features/chat/components/chat-settings-panel";
import {
  clearChatSessionMessages,
  createChatSession,
  deleteChatSessionMessage,
  fetchChatSessionMessages,
  fetchChatSessions,
  fetchPlatformSystemPrompt,
  sendChatMessage,
  streamChatMessage,
} from "@/features/chat/services/chat-api";
import type { ChatMessage, ChatRuntimeConfig, ChatTimelineEvent } from "@/features/chat/types/chat";
import { useModelContext } from "@/features/models/context/model-context";

const CHAT_SESSION_STORAGE_KEY = "leo.chat.sessionId";
const DEFAULT_PLATFORM_SYSTEM_PROMPT =
  "你是 Leo，AI Agent 工作台助手。你的目标是帮助用户在同一平台内完成聊天、工具调用、知识检索、Agent 协作与工作流执行。";

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
      return "运行中";
    }
    if (payload.phase === "step_start") {
      return payload.step ? `执行步骤 ${payload.step}` : "执行步骤";
    }
    if (payload.phase === "step_done") {
      return payload.step ? `步骤 ${payload.step} 完成` : "步骤完成";
    }
    if (payload.phase === "runtime_done") {
      return "正在整理答案";
    }
    if (payload.phase === "terminated") {
      return "流程已停止";
    }
    if (payload.message?.trim()) {
      const sanitized = payload.message
        .replace(/\/\d+\b/g, "")
        .replace(/最多\s*\d+\s*步/g, "")
        .trim();
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
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isSending, setIsSending] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [pendingTime, setPendingTime] = useState<string | null>(null);
  const [hasStreamStarted, setHasStreamStarted] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [platformSystemPrompt, setPlatformSystemPrompt] = useState(DEFAULT_PLATFORM_SYSTEM_PROMPT);
  const [runtimeConfig, setRuntimeConfig] = useState<ChatRuntimeConfig>({
    workspacePrompt: "",
  });
  const [isClearingMessages, setIsClearingMessages] = useState(false);
  const [pendingAssistantMessageId, setPendingAssistantMessageId] = useState<string | null>(null);
  const streamAbortControllerRef = useRef<AbortController | null>(null);
  const { activeModel } = useModelContext();
  const activeModelLabel = activeModel?.name?.trim() || "unknown";

  const isAbortError = (error: unknown): boolean => {
    if (error instanceof DOMException && error.name === "AbortError") {
      return true;
    }
    if (error instanceof Error) {
      return error.name === "AbortError" || error.message === "The operation was aborted.";
    }
    return false;
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
    let cancelled = false;

    const initSession = async () => {
      const sleep = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));
      const loadHistoryWithRetry = async (targetSessionId: string): Promise<ChatMessage[]> => {
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
      const loadSessionAndHistory = async (targetSessionId: string): Promise<boolean> => {
        const historyMessages = await loadHistoryWithRetry(targetSessionId);
        if (cancelled) {
          return true;
        }
        setSessionId(targetSessionId);
        setMessages(historyMessages);
        window.localStorage.setItem(CHAT_SESSION_STORAGE_KEY, targetSessionId);
        return true;
      };

      const storedSessionId = window.localStorage.getItem(CHAT_SESSION_STORAGE_KEY);

      if (storedSessionId) {
        try {
          const sessions = await fetchChatSessions();
          const storedSession = sessions.find((session) => session.id === storedSessionId);
          const isFeishuSession = Boolean(
            storedSession?.title.trim().toLowerCase().startsWith("feishu-"),
          );
          if (isFeishuSession) {
            window.localStorage.removeItem(CHAT_SESSION_STORAGE_KEY);
          }
        } catch {
          // If session list fetch fails, keep legacy behavior and try stored session directly.
        }

        const activeStoredSessionId = window.localStorage.getItem(CHAT_SESSION_STORAGE_KEY);
        if (!activeStoredSessionId) {
          // Stored session was cleared (e.g. Feishu session); continue fallback flow.
        } else {
        try {
          await loadSessionAndHistory(activeStoredSessionId);
          return;
        } catch (error) {
          const message = error instanceof Error ? error.message : "";
          const shouldResetSession =
            message.includes("会话不存在") || message.includes("404") || message.includes("HTTP 404");

          if (shouldResetSession) {
            window.localStorage.removeItem(CHAT_SESSION_STORAGE_KEY);
          } else if (!cancelled) {
            setRequestError(message || "历史消息加载失败，请稍后重试");
          }
        }
        }
      }

      try {
        const sessions = await fetchChatSessions();
        const browserSessions = sessions.filter(
          (session) => !session.title.trim().toLowerCase().startsWith("feishu-"),
        );
        for (const session of browserSessions) {
          try {
            await loadSessionAndHistory(session.id);
            return;
          } catch {
            // try next session
          }
        }
      } catch (error) {
        if (!cancelled) {
          const message = error instanceof Error ? error.message : "加载会话列表失败";
          setRequestError(message);
        }
      }

      try {
        const session = await createChatSession();
        if (cancelled) {
          return;
        }
        setSessionId(session.id);
        window.localStorage.setItem(CHAT_SESSION_STORAGE_KEY, session.id);
      } catch {
        if (cancelled) {
          return;
        }
        setRequestError("初始化会话失败，请检查后端和数据库连接");
      }
    };

    void initSession();

    return () => {
      cancelled = true;
    };
  }, []);

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

    if (!sessionId) {
      setMessages((prev) => prev.filter((item) => item.id !== message.id));
      return;
    }

    try {
      await deleteChatSessionMessage(sessionId, message.id);
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
    try {
      if (sessionId) {
        await clearChatSessionMessages(sessionId);
      }
      setMessages([]);
      setRequestError(null);
    } catch (error) {
      const messageText = error instanceof Error ? error.message : "清空失败，请稍后重试";
      setRequestError(messageText);
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
        if (last && last.phase === event.phase && last.text === event.text) {
          return message;
        }
        const nextTimeline = [...history, event].slice(-30);
        return { ...message, timelineEvents: nextTimeline };
      }),
    );
  };

  const handleSend = async (content: string) => {
    if (isSending) {
      return;
    }
    let activeSessionId = sessionId;
    if (!activeSessionId) {
      try {
        const session = await createChatSession();
        activeSessionId = session.id;
        setSessionId(session.id);
        window.localStorage.setItem(CHAT_SESSION_STORAGE_KEY, session.id);
      } catch {
        setRequestError("创建会话失败，请检查后端和数据库连接");
        return;
      }
    }

    const requestConfig = {
      workspacePrompt: runtimeConfig.workspacePrompt.trim(),
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
    setIsSending(true);
    setPendingTime(sentAt);
    setHasStreamStarted(false);

    const assistantMessageId = generateClientMessageId("msg-assistant");
    setPendingAssistantMessageId(assistantMessageId);
    let finalAssistantMessageId = assistantMessageId;
    let streamedContent = "";
    let hasStreamChunk = false;
    let hasReplyStartEvent = false;
    let shouldRemoveAssistantPlaceholder = false;
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
              appendTimelineEvent(assistantMessageId, {
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
              setPendingAssistantMessageId((prev) =>
                prev === assistantMessageId ? messageId : prev,
              );
            }
            appendTimelineEvent(messageId || finalAssistantMessageId, {
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
            appendTimelineEvent(
              assistantMessageId,
              {
                id: generateClientMessageId("timeline"),
                phase: eventType,
                text: line,
                createdAt: nowTimeLabel(),
                status,
              },
            );
            setHasStreamStarted(true);
            setPendingTime(null);
          },
        },
        {
          ...requestConfig,
          sessionId: activeSessionId,
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
        appendTimelineEvent(assistantMessageId, {
          id: generateClientMessageId("timeline"),
          phase: "progress",
          text: "流式失败，回退普通请求",
          createdAt: nowTimeLabel(),
          status: "failed",
        });
        try {
          const assistantMessage = await sendChatMessage(content, {
            ...requestConfig,
            sessionId: activeSessionId,
          });
          replaceMessageId(assistantMessageId, assistantMessage.message.id);
          finalAssistantMessageId = assistantMessage.message.id;
          setPendingAssistantMessageId(null);
          upsertAssistantMessage(
            assistantMessage.message.id,
            assistantMessage.message.content,
            assistantMessage.message.createdAt,
            assistantMessage.message.model || activeModelLabel,
          );
          appendTimelineEvent(assistantMessage.message.id, {
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
      if (hasStreamChunk) {
        upsertAssistantMessage(finalAssistantMessageId, streamedContent, sentAt, activeModelLabel);
      } else if (shouldRemoveAssistantPlaceholder) {
        removeMessageById(finalAssistantMessageId);
        setPendingAssistantMessageId(null);
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

  return (
    <div className="relative -mb-8 -ml-4 -mr-4 -mt-6 h-[calc(100vh-4rem)] overflow-hidden md:-ml-6 md:-mr-6">
      <button
        type="button"
        onClick={() => setSettingsOpen(true)}
        className="absolute right-3 top-2 z-10 inline-flex h-10 w-10 items-center justify-center rounded-lg text-slate-600 transition-colors hover:text-slate-900 md:right-5"
        aria-label="打开设置抽屉"
      >
        <SlidersHorizontal className="h-5 w-5" />
      </button>
      <section className="flex h-[calc(100vh-4rem)] min-w-0 flex-1 flex-col overflow-hidden">
        <div className="min-h-0 flex flex-1 flex-col px-0 pt-0">
          <ChatMessages
            messages={displayMessages}
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
          </div>
          <ChatComposer
            isSending={isSending}
            onSend={handleSend}
            onStop={handleStopGeneration}
            onClearMessages={handleClearMessages}
            isClearingMessages={isClearingMessages}
          />
        </div>
      </section>

      <div
        className={`absolute inset-0 z-20 transition-opacity duration-200 ${
          settingsOpen ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0"
        }`}
      >
        <button
          type="button"
          onClick={() => setSettingsOpen(false)}
          className="absolute inset-0 bg-slate-900/20"
          aria-label="关闭设置抽屉遮罩"
        />
        <div
          className={`absolute bottom-0 right-0 top-0 transition-transform duration-300 ease-out ${
            settingsOpen ? "translate-x-0" : "translate-x-full"
          }`}
        >
          <ChatSettingsPanel
            runtimeConfig={runtimeConfig}
            platformSystemPrompt={platformSystemPrompt}
            activeModelLabel={activeModelLabel}
            onApplyRuntimeConfig={setRuntimeConfig}
            onClose={() => setSettingsOpen(false)}
          />
        </div>
      </div>
    </div>
  );
}
