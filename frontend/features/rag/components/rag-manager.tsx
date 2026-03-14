"use client";

import { Database, RefreshCcw, Search, Trash2, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  clearRagSources,
  deleteRagSources,
  fetchRagSources,
  fetchRagStats,
  indexRagPaths,
  searchRag,
  uploadRagFiles,
  type RagSearchResult,
  type RagSourceItem,
  type RagStatsResult,
} from "@/features/rag/services/rag-api";
import { WorkspacePageHeader } from "@/shared/components/layout/workspace-page-header";

function prettyStats(stats: RagStatsResult | null): string {
  if (!stats) {
    return "{}";
  }
  return JSON.stringify(stats, null, 2);
}

export function RagManager() {
  const [stats, setStats] = useState<RagStatsResult | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [isRefreshingStats, setIsRefreshingStats] = useState(false);

  const [sources, setSources] = useState<RagSourceItem[]>([]);
  const [sourcesError, setSourcesError] = useState<string | null>(null);
  const [isRefreshingSources, setIsRefreshingSources] = useState(false);
  const [isDeletingPath, setIsDeletingPath] = useState<string | null>(null);
  const [isClearing, setIsClearing] = useState(false);
  const [deletePhysicalFiles, setDeletePhysicalFiles] = useState(false);
  const [clearConfirmInput, setClearConfirmInput] = useState("");

  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadResult, setUploadResult] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);

  const [pathsInput, setPathsInput] = useState("");
  const [forceReindex, setForceReindex] = useState(false);
  const [indexResult, setIndexResult] = useState<string | null>(null);
  const [indexError, setIndexError] = useState<string | null>(null);
  const [isIndexing, setIsIndexing] = useState(false);

  const [query, setQuery] = useState("");
  const [topKInput, setTopKInput] = useState("8");
  const [withRerank, setWithRerank] = useState(true);
  const [searchResult, setSearchResult] = useState<RagSearchResult | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [isSearching, setIsSearching] = useState(false);

  const selectedFileNames = useMemo(
    () => selectedFiles.map((file) => file.name).join(", "),
    [selectedFiles],
  );

  const topK = useMemo(() => {
    const parsed = Number(topKInput);
    if (!Number.isFinite(parsed)) {
      return 8;
    }
    return Math.max(1, Math.min(20, Math.floor(parsed)));
  }, [topKInput]);

  const clearAllowed = clearConfirmInput.trim().toUpperCase() === "CLEAR";

  const loadStats = async () => {
    setIsRefreshingStats(true);
    setStatsError(null);
    try {
      const data = await fetchRagStats();
      setStats(data);
    } catch (error) {
      setStatsError(error instanceof Error ? error.message : "加载统计失败");
    } finally {
      setIsRefreshingStats(false);
    }
  };

  const loadSources = async () => {
    setIsRefreshingSources(true);
    setSourcesError(null);
    try {
      const data = await fetchRagSources();
      setSources(data.sources);
    } catch (error) {
      setSourcesError(error instanceof Error ? error.message : "加载文件列表失败");
    } finally {
      setIsRefreshingSources(false);
    }
  };

  const refreshAll = async () => {
    await Promise.all([loadStats(), loadSources()]);
  };

  useEffect(() => {
    void refreshAll();
  }, []);

  const handleUpload = async () => {
    setUploadError(null);
    setUploadResult(null);
    setIndexError(null);
    setIndexResult(null);
    if (selectedFiles.length === 0) {
      setUploadError("请先选择文件");
      return;
    }
    setIsUploading(true);
    try {
      const upload = await uploadRagFiles(selectedFiles);
      setUploadResult(`上传成功 ${upload.savedPaths.length} 个文件`);

      const indexed = await indexRagPaths(upload.savedPaths, false);
      setIndexResult(
        `索引完成：indexed=${indexed.summary.indexed}, skipped=${indexed.summary.skipped}, failed=${indexed.summary.failed}`,
      );
      setPathsInput(upload.savedPaths.join("\n"));
      await refreshAll();
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "上传失败");
    } finally {
      setIsUploading(false);
    }
  };

  const handleIndexPaths = async () => {
    setIndexError(null);
    setIndexResult(null);
    const paths = pathsInput
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0);
    if (paths.length === 0) {
      setIndexError("请至少输入一个路径");
      return;
    }
    setIsIndexing(true);
    try {
      const result = await indexRagPaths(paths, forceReindex);
      setIndexResult(
        `索引完成：resolved=${result.summary.resolved_files}, indexed=${result.summary.indexed}, skipped=${result.summary.skipped}, failed=${result.summary.failed}`,
      );
      await refreshAll();
    } catch (error) {
      setIndexError(error instanceof Error ? error.message : "索引失败");
    } finally {
      setIsIndexing(false);
    }
  };

  const handleSearch = async () => {
    setSearchError(null);
    setSearchResult(null);
    if (!query.trim()) {
      setSearchError("请输入查询内容");
      return;
    }
    setIsSearching(true);
    try {
      const result = await searchRag(query.trim(), topK, withRerank);
      setSearchResult(result);
    } catch (error) {
      setSearchError(error instanceof Error ? error.message : "检索失败");
    } finally {
      setIsSearching(false);
    }
  };

  const handleDeleteSource = async (path: string) => {
    setSourcesError(null);
    setIsDeletingPath(path);
    try {
      await deleteRagSources([path], deletePhysicalFiles);
      await refreshAll();
    } catch (error) {
      setSourcesError(error instanceof Error ? error.message : "删除失败");
    } finally {
      setIsDeletingPath(null);
    }
  };

  const handleClearAll = async () => {
    if (!clearAllowed) {
      return;
    }
    setSourcesError(null);
    setIsClearing(true);
    try {
      await clearRagSources(deletePhysicalFiles);
      setClearConfirmInput("");
      await refreshAll();
    } catch (error) {
      setSourcesError(error instanceof Error ? error.message : "清空失败");
    } finally {
      setIsClearing(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-6xl">
      <div className="apple-surface overflow-hidden">
        <div className="p-6 md:p-7">
          <div className="flex items-start justify-between gap-3">
            <WorkspacePageHeader
              title="Rag"
              description="上传知识文件、建立索引、检索与管理已入库文件。"
              icon={Database}
            />
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8"
              onClick={() => void refreshAll()}
              disabled={isRefreshingStats || isRefreshingSources}
              aria-label="刷新全部"
              title="刷新全部"
            >
              <RefreshCcw className={`h-4 w-4 ${(isRefreshingStats || isRefreshingSources) ? "animate-spin" : ""}`} />
            </Button>
          </div>
        </div>

        <div className="grid gap-6 border-t border-slate-200/80 p-6 md:p-7">
          <Card>
            <CardHeader>
              <CardTitle>索引统计</CardTitle>
              <CardDescription>当前知识库概览</CardDescription>
            </CardHeader>
            <CardContent>
              <pre className="overflow-x-auto rounded-md bg-slate-50 p-3 text-xs text-slate-700">{prettyStats(stats)}</pre>
              {statsError ? <p className="mt-2 text-xs text-red-600">{statsError}</p> : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>上传并入库</CardTitle>
              <CardDescription>支持 txt/md/pdf/docx/py/json/yaml/yml</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(event) => setSelectedFiles(Array.from(event.target.files ?? []))}
              />
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="outline"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={isUploading}
                >
                  选择文件
                </Button>
                <Button onClick={() => void handleUpload()} disabled={isUploading}>
                  <Upload className="h-4 w-4" />
                  {isUploading ? "处理中..." : "上传并索引"}
                </Button>
              </div>
              <p className="text-xs text-slate-600">
                {selectedFileNames || "未选择文件"}
              </p>
              {uploadResult ? <p className="text-xs text-emerald-700">{uploadResult}</p> : null}
              {uploadError ? <p className="text-xs text-red-600">{uploadError}</p> : null}
              {indexResult ? <p className="text-xs text-emerald-700">{indexResult}</p> : null}
              {indexError ? <p className="text-xs text-red-600">{indexError}</p> : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>路径索引</CardTitle>
              <CardDescription>每行一个文件或目录绝对路径</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Textarea
                value={pathsInput}
                onChange={(event) => setPathsInput(event.target.value)}
                className="h-28 text-xs"
                placeholder="E:\\path\\to\\docs"
              />
              <div className="flex flex-wrap items-center gap-3">
                <Checkbox
                  checked={forceReindex}
                  onChange={(event) => setForceReindex(event.target.checked)}
                  label="强制重建索引"
                />
                <Button variant="outline" onClick={() => void handleIndexPaths()} disabled={isIndexing}>
                  {isIndexing ? "索引中..." : "执行路径索引"}
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>知识库文件管理</CardTitle>
              <CardDescription>删除单个已入库文件或一键清空索引</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Checkbox
                checked={deletePhysicalFiles}
                onChange={(event) => setDeletePhysicalFiles(event.target.checked)}
                label="同时删除本地上传文件（危险）"
              />
              <div className="overflow-x-auto rounded-md border border-slate-200">
                <table className="w-full min-w-[700px] text-left text-xs">
                  <thead className="bg-slate-50 text-slate-600">
                    <tr>
                      <th className="px-3 py-2 font-medium">文件</th>
                      <th className="px-3 py-2 font-medium">chunks</th>
                      <th className="px-3 py-2 font-medium">版本</th>
                      <th className="px-3 py-2 font-medium">时间</th>
                      <th className="px-3 py-2 font-medium text-right">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sources.map((item) => (
                      <tr key={item.path} className="border-t border-slate-100">
                        <td className="max-w-[460px] truncate px-3 py-2 text-slate-700" title={item.path}>
                          {item.path}
                        </td>
                        <td className="px-3 py-2 text-slate-600">{item.chunk_count}</td>
                        <td className="px-3 py-2 text-slate-600">{item.version}</td>
                        <td className="px-3 py-2 text-slate-500">{item.last_indexed_at}</td>
                        <td className="px-3 py-2 text-right">
                          <Button
                            variant="destructive"
                            size="sm"
                            disabled={isDeletingPath === item.path || isClearing}
                            onClick={() => void handleDeleteSource(item.path)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                            {isDeletingPath === item.path ? "删除中" : "删除"}
                          </Button>
                        </td>
                      </tr>
                    ))}
                    {sources.length === 0 ? (
                      <tr>
                        <td className="px-3 py-4 text-slate-500" colSpan={5}>
                          暂无已入库文件
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>

              <div className="rounded-md border border-red-200 bg-red-50 p-3">
                <p className="text-xs text-red-700">输入 CLEAR 后才允许清空全部知识库索引。</p>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <Input
                    value={clearConfirmInput}
                    onChange={(event) => setClearConfirmInput(event.target.value)}
                    placeholder="输入 CLEAR"
                    className="max-w-[220px]"
                  />
                  <Button
                    variant="destructive"
                    disabled={!clearAllowed || isClearing}
                    onClick={() => void handleClearAll()}
                  >
                    <Trash2 className="h-4 w-4" />
                    {isClearing ? "清空中..." : "清空全部"}
                  </Button>
                </div>
              </div>

              {sourcesError ? <p className="text-xs text-red-600">{sourcesError}</p> : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>检索测试</CardTitle>
              <CardDescription>快速验证召回质量与来源片段</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap gap-2">
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="输入问题或关键词"
                  className="min-w-[260px] flex-1"
                />
                <Input
                  type="number"
                  min={1}
                  max={20}
                  value={topKInput}
                  onChange={(event) => setTopKInput(event.target.value)}
                  className="w-24"
                />
                <Checkbox
                  checked={withRerank}
                  onChange={(event) => setWithRerank(event.target.checked)}
                  label="rerank"
                />
                <Button onClick={() => void handleSearch()} disabled={isSearching}>
                  <Search className="h-4 w-4" />
                  {isSearching ? "检索中..." : "检索"}
                </Button>
              </div>

              {searchError ? <p className="text-xs text-red-600">{searchError}</p> : null}
              {searchResult ? (
                <ul className="space-y-2">
                  {searchResult.hits.map((hit) => (
                    <li key={hit.chunk_id} className="rounded-md border border-slate-200 bg-slate-50 p-3">
                      <p className="text-xs text-slate-500">
                        {hit.source_path} · chunk#{hit.chunk_index} · score={hit.score.toFixed(4)}
                      </p>
                      <p className="mt-1 whitespace-pre-wrap text-sm text-slate-800">{hit.text}</p>
                    </li>
                  ))}
                </ul>
              ) : null}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
