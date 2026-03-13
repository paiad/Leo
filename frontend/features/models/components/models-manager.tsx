"use client";

import { Cable, Pencil, Plus, Star, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useModelContext } from "@/features/models/context/model-context";
import { WorkspacePageHeader } from "@/shared/components/layout/workspace-page-header";
import type { WorkspaceModelInput } from "@/features/models/types/model";

type FormState = WorkspaceModelInput;

const EMPTY_FORM: FormState = {
  name: "",
  provider: "openai-compatible",
  baseUrl: "",
  apiKey: "",
  enabled: true,
};

export function ModelsManager() {
  const { models, activeModelId, setActiveModelId, createModel, updateModel, deleteModel } =
    useModelContext();
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const sortedModels = useMemo(
    () => [...models].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)),
    [models],
  );

  const handleSubmit = async () => {
    if (!form.name.trim()) {
      setError("模型名不能为空");
      return;
    }

    if (!form.provider.trim()) {
      setError("Provider 不能为空");
      return;
    }

    setError(null);
    try {
      if (editingId) {
        const updated = await updateModel(editingId, form);
        if (!updated) {
          setError("模型不存在或已被删除");
        }
      } else {
        await createModel(form);
      }
      setForm(EMPTY_FORM);
      setEditingId(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败，请稍后重试");
    }
  };

  const handleSwitchActiveModel = async (modelId: string) => {
    try {
      await setActiveModelId(modelId);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "切换当前模型失败");
    }
  };

  const handleDeleteModel = async (modelId: string) => {
    try {
      await deleteModel(modelId);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除模型失败");
    }
  };

  return (
    <section className="mx-auto w-full max-w-6xl">
      <div className="apple-surface overflow-hidden">
        <div className="p-5 md:p-6">
          <WorkspacePageHeader
            title="Models"
            description="统一管理可用模型，切换当前模型后会影响工作台会话模型。"
            icon={Cable}
          />
        </div>

        <div className="border-t border-slate-200/80 p-5 md:p-6">
          <div className="grid gap-0 lg:grid-cols-[1.4fr_1fr]">
            <div className="pr-0 lg:pr-5">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-semibold tracking-wide text-slate-800">模型列表</h3>
            <span className="rounded-full border border-slate-200/80 bg-white px-2.5 py-1 text-xs text-slate-500">
              {models.length} 个模型
            </span>
          </div>
          <div className="space-y-2">
            {sortedModels.map((model) => {
              const selected = model.id === activeModelId;
              return (
                <article
                  key={model.id}
                  className="relative"
                >
                  <div
                  className={`rounded-2xl border p-3.5 transition-colors ${
                    selected ? "border-slate-300 bg-slate-50/90" : "border-slate-200/80 bg-white/90"
                  }`}
                >
                  {selected ? (
                    <span className="absolute left-3 top-3 inline-flex h-4 w-4 items-center justify-center text-amber-500">
                      <Star className="h-3.5 w-3.5 fill-current" />
                    </span>
                  ) : null}
                  <div className="flex items-start justify-between gap-2 pl-6">
                    <button
                      type="button"
                      onClick={() => void handleSwitchActiveModel(model.id)}
                      className="min-w-0 text-left"
                    >
                      <p className="truncate text-[15px] font-semibold tracking-tight text-slate-900">{model.name}</p>
                      <p className="mt-1 truncate text-xs text-slate-500">
                        {model.provider} {model.baseUrl ? `| ${model.baseUrl}` : ""}
                      </p>
                    </button>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => {
                          setForm({
                            name: model.name,
                            provider: model.provider,
                            baseUrl: model.baseUrl,
                            apiKey: model.apiKey,
                            enabled: model.enabled,
                          });
                          setEditingId(model.id);
                          setError(null);
                        }}
                        className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800"
                        aria-label="编辑模型"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleDeleteModel(model.id)}
                        className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-red-50 hover:text-red-600"
                        aria-label="删除模型"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                  <div className="mt-3 flex items-center gap-2 text-xs">
                    <span
                      className={`rounded-full px-2 py-0.5 ${
                        model.enabled ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500"
                      }`}
                    >
                      {model.enabled ? "Enabled" : "Disabled"}
                    </span>
                    {selected ? (
                      <span className="rounded-full bg-slate-900 px-2 py-0.5 text-white">当前模型</span>
                    ) : null}
                  </div>
                  </div>
                </article>
              );
            })}
          </div>
        </div>

            <div className="mt-5 border-t border-slate-200/80 pt-5 lg:ml-5 lg:mt-0 lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
              <h3 className="text-sm font-semibold tracking-wide text-slate-800">{editingId ? "编辑模型" : "新增模型"}</h3>
              <div className="mt-3 space-y-3">
            <label className="block">
              <span className="text-xs text-slate-500">模型名</span>
              <input
                value={form.name}
                onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
                placeholder="例如：gpt-4o-mini"
                className="mt-1 w-full rounded-xl border border-slate-200/80 bg-white px-3 py-2.5 text-sm outline-none focus:border-slate-300"
              />
            </label>
            <label className="block">
              <span className="text-xs text-slate-500">Provider</span>
              <input
                value={form.provider}
                onChange={(event) => setForm((prev) => ({ ...prev, provider: event.target.value }))}
                placeholder="openai-compatible"
                className="mt-1 w-full rounded-xl border border-slate-200/80 bg-white px-3 py-2.5 text-sm outline-none focus:border-slate-300"
              />
            </label>
            <label className="block">
              <span className="text-xs text-slate-500">Base URL</span>
              <input
                value={form.baseUrl}
                onChange={(event) => setForm((prev) => ({ ...prev, baseUrl: event.target.value }))}
                placeholder="https://api.openai.com/v1"
                className="mt-1 w-full rounded-xl border border-slate-200/80 bg-white px-3 py-2.5 text-sm outline-none focus:border-slate-300"
              />
            </label>
            <label className="block">
              <span className="text-xs text-slate-500">API Key</span>
              <input
                type="password"
                value={form.apiKey}
                onChange={(event) => setForm((prev) => ({ ...prev, apiKey: event.target.value }))}
                placeholder="sk-..."
                className="mt-1 w-full rounded-xl border border-slate-200/80 bg-white px-3 py-2.5 text-sm outline-none focus:border-slate-300"
              />
            </label>
            <label className="inline-flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(event) => setForm((prev) => ({ ...prev, enabled: event.target.checked }))}
                className="h-4 w-4 rounded border-slate-300"
              />
              启用
            </label>
            {error ? <p className="text-xs text-red-600">{error}</p> : null}
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => void handleSubmit()}
                className="inline-flex items-center gap-1 rounded-xl bg-slate-900 px-3.5 py-2 text-xs font-semibold text-white transition-colors hover:bg-slate-800"
              >
                <Plus className="h-3.5 w-3.5" />
                {editingId ? "保存修改" : "新增模型"}
              </button>
              {editingId ? (
                <button
                  type="button"
                  onClick={() => {
                    setEditingId(null);
                    setForm(EMPTY_FORM);
                    setError(null);
                  }}
                  className="rounded-xl border border-slate-200/80 px-3.5 py-2 text-xs font-semibold text-slate-700 transition-colors hover:bg-slate-50"
                >
                  取消
                </button>
              ) : null}
            </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
