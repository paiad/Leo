export type ChatRole = "system" | "user" | "assistant";

export type ChatMessage = {
  id: string;
  role: ChatRole;
  content: string;
  createdAt: string;
  userInputType?: "text" | "audio_asr";
  model?: string;
  toolEvents?: ChatToolEvent[];
  decisionEvents?: ChatDecisionEvent[];
  timelineEvents?: ChatTimelineEvent[];
};

export type ChatTimelineEvent = {
  id: string;
  phase: "progress" | "tool_start" | "tool_done" | "reply_start" | "reply_done";
  text: string;
  createdAt: string;
  status?: "running" | "success" | "failed";
};

export type ChatToolTransportType = "stdio" | "http" | "sse" | "streamablehttp";

export type ChatMcpCatalogItem = {
  toolId: string;
  name: string;
  type: ChatToolTransportType;
  command?: string | null;
  args: string[];
  url?: string | null;
  description: string;
  enabled: boolean;
};

export type ChatToolCatalogItem = ChatMcpCatalogItem;

export type ChatMcpDiscoveredTool = {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  enabled: boolean;
};

export type ChatMcpServer = {
  serverId: string;
  name: string;
  type: ChatToolTransportType;
  command?: string | null;
  args: string[];
  env: Record<string, string>;
  url?: string | null;
  description: string;
  enabled: boolean;
  discoveredTools: ChatMcpDiscoveredTool[];
};

export type ChatToolEvent = {
  toolId?: string;
  toolName: string;
  status: "started" | "success" | "failed" | "ignored";
  latencyMs?: number;
  inputPreview?: string;
  outputJson?: unknown;
  outputPreview?: string;
  errorMessage?: string;
};

export type ChatDecisionEvent = {
  action: "invoke" | "skip" | "clarify";
  reason: string;
  confidence?: number;
  prompt?: string;
  source?: string;
};

export type ChatRuntimeConfig = {
  workspacePrompt: string;
};

export type ChatRoutingEvent = {
  id?: string;
  session_id?: string | null;
  event_type: string;
  request_preview?: string;
  prompt_hash?: string;
  intent?: string;
  selected_server_id?: string | null;
  candidate_servers?: string[];
  connected_servers?: string[];
  used_servers?: string[];
  scores?: Record<string, unknown>;
  success?: boolean | null;
  latency_ms?: number | null;
  createdAt?: string;
};

export type ChatRoutingDashboard = {
  window: {
    days: number;
    sinceIso: string;
    nowIso: string;
  };
  counts: {
    total: number;
    byEventType: Record<string, number>;
  };
  metrics: {
    routingAccuracy: number | null;
    toolSuccessRate: number | null;
    avgLatencyMs: number | null;
    fallbackTriggerRate: number | null;
  };
  daily: Array<{
    date: string;
    requests: number;
    routingAccuracy: number | null;
    toolSuccessRate: number | null;
    avgLatencyMs: number | null;
    fallbackTriggerRate: number | null;
  }>;
};
