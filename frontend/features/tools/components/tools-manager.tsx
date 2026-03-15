"use client";

import { RefreshCcw, ToggleLeft, ToggleRight, Trash2, Wrench } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  createMcpServer,
  deleteMcpServer,
  fetchMcpServers,
  updateMcpServer,
  type CreateMcpServerInput,
} from "@/features/chat/services/chat-api";
import type { ChatMcpServer, ChatToolTransportType } from "@/features/chat/types/chat";
import { WorkspacePageHeader } from "@/shared/components/layout/workspace-page-header";

const DEFAULT_IMPORT_JSON = `{
  "mcpServers": {
    "my-filesystem": {
      "type": "stdio",
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "E:\\\\Github\\\\OpenManus\\\\workspace"
      ],
      "env": {
        "EXAMPLE_TOKEN": "replace_me"
      },
      "description": "Local filesystem MCP"
    }
  }
}`;

type RawMcpServerConfig = {
  serverId?: string;
  name?: string;
  type?: string;
  command?: string;
  args?: unknown;
  env?: unknown;
  headers?: unknown;
  url?: string;
  description?: string;
  enabled?: boolean;
};

const MCP_PURPOSE_ZH: Record<string, string> = {
  "leo-local": "本地内置工具集，提供命令执行、文件编辑和基础浏览器自动化能力。",
  playwright: "浏览器自动化工具，适合网页登录、点击操作、截图和页面流程执行。",
  trendradar: "趋势与热点分析工具，可用于抓取和分析新闻、社媒热点与舆情。",
  fetch: "网页内容抓取工具，适合快速读取链接正文并给模型做总结分析。",
  context7: "开发文档上下文工具，提供最新 API/SDK 文档检索与代码示例参考。",
  exa: "高质量搜索工具，支持网页搜索、代码上下文检索和公司研究。",
  github: "GitHub 协作工具，可读写仓库、Issue、PR、分支和提交信息。",
};

function isTransportType(value: unknown): value is ChatToolTransportType {
  return value === "stdio" || value === "http" || value === "sse" || value === "streamablehttp";
}

function ensureObject(value: unknown, message: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(message);
  }
  return value as Record<string, unknown>;
}

function normalizeStringMap(value: unknown): Record<string, string> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  const normalized = Object.fromEntries(
    Object.entries(value as Record<string, unknown>).filter(
      (entry): entry is [string, string] =>
        typeof entry[0] === "string" && typeof entry[1] === "string",
    ),
  );
  return Object.keys(normalized).length > 0 ? normalized : undefined;
}

function normalizeServerConfig(
  fallbackServerId: string,
  raw: RawMcpServerConfig,
): CreateMcpServerInput {
  const type = raw.type;
  if (!isTransportType(type)) {
    throw new Error(`${fallbackServerId}: type 必须是 stdio/http/sse/streamablehttp`);
  }

  const serverId = typeof raw.serverId === "string" && raw.serverId.trim() ? raw.serverId.trim() : fallbackServerId;
  const name = typeof raw.name === "string" && raw.name.trim() ? raw.name.trim() : serverId;
  const args = Array.isArray(raw.args)
    ? raw.args.filter((item): item is string => typeof item === "string")
    : [];
  const parsedEnv = normalizeStringMap(raw.env);
  const parsedHeaders = normalizeStringMap(raw.headers);
  const env = parsedEnv || parsedHeaders ? { ...(parsedHeaders ?? {}), ...(parsedEnv ?? {}) } : undefined;

  return {
    serverId,
    name,
    type,
    command: typeof raw.command === "string" && raw.command.trim() ? raw.command.trim() : undefined,
    args,
    env,
    url: typeof raw.url === "string" && raw.url.trim() ? raw.url.trim() : undefined,
    description:
      typeof raw.description === "string" && raw.description.trim()
        ? raw.description.trim()
        : "",
    enabled: typeof raw.enabled === "boolean" ? raw.enabled : true,
  };
}

function parseImportPayload(raw: string): CreateMcpServerInput[] {
  let payload: unknown;
  try {
    payload = JSON.parse(raw);
  } catch {
    throw new Error("JSON 解析失败，请检查格式");
  }

  const payloadObject = ensureObject(payload, "导入内容必须是对象");
  const mcpServers = ensureObject(payloadObject.mcpServers, "缺少 mcpServers 对象");

  const result: CreateMcpServerInput[] = [];
  for (const [serverId, value] of Object.entries(mcpServers)) {
    const configObject = ensureObject(value, `${serverId}: 配置必须是对象`);
    result.push(normalizeServerConfig(serverId, configObject as RawMcpServerConfig));
  }

  if (result.length === 0) {
    throw new Error("mcpServers 不能为空");
  }
  return result;
}

export function McpManager() {
  const [servers, setServers] = useState<ChatMcpServer[]>([]);
  const [importJson, setImportJson] = useState(DEFAULT_IMPORT_JSON);
  const [importError, setImportError] = useState<string | null>(null);
  const [importResult, setImportResult] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [isImporting, setIsImporting] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [deletingServerId, setDeletingServerId] = useState<string | null>(null);
  const [togglingServerId, setTogglingServerId] = useState<string | null>(null);
  const importTextareaRef = useRef<HTMLTextAreaElement | null>(null);

  const loadServers = async () => {
    const data = await fetchMcpServers();
    setServers(data);
  };

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await fetchMcpServers();
        if (cancelled) {
          return;
        }
        setServers(data);
      } catch (error) {
        if (cancelled) {
          return;
        }
        setListError(error instanceof Error ? error.message : "加载 MCP 列表失败");
      }
    };
    void load();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const textarea = importTextareaRef.current;
    if (!textarea) {
      return;
    }
    textarea.style.height = "auto";
    textarea.style.height = `${textarea.scrollHeight}px`;
  }, [importJson]);

  const handleRefresh = async () => {
    setListError(null);
    setIsRefreshing(true);
    try {
      await loadServers();
    } catch (error) {
      setListError(error instanceof Error ? error.message : "刷新 MCP 列表失败");
    } finally {
      setIsRefreshing(false);
    }
  };

  const handleImport = async () => {
    setImportError(null);
    setImportResult(null);
    setListError(null);
    setIsImporting(true);

    try {
      const inputs = parseImportPayload(importJson);
      let successCount = 0;
      const failed: string[] = [];

      for (const input of inputs) {
        const identity = input.serverId || input.name || "unknown";
        try {
          await createMcpServer(input);
          successCount += 1;
        } catch (error) {
          const message = error instanceof Error ? error.message : "创建失败";
          failed.push(`${identity}: ${message}`);
        }
      }

      await loadServers();

      if (failed.length > 0) {
        setImportError(`部分导入失败：\n${failed.join("\n")}`);
      }
      setImportResult(`导入完成：成功 ${successCount} 个，失败 ${failed.length} 个`);
    } catch (error) {
      setImportError(error instanceof Error ? error.message : "导入失败");
    } finally {
      setIsImporting(false);
    }
  };

  const handleDelete = async (serverId: string) => {
    setDeletingServerId(serverId);
    setListError(null);
    try {
      const next = await deleteMcpServer(serverId);
      setServers(next);
    } catch (error) {
      setListError(error instanceof Error ? error.message : "删除 MCP 失败");
    } finally {
      setDeletingServerId(null);
    }
  };

  const handleToggleEnabled = async (server: ChatMcpServer) => {
    setTogglingServerId(server.serverId);
    setListError(null);
    try {
      const updated = await updateMcpServer(server.serverId, {
        enabled: !server.enabled,
      });
      setServers((prev) =>
        prev.map((item) => (item.serverId === updated.serverId ? updated : item)),
      );
    } catch (error) {
      setListError(error instanceof Error ? error.message : "更新 MCP 状态失败");
    } finally {
      setTogglingServerId(null);
    }
  };

  const getPurposeText = (server: ChatMcpServer): string | null => {
    const preset = MCP_PURPOSE_ZH[server.serverId];
    if (preset) {
      return preset;
    }
    if (server.description?.trim()) {
      return server.description.trim();
    }
    return null;
  };

  return (
    <div className="mx-auto w-full max-w-6xl">
      <div className="apple-surface overflow-hidden">
        <div className="p-6 md:p-7">
          <WorkspacePageHeader
            title="MCP"
            description="粘贴 mcpServers JSON 并导入，统一管理 MCP Server 的启用状态。"
            icon={Wrench}
          />
        </div>
        <div className="space-y-6 border-t border-slate-200/80 p-6 md:p-7">
      <section className="rounded-2xl border border-slate-200/80 bg-white p-5">
        <h2 className="text-sm font-semibold tracking-wide text-slate-800">导入 JSON</h2>
        <textarea
          ref={importTextareaRef}
          value={importJson}
          onChange={(event) => setImportJson(event.target.value)}
          className="mt-3 w-full overflow-hidden rounded-2xl border border-slate-200/80 bg-white px-3 py-2.5 font-mono text-xs outline-none focus:border-slate-300"
          placeholder='{"mcpServers": {...}}'
        />
        <div className="mt-3 flex items-center gap-2">
          <button
            type="button"
            onClick={() => void handleImport()}
            disabled={isImporting}
            className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {isImporting ? "导入中..." : "导入 mcpServers"}
          </button>
          <button
            type="button"
            onClick={() => setImportJson(DEFAULT_IMPORT_JSON)}
            className="rounded-xl border border-slate-200/80 px-4 py-2 text-sm text-slate-700"
          >
            重置示例
          </button>
        </div>
        {importResult ? (
          <p className="mt-2 whitespace-pre-wrap text-xs text-emerald-700">{importResult}</p>
        ) : null}
        {importError ? (
          <p className="mt-2 whitespace-pre-wrap text-xs text-red-600">{importError}</p>
        ) : null}
      </section>

      <section className="rounded-2xl border border-slate-200/80 bg-white p-5">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold tracking-wide text-slate-800">已添加 MCP ({servers.length})</h2>
          <button
            type="button"
            onClick={() => void handleRefresh()}
            disabled={isRefreshing}
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200/90 text-slate-700 disabled:opacity-60"
            aria-label="刷新 MCP 列表"
            title="刷新 MCP 列表"
          >
            <RefreshCcw className={`h-4 w-4 ${isRefreshing ? "animate-spin" : ""}`} />
          </button>
        </div>
        {listError ? <p className="mt-2 text-xs text-red-600">{listError}</p> : null}
        <ul className="mt-3 space-y-3">
          {servers.map((server) => (
            <li key={server.serverId} className="rounded-2xl border border-slate-200/80 bg-slate-50/60 p-3.5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-slate-900">
                    {server.name} <span className="font-normal text-slate-500">({server.serverId})</span>
                  </p>
                  <p className="mt-1 text-xs">
                    <span
                      className={`rounded px-2 py-0.5 ${
                        server.enabled
                          ? "bg-emerald-100 text-emerald-700"
                          : "bg-rose-100 text-rose-700"
                      }`}
                    >
                      {server.enabled ? "已启用" : "已禁用"}
                    </span>
                  </p>
                  <p className="mt-1 text-xs text-slate-600">
                    type: {server.type}
                    {server.command ? ` · command: ${server.command}` : ""}
                    {server.url ? ` · url: ${server.url}` : ""}
                  </p>
                  {server.args.length > 0 ? (
                    <pre className="mt-2 overflow-x-auto rounded bg-white p-2 text-[11px] text-slate-600">
                      {JSON.stringify(server.args)}
                    </pre>
                  ) : null}
                  {server.env && Object.keys(server.env).length > 0 ? (
                    <p className="mt-2 text-xs text-slate-600">
                      {server.type === "stdio" ? "env keys" : "headers/env keys"}: {Object.keys(server.env).join(", ")}
                    </p>
                  ) : null}
                  {server.description ? (
                    <p className="mt-2 text-xs text-slate-600">
                      作用：{getPurposeText(server)}
                    </p>
                  ) : null}
                  {!server.description && getPurposeText(server) ? (
                    <p className="mt-2 text-xs text-slate-600">作用：{getPurposeText(server)}</p>
                  ) : null}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void handleToggleEnabled(server)}
                    disabled={togglingServerId === server.serverId}
                    className={`inline-flex h-8 w-8 items-center justify-center rounded-lg border disabled:opacity-60 ${
                      server.enabled
                        ? "border-emerald-300 text-emerald-600"
                        : "border-rose-300 text-rose-600"
                    }`}
                    aria-label={server.enabled ? "禁用 MCP Server" : "启用 MCP Server"}
                    title={server.enabled ? "禁用 MCP Server" : "启用 MCP Server"}
                  >
                    {togglingServerId === server.serverId ? (
                      <RefreshCcw className="h-4 w-4 animate-spin" />
                    ) : server.enabled ? (
                      <ToggleRight className="h-4 w-4" />
                    ) : (
                      <ToggleLeft className="h-4 w-4" />
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleDelete(server.serverId)}
                    disabled={deletingServerId === server.serverId}
                    className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-red-200 text-red-600 disabled:opacity-60"
                    aria-label="删除 MCP Server"
                    title="删除 MCP Server"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            </li>
          ))}
          {servers.length === 0 ? (
            <li className="rounded-2xl border border-dashed border-slate-300 bg-slate-50/70 p-4 text-xs text-slate-500">
              当前没有 MCP Server，先在上方粘贴 JSON 并导入。
            </li>
          ) : null}
        </ul>
      </section>
        </div>
      </div>
    </div>
  );
}

// Backward-compatible export alias.
export const ToolsManager = McpManager;
