import type { WorkspaceModel, WorkspaceModelInput } from "@/features/models/types/model";

const DEFAULT_API_BASE_URL = "http://localhost:8000";

function resolveApiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL;
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
    // fallback
  }
  return `${fallback}：HTTP ${response.status}`;
}

type ApiResponse<T> = {
  success: boolean;
  data: T;
  error: string | null;
};

export async function fetchModels(): Promise<WorkspaceModel[]> {
  const baseUrl = resolveApiBaseUrl();
  const response = await fetch(`${baseUrl}/api/v1/models`);
  if (!response.ok) {
    if (response.status === 404) {
      throw new Error("后端缺少 /api/v1/models 接口，请重启并使用最新 BFF 代码");
    }
    throw new Error(await readErrorMessage(response, "加载模型失败"));
  }
  const payload = (await response.json()) as ApiResponse<WorkspaceModel[]>;
  if (!payload.success) {
    throw new Error(payload.error ?? "加载模型失败");
  }
  return payload.data ?? [];
}

export async function fetchActiveModel(): Promise<WorkspaceModel | null> {
  const baseUrl = resolveApiBaseUrl();
  const response = await fetch(`${baseUrl}/api/v1/models/active`);
  if (!response.ok) {
    if (response.status === 404) {
      throw new Error("后端缺少 /api/v1/models/active 接口，请重启并使用最新 BFF 代码");
    }
    throw new Error(await readErrorMessage(response, "加载当前模型失败"));
  }
  const payload = (await response.json()) as ApiResponse<WorkspaceModel | null>;
  if (!payload.success) {
    throw new Error(payload.error ?? "加载当前模型失败");
  }
  return payload.data ?? null;
}

export async function createModel(input: WorkspaceModelInput): Promise<WorkspaceModel> {
  const baseUrl = resolveApiBaseUrl();
  const response = await fetch(`${baseUrl}/api/v1/models`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, "新增模型失败"));
  }
  const payload = (await response.json()) as ApiResponse<WorkspaceModel>;
  if (!payload.success || !payload.data) {
    throw new Error(payload.error ?? "新增模型失败");
  }
  return payload.data;
}

export async function updateModel(modelId: string, input: WorkspaceModelInput): Promise<WorkspaceModel> {
  const baseUrl = resolveApiBaseUrl();
  const response = await fetch(`${baseUrl}/api/v1/models/${modelId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, "更新模型失败"));
  }
  const payload = (await response.json()) as ApiResponse<WorkspaceModel>;
  if (!payload.success || !payload.data) {
    throw new Error(payload.error ?? "更新模型失败");
  }
  return payload.data;
}

export async function deleteModel(modelId: string): Promise<void> {
  const baseUrl = resolveApiBaseUrl();
  const response = await fetch(`${baseUrl}/api/v1/models/${modelId}`, {
    method: "DELETE",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, "删除模型失败"));
  }
  const payload = (await response.json()) as ApiResponse<{ deleted: boolean }>;
  if (!payload.success) {
    throw new Error(payload.error ?? "删除模型失败");
  }
}

export async function setActiveModel(modelId: string): Promise<WorkspaceModel> {
  const baseUrl = resolveApiBaseUrl();
  const response = await fetch(`${baseUrl}/api/v1/models/active/${modelId}`, {
    method: "PUT",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, "设置当前模型失败"));
  }
  const payload = (await response.json()) as ApiResponse<WorkspaceModel>;
  if (!payload.success || !payload.data) {
    throw new Error(payload.error ?? "设置当前模型失败");
  }
  return payload.data;
}
