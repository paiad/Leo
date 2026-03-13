import { Bot, SlidersHorizontal, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { ChatRuntimeConfig } from "@/features/chat/types/chat";

type ChatSettingsPanelProps = {
  runtimeConfig: ChatRuntimeConfig;
  platformSystemPrompt: string;
  activeModelLabel: string;
  onApplyRuntimeConfig: (config: ChatRuntimeConfig) => void;
  onClose?: () => void;
};

export function ChatSettingsPanel({
  runtimeConfig,
  platformSystemPrompt,
  activeModelLabel,
  onApplyRuntimeConfig,
  onClose,
}: ChatSettingsPanelProps) {
  const [draft, setDraft] = useState<ChatRuntimeConfig>(runtimeConfig);

  useEffect(() => {
    setDraft(runtimeConfig);
  }, [runtimeConfig]);

  const handleApply = () => {
    onApplyRuntimeConfig({
      workspacePrompt: draft.workspacePrompt.trim(),
    });
  };

  return (
    <aside className="h-full min-w-[320px] max-w-[360px] shrink-0 space-y-4 overflow-y-auto border-l border-slate-200 bg-white p-4 shadow-xl">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-900">面板设置</h2>
        {onClose ? (
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900"
            aria-label="关闭设置面板"
          >
            <X className="h-4 w-4" />
          </button>
        ) : null}
      </div>

      <section className="rounded-2xl border border-slate-200 bg-white p-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
          <Bot className="h-4 w-4" />
          当前会话模型
        </div>
        <p className="mt-2 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-700">
          {activeModelLabel}
        </p>
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
          <SlidersHorizontal className="h-4 w-4" />
          System (Platform)
        </div>
        <p className="mt-1 text-xs text-slate-500">平台固定提示词，只读不可编辑</p>
        <textarea
          readOnly
          value={platformSystemPrompt}
          className="mt-2 h-36 w-full resize-none rounded-lg border border-slate-200 bg-slate-50 px-2 py-2 text-xs leading-5 text-slate-600 outline-none"
        />
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-4">
        <div className="text-sm font-semibold text-slate-900">System (Workspace)</div>
        <p className="mt-1 text-xs text-slate-500">工作区可编辑提示词，将拼接到平台提示词后</p>
        <textarea
          value={draft.workspacePrompt}
          onChange={(event) =>
            setDraft((prev) => ({ ...prev, workspacePrompt: event.target.value }))
          }
          placeholder="例如：回答先给结论，再给执行步骤。"
          className="mt-2 h-28 w-full resize-y rounded-lg border border-slate-200 px-2 py-2 text-xs leading-5 text-slate-700 outline-none focus:border-slate-400"
        />
        <button
          type="button"
          onClick={handleApply}
          className="mt-3 w-full rounded-lg bg-slate-900 px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-slate-800"
        >
          应用
        </button>
      </section>
    </aside>
  );
}
