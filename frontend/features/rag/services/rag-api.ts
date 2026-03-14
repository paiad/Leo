const DEFAULT_API_BASE_URL = "http://localhost:8000";

type ApiResponse<T> = {
  success: boolean;
  data: T;
  error: string | null;
};

type RagUploadResult = {
  savedPaths: string[];
  uploadDir: string;
};

type RagIndexResult = {
  summary: {
    requested_paths: string[];
    resolved_files: number;
    indexed: number;
    skipped: number;
    failed: number;
  };
  items: Array<Record<string, unknown>>;
};

type RagSearchResult = {
  query: string;
  top_k: number;
  hits: Array<{
    chunk_id: string;
    score: number;
    text: string;
    source_path: string;
    chunk_index: number;
    version: number;
    token_count: number;
    rerank_score?: number;
  }>;
  debug: Record<string, unknown>;
};

type RagStatsResult = Record<string, unknown>;

type RagSourceItem = {
  source_id: number;
  path: string;
  version: number;
  checksum: string;
  last_indexed_at: string;
  chunk_count: number;
};

type RagSourcesResult = {
  sources: RagSourceItem[];
};

type RagDeleteResult = {
  requested: string[];
  deleted_paths: string[];
  not_found: string[];
  deleted_count: number;
  deleted_files: string[];
  file_delete_failed: Array<{ path: string; error: string }>;
  before_exists: string[];
};

type RagClearResult = {
  total_before: number;
  cleared: number;
  deleted_paths: string[];
  deleted_files: string[];
  file_delete_failed: Array<{ path: string; error: string }>;
};

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
    // ignore
  }
  return `${fallback}：HTTP ${response.status}`;
}

export async function uploadRagFiles(files: File[]): Promise<RagUploadResult> {
  if (files.length === 0) {
    throw new Error("请先选择文件");
  }
  const baseUrl = resolveApiBaseUrl();
  const formData = new FormData();
  for (const file of files) {
    formData.append("files", file);
  }
  const response = await fetch(`${baseUrl}/api/v1/rag/upload`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, "上传失败"));
  }
  const payload = (await response.json()) as ApiResponse<RagUploadResult>;
  if (!payload.success || !payload.data) {
    throw new Error(payload.error ?? "上传失败");
  }
  return payload.data;
}

export async function indexRagPaths(paths: string[], forceReindex = false): Promise<RagIndexResult> {
  const baseUrl = resolveApiBaseUrl();
  const response = await fetch(`${baseUrl}/api/v1/rag/index`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ paths, forceReindex }),
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, "索引失败"));
  }
  const payload = (await response.json()) as ApiResponse<RagIndexResult>;
  if (!payload.success || !payload.data) {
    throw new Error(payload.error ?? "索引失败");
  }
  return payload.data;
}

export async function searchRag(
  query: string,
  topK = 8,
  withRerank = true,
): Promise<RagSearchResult> {
  const baseUrl = resolveApiBaseUrl();
  const response = await fetch(`${baseUrl}/api/v1/rag/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ query, topK, withRerank }),
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, "检索失败"));
  }
  const payload = (await response.json()) as ApiResponse<RagSearchResult>;
  if (!payload.success || !payload.data) {
    throw new Error(payload.error ?? "检索失败");
  }
  return payload.data;
}

export async function fetchRagStats(): Promise<RagStatsResult> {
  const baseUrl = resolveApiBaseUrl();
  const response = await fetch(`${baseUrl}/api/v1/rag/stats`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, "加载统计失败"));
  }
  const payload = (await response.json()) as ApiResponse<RagStatsResult>;
  if (!payload.success || !payload.data) {
    throw new Error(payload.error ?? "加载统计失败");
  }
  return payload.data;
}

export async function fetchRagSources(): Promise<RagSourcesResult> {
  const baseUrl = resolveApiBaseUrl();
  const response = await fetch(`${baseUrl}/api/v1/rag/sources`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, "加载文件列表失败"));
  }
  const payload = (await response.json()) as ApiResponse<RagSourcesResult>;
  if (!payload.success || !payload.data) {
    throw new Error(payload.error ?? "加载文件列表失败");
  }
  return payload.data;
}

export async function deleteRagSources(paths: string[], deleteFiles = false): Promise<RagDeleteResult> {
  const baseUrl = resolveApiBaseUrl();
  const response = await fetch(`${baseUrl}/api/v1/rag/delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ paths, deleteFiles }),
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, "删除失败"));
  }
  const payload = (await response.json()) as ApiResponse<RagDeleteResult>;
  if (!payload.success || !payload.data) {
    throw new Error(payload.error ?? "删除失败");
  }
  return payload.data;
}

export async function clearRagSources(deleteFiles = false): Promise<RagClearResult> {
  const baseUrl = resolveApiBaseUrl();
  const response = await fetch(`${baseUrl}/api/v1/rag/clear`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ deleteFiles }),
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, "清空失败"));
  }
  const payload = (await response.json()) as ApiResponse<RagClearResult>;
  if (!payload.success || !payload.data) {
    throw new Error(payload.error ?? "清空失败");
  }
  return payload.data;
}

export type {
  RagClearResult,
  RagDeleteResult,
  RagIndexResult,
  RagSearchResult,
  RagSourceItem,
  RagSourcesResult,
  RagStatsResult,
  RagUploadResult,
};
