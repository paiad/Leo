import type {
  ChatMessage,
  ChatDecisionEvent,
  ChatMcpCatalogItem,
  ChatMcpDiscoveredTool,
  ChatRoutingDashboard,
  ChatRoutingEvent,
  ChatMcpServer,
  ChatToolEvent,
  ChatToolTransportType,
} from "@/features/chat/types/chat";

const DEFAULT_API_BASE_URL = "http://localhost:8000";

type ChatRequestOptions = {
  workspacePrompt?: string;
  sessionId?: string | null;
  requestId?: string;
  model?: string;
  baseUrl?: string;
  apiKey?: string;
  source?: "browser" | "lark";
};

type ChatMessageApiResponse = {
  success: boolean;
  data: {
    id: string;
    role: ChatMessage["role"];
    content: string;
    createdAt: string;
    model?: string;
    toolEvents?: ChatToolEvent[];
    decisionEvents?: ChatDecisionEvent[];
  } | null;
  toolEvents?: ChatToolEvent[];
  decisionEvents?: ChatDecisionEvent[];
  error: string | null;
};

export type ChatSendResult = {
  message: ChatMessage;
  toolEvents: ChatToolEvent[];
  decisionEvents: ChatDecisionEvent[];
};

type ChatModelConfigApiResponse = {
  success: boolean;
  data: {
    provider: string;
    defaultBaseUrl: string;
    defaultModel: string;
    availableModels: string[];
  } | null;
  error: string | null;
};

type ChatSessionApiResponse = {
  success: boolean;
  data: {
    id: string;
    title: string;
    createdAt: string;
    updatedAt: string;
    source?: "browser" | "lark";
  } | null;
  error: string | null;
};

type ChatMessageListApiResponse = {
  success: boolean;
  data: {
    id: string;
    role: ChatMessage["role"];
    content: string;
    createdAt: string;
    model?: string;
    toolEvents?: ChatToolEvent[];
    decisionEvents?: ChatDecisionEvent[];
  }[];
  error: string | null;
};

type SystemPromptApiResponse = {
  success: boolean;
  data: {
    platformPrompt: string;
  } | null;
  error: string | null;
};

type McpCatalogApiResponse = {
  success: boolean;
  data: ChatMcpCatalogItem[];
  error: string | null;
};

type McpServerApiResponse = {
  success: boolean;
  data: ChatMcpServer | null;
  error: string | null;
};

type McpServerListApiResponse = {
  success: boolean;
  data: ChatMcpServer[];
  error: string | null;
};

type McpDiscoveredToolListApiResponse = {
  success: boolean;
  data: ChatMcpDiscoveredTool[];
  error: string | null;
};

type RuntimeRoutingDashboardApiResponse = {
  success: boolean;
  data: ChatRoutingDashboard | null;
  error: string | null;
};

type RuntimeRoutingEventListApiResponse = {
  success: boolean;
  data: ChatRoutingEvent[];
  error: string | null;
};

export type CreateMcpServerInput = {
  serverId?: string;
  name?: string;
  type: ChatToolTransportType;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  description?: string;
  category?: string;
  capabilityProfile?: Record<string, unknown>;
  enabled?: boolean;
};

export type UpdateMcpServerInput = {
  name?: string;
  type?: ChatToolTransportType;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  description?: string;
  category?: string;
  capabilityProfile?: Record<string, unknown>;
  enabled?: boolean;
};

export type ChatModelConfig = {
  provider: string;
  defaultBaseUrl: string;
  defaultModel: string;
  availableModels: string[];
};

export type ChatSession = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  source?: "browser" | "lark";
};

type ChatStreamHandlers = {
  onChunk: (chunk: string) => void;
  onDone?: (payload: { messageId?: string; toolEvents?: ChatToolEvent[]; decisionEvents?: ChatDecisionEvent[] }) => void;
  onError?: (message: string) => void;
  onToolEvents?: (events: ChatToolEvent[]) => void;
  onDecisionEvents?: (events: ChatDecisionEvent[]) => void;
  onProgress?: (
    eventType: "progress" | "tool_start" | "tool_done",
    payload: StreamProgressPayload,
  ) => void;
};

type StreamErrorPayload = {
  message?: string;
  errorType?: string;
  context?: Record<string, unknown>;
};

type StreamProgressPayload = {
  phase?: string;
  message?: string;
  step?: number;
  maxSteps?: number;
  reason?: string;
  toolName?: string;
  arguments?: string;
  ok?: boolean;
  durationMs?: number;
  error?: string | null;
  resultPreview?: string;
};

type ChatStreamEvent =
  | { type: "chunk"; payload: { content?: string } }
  | {
      type: "done";
      payload: {
        done?: boolean;
        messageId?: string;
        toolEvents?: ChatToolEvent[];
        decisionEvents?: ChatDecisionEvent[];
      };
    }
  | { type: "tool_events"; payload: { toolEvents?: ChatToolEvent[] } }
  | { type: "decision"; payload: { decisionEvents?: ChatDecisionEvent[] } }
  | { type: "progress"; payload: StreamProgressPayload }
  | { type: "tool_start"; payload: StreamProgressPayload }
  | { type: "tool_done"; payload: StreamProgressPayload }
  | { type: "error"; payload: StreamErrorPayload };

function resolveApiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
}

function buildChatRequestBody(
  content: string,
  options?: ChatRequestOptions,
): Record<string, unknown> {
  const requestBody: Record<string, unknown> = { content };
  if (options?.workspacePrompt?.trim()) {
    requestBody.workspacePrompt = options.workspacePrompt.trim();
  }
  if (options?.sessionId) {
    requestBody.sessionId = options.sessionId;
  }
  if (options?.requestId?.trim()) {
    requestBody.requestId = options.requestId.trim();
  }
  if (options?.model?.trim()) {
    requestBody.model = options.model.trim();
  }
  if (options?.baseUrl?.trim()) {
    requestBody.baseUrl = options.baseUrl.trim();
  }
  if (options?.apiKey?.trim()) {
    requestBody.apiKey = options.apiKey.trim();
  }
  if (options?.source) {
    requestBody.source = options.source;
  }
  return requestBody;
}

async function readErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const payload = (await response.json()) as {
      error?: string | null;
      detail?: { error?: string | null } | string;
    };
    if (payload?.error && payload.error.trim()) {
      return payload.error;
    }
    if (
      payload?.detail &&
      typeof payload.detail === "object" &&
      payload.detail.error &&
      payload.detail.error.trim()
    ) {
      return payload.detail.error;
    }
    if (typeof payload?.detail === "string" && payload.detail.trim()) {
      return payload.detail;
    }
  } catch {
    // ignore parse errors, fallback to status text below
  }
  return `${fallback}：HTTP ${response.status}`;
}

function parseSseEvent(rawEvent: string): ChatStreamEvent | null {
  const lines = rawEvent.split("\n");
  let eventType = "message";
  const dataLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith("event:")) {
      eventType = line.slice(6).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }

  if (dataLines.length === 0) {
    return null;
  }

  let payload: unknown;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return null;
  }

  if (eventType === "chunk") {
    return { type: "chunk", payload: payload as { content?: string } };
  }
  if (eventType === "done") {
    return {
      type: "done",
      payload: payload as {
        done?: boolean;
        messageId?: string;
        toolEvents?: ChatToolEvent[];
        decisionEvents?: ChatDecisionEvent[];
      },
    };
  }
  if (eventType === "tool_events") {
    return { type: "tool_events", payload: payload as { toolEvents?: ChatToolEvent[] } };
  }
  if (eventType === "decision") {
    return { type: "decision", payload: payload as { decisionEvents?: ChatDecisionEvent[] } };
  }
  if (eventType === "error") {
    return { type: "error", payload: payload as StreamErrorPayload };
  }
  if (eventType === "progress") {
    return { type: "progress", payload: payload as StreamProgressPayload };
  }
  if (eventType === "tool_start") {
    return { type: "tool_start", payload: payload as StreamProgressPayload };
  }
  if (eventType === "tool_done") {
    return { type: "tool_done", payload: payload as StreamProgressPayload };
  }

  return null;
}

export async function sendChatMessage(
  content: string,
  options?: ChatRequestOptions,
): Promise<ChatSendResult> {
  const baseUrl = resolveApiBaseUrl();
  const requestBody = buildChatRequestBody(content, options);

  const response = await fetch(`${baseUrl}/api/v1/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(requestBody),
  });

  if (!response.ok) {
    throw new Error(await readErrorMessage(response, "请求失败"));
  }

  const payload = (await response.json()) as ChatMessageApiResponse;

  if (!payload.success || !payload.data) {
    throw new Error(payload.error ?? "服务返回异常");
  }

  return {
    message: payload.data,
    toolEvents: payload.toolEvents ?? [],
    decisionEvents: payload.decisionEvents ?? [],
  };
}

export async function streamChatMessage(
  content: string,
  handlers: ChatStreamHandlers,
  options?: ChatRequestOptions,
  signal?: AbortSignal,
): Promise<void> {
  const baseUrl = resolveApiBaseUrl();
  const requestBody = buildChatRequestBody(content, options);
  const streamEndpoint = `${baseUrl}/api/v1/chat/completions?stream=true`;

  const response = await fetch(streamEndpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(requestBody),
    signal,
  });

  if (!response.ok) {
    throw new Error(await readErrorMessage(response, "流式请求失败"));
  }
  if (!response.body) {
    throw new Error("流式响应体为空");
  }

  const requestLogContext = {
    endpoint: streamEndpoint,
    sessionId: options?.sessionId ?? null,
  };
  const decoder = new TextDecoder("utf-8");
  const reader = response.body.getReader();
  let buffer = "";
  let streamErrorMessage: string | null = null;

  const emitStreamError = (
    message: string,
    payload?: StreamErrorPayload,
    reason: "event_error" | "stream_incomplete" = "event_error",
  ) => {
    if (reason === "event_error") {
      console.error("[chat.stream] 收到错误事件", {
        ...requestLogContext,
        errorType: payload?.errorType ?? null,
        context: payload?.context ?? null,
        message,
      });
    } else {
      console.error("[chat.stream] 连接异常中断", {
        ...requestLogContext,
        message,
        tail: buffer.slice(Math.max(buffer.length - 200, 0)),
      });
    }
    handlers.onError?.(message);
    if (!streamErrorMessage) {
      streamErrorMessage = message;
    }
  };

  const processEvent = (parsed: ChatStreamEvent): boolean => {
    if (parsed.type === "chunk") {
      const chunk = parsed.payload.content;
      if (chunk) {
        handlers.onChunk(chunk);
      }
      return false;
    }

    if (parsed.type === "error") {
      const message = parsed.payload.message ?? "流式请求失败";
      emitStreamError(message, parsed.payload, "event_error");
      return false;
    }

    if (parsed.type === "progress" || parsed.type === "tool_start" || parsed.type === "tool_done") {
      handlers.onProgress?.(parsed.type, parsed.payload);
      return false;
    }

    if (parsed.type === "tool_events") {
      const events = parsed.payload.toolEvents;
      if (Array.isArray(events) && events.length > 0) {
        handlers.onToolEvents?.(events);
      }
      return false;
    }

    if (parsed.type === "decision") {
      const events = parsed.payload.decisionEvents;
      if (Array.isArray(events) && events.length > 0) {
        handlers.onDecisionEvents?.(events);
      }
      return false;
    }

    if (parsed.type === "done") {
      handlers.onDone?.({
        messageId: parsed.payload.messageId,
        toolEvents: parsed.payload.toolEvents,
        decisionEvents: parsed.payload.decisionEvents,
      });
      if (streamErrorMessage) {
        throw new Error(streamErrorMessage);
      }
      return true;
    }

    return false;
  };

  const consumeBuffer = (allowTailEvent: boolean): boolean => {
    while (true) {
      const eventEnd = buffer.indexOf("\n\n");
      if (eventEnd === -1) {
        break;
      }

      const rawEvent = buffer.slice(0, eventEnd).trim();
      buffer = buffer.slice(eventEnd + 2);
      if (!rawEvent) {
        continue;
      }

      const parsed = parseSseEvent(rawEvent);
      if (!parsed) {
        continue;
      }
      if (processEvent(parsed)) {
        return true;
      }
    }

    if (allowTailEvent) {
      const rawEvent = buffer.trim();
      if (rawEvent) {
        buffer = "";
        const parsed = parseSseEvent(rawEvent);
        if (parsed && processEvent(parsed)) {
          return true;
        }
      }
    }

    return false;
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, "\n");
      if (consumeBuffer(false)) {
        return;
      }
    }

    buffer += decoder.decode();
    buffer = buffer.replace(/\r\n/g, "\n");
    if (consumeBuffer(true)) {
      return;
    }
  } finally {
    reader.releaseLock();
  }

  if (streamErrorMessage) {
    throw new Error(streamErrorMessage);
  }

  const incompleteMessage = "流式连接异常中断（未收到 done 事件）";
  emitStreamError(incompleteMessage, undefined, "stream_incomplete");
  throw new Error(incompleteMessage);
}

export async function fetchChatModelConfig(): Promise<ChatModelConfig> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
  const response = await fetch(`${baseUrl}/api/v1/chat/models`, {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`模型配置请求失败：HTTP ${response.status}`);
  }

  const payload = (await response.json()) as ChatModelConfigApiResponse;
  if (!payload.success || !payload.data) {
    throw new Error(payload.error ?? "模型配置返回异常");
  }

  return payload.data;
}

export async function createChatSession(source: "browser" | "lark" = "browser"): Promise<ChatSession> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
  const response = await fetch(`${baseUrl}/api/v1/chat/sessions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify({ source }),
  });

  if (!response.ok) {
    throw new Error(`创建会话失败：HTTP ${response.status}`);
  }

  const payload = (await response.json()) as ChatSessionApiResponse;
  if (!payload.success || !payload.data) {
    throw new Error(payload.error ?? "创建会话返回异常");
  }

  return payload.data;
}

export async function fetchChatSessions(): Promise<ChatSession[]> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
  const response = await fetch(`${baseUrl}/api/v1/chat/sessions`, {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`会话列表请求失败：HTTP ${response.status}`);
  }

  const payload = (await response.json()) as {
    success: boolean;
    data: ChatSession[];
    error: string | null;
  };

  if (!payload.success) {
    throw new Error(payload.error ?? "会话列表返回异常");
  }

  return payload.data ?? [];
}

export async function fetchChatSessionMessages(sessionId: string): Promise<ChatMessage[]> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
  const endpoint = `${baseUrl}/api/v1/chat/sessions/${sessionId}/messages`;
  const response = await fetch(endpoint, {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`加载历史消息失败：GET ${endpoint} -> HTTP ${response.status}`);
  }

  const payload = (await response.json()) as ChatMessageListApiResponse;
  if (!payload.success) {
    throw new Error(payload.error ?? "历史消息返回异常");
  }

  return payload.data;
}

export async function deleteChatSessionMessage(
  sessionId: string,
  messageId: string,
): Promise<void> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
  const response = await fetch(`${baseUrl}/api/v1/chat/sessions/${sessionId}/messages/${messageId}`, {
    method: "DELETE",
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`删除消息失败：HTTP ${response.status}`);
  }

  const payload = (await response.json()) as {
    success: boolean;
    data: { deleted: boolean } | null;
    error: string | null;
  };

  if (!payload.success) {
    throw new Error(payload.error ?? "删除消息失败");
  }
}

export async function clearChatSessionMessages(sessionId: string): Promise<number> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
  const response = await fetch(`${baseUrl}/api/v1/chat/sessions/${sessionId}/messages`, {
    method: "DELETE",
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`清空消息失败：HTTP ${response.status}`);
  }

  const payload = (await response.json()) as {
    success: boolean;
    data: { deletedCount?: number } | null;
    error: string | null;
  };

  if (!payload.success) {
    throw new Error(payload.error ?? "清空消息失败");
  }

  return payload.data?.deletedCount ?? 0;
}

export async function fetchPlatformSystemPrompt(): Promise<string> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
  const response = await fetch(`${baseUrl}/api/v1/chat/system-prompt`, {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`加载平台提示词失败：HTTP ${response.status}`);
  }

  const payload = (await response.json()) as SystemPromptApiResponse;
  if (!payload.success || !payload.data?.platformPrompt) {
    throw new Error(payload.error ?? "平台提示词返回异常");
  }

  return payload.data.platformPrompt;
}

export async function fetchMcpServers(): Promise<ChatMcpServer[]> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
  const response = await fetch(`${baseUrl}/api/v1/mcp/servers`, {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`加载 MCP Server 列表失败：HTTP ${response.status}`);
  }

  const payload = (await response.json()) as McpServerListApiResponse;
  if (!payload.success) {
    throw new Error(payload.error ?? "MCP Server 列表返回异常");
  }
  return payload.data;
}

export async function createMcpServer(input: CreateMcpServerInput): Promise<ChatMcpServer> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
  const response = await fetch(`${baseUrl}/api/v1/mcp/servers`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(input),
  });

  if (!response.ok) {
    throw new Error(`新增 MCP Server 失败：HTTP ${response.status}`);
  }

  const payload = (await response.json()) as McpServerApiResponse;
  if (!payload.success || !payload.data) {
    throw new Error(payload.error ?? "新增 MCP Server 返回异常");
  }
  return payload.data;
}

export async function updateMcpServer(
  serverId: string,
  input: UpdateMcpServerInput,
): Promise<ChatMcpServer> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
  const response = await fetch(`${baseUrl}/api/v1/mcp/servers/${serverId}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(input),
  });

  if (!response.ok) {
    throw new Error(`更新 MCP Server 失败：HTTP ${response.status}`);
  }

  const payload = (await response.json()) as McpServerApiResponse;
  if (!payload.success || !payload.data) {
    throw new Error(payload.error ?? "更新 MCP Server 返回异常");
  }
  return payload.data;
}

export async function deleteMcpServer(serverId: string): Promise<ChatMcpServer[]> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
  const response = await fetch(`${baseUrl}/api/v1/mcp/servers/${serverId}`, {
    method: "DELETE",
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`删除 MCP Server 失败：HTTP ${response.status}`);
  }

  const payload = (await response.json()) as McpServerListApiResponse;
  if (!payload.success) {
    throw new Error(payload.error ?? "删除 MCP Server 返回异常");
  }
  return payload.data;
}

export async function discoverMcpServerTools(serverId: string): Promise<ChatMcpDiscoveredTool[]> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
  const response = await fetch(`${baseUrl}/api/v1/mcp/servers/${serverId}/discover`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify({ timeoutSeconds: 30 }),
  });

  if (!response.ok) {
    throw new Error(`MCP discover 失败：HTTP ${response.status}`);
  }

  const payload = (await response.json()) as McpDiscoveredToolListApiResponse;
  if (!payload.success) {
    throw new Error(payload.error ?? "MCP discover 返回异常");
  }
  return payload.data;
}

export async function backfillMcpServerProfiles(
  options: { force?: boolean; limit?: number } = {},
): Promise<ChatMcpServer[]> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
  const force = options.force ? "true" : "false";
  const limit = typeof options.limit === "number" ? String(options.limit) : "50";
  const response = await fetch(
    `${baseUrl}/api/v1/mcp/servers/profile/backfill?force=${force}&limit=${encodeURIComponent(limit)}`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
      },
    },
  );

  if (!response.ok) {
    throw new Error(`补齐 MCP Profile 失败：HTTP ${response.status}`);
  }

  const payload = (await response.json()) as McpServerListApiResponse;
  if (!payload.success) {
    throw new Error(payload.error ?? "补齐 MCP Profile 返回异常");
  }

  return payload.data;
}

export async function fetchMcpCatalog(): Promise<ChatMcpCatalogItem[]> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
  const response = await fetch(`${baseUrl}/api/v1/mcp/catalog`, {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`加载工具列表失败：HTTP ${response.status}`);
  }

  const payload = (await response.json()) as McpCatalogApiResponse;
  if (!payload.success) {
    throw new Error(payload.error ?? "工具列表返回异常");
  }

  return payload.data;
}

// Backward-compatible alias.
export const fetchToolCatalog = fetchMcpCatalog;

export async function fetchMcpRoutingDashboard(days = 7): Promise<ChatRoutingDashboard> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
  const response = await fetch(`${baseUrl}/api/v1/runtime/mcp-routing/dashboard?days=${days}`, {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`加载 MCP 路由看板失败：HTTP ${response.status}`);
  }

  const payload = (await response.json()) as RuntimeRoutingDashboardApiResponse;
  if (!payload.success || !payload.data) {
    throw new Error(payload.error ?? "MCP 路由看板返回异常");
  }
  return payload.data;
}

export async function fetchMcpRoutingEvents(
  days = 1,
  limit = 50,
  eventType?: string,
): Promise<ChatRoutingEvent[]> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
  const query = new URLSearchParams({
    days: String(days),
    limit: String(limit),
  });
  if (eventType?.trim()) {
    query.set("eventType", eventType.trim());
  }
  const response = await fetch(`${baseUrl}/api/v1/runtime/mcp-routing/events?${query.toString()}`, {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`加载 MCP 路由事件失败：HTTP ${response.status}`);
  }

  const payload = (await response.json()) as RuntimeRoutingEventListApiResponse;
  if (!payload.success) {
    throw new Error(payload.error ?? "MCP 路由事件返回异常");
  }
  return payload.data ?? [];
}

export async function purgeLegacyMcpRoutingEvents(): Promise<number> {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
  const response = await fetch(`${baseUrl}/api/v1/runtime/mcp-routing/legacy`, {
    method: "DELETE",
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`清空 legacy 路由事件失败：HTTP ${response.status}`);
  }

  const payload = (await response.json()) as {
    success: boolean;
    data: { deletedCount?: number } | null;
    error: string | null;
  };

  if (!payload.success) {
    throw new Error(payload.error ?? "清空 legacy 路由事件失败");
  }

  return payload.data?.deletedCount ?? 0;
}
