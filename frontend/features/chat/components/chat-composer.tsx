"use client";

import { Eraser, Send, Square } from "lucide-react";
import { KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

type ChatComposerProps = {
  placeholder?: string;
  isSending?: boolean;
  onSend?: (content: string) => Promise<void>;
  onStop?: () => void;
  onClearMessages?: () => Promise<void>;
  isClearingMessages?: boolean;
  clearScopeLabel?: string;
};

export function ChatComposer({
  placeholder = "输入消息",
  isSending = false,
  onSend,
  onStop,
  onClearMessages,
  isClearingMessages = false,
  clearScopeLabel = "当前来源",
}: ChatComposerProps) {
  const [draft, setDraft] = useState("");
  const [isClearMenuOpen, setIsClearMenuOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const charCount = useMemo(() => draft.trim().length, [draft]);
  const drawerPopupBaseClass =
    "absolute bottom-full left-1/2 z-30 mb-0 w-[96%] max-w-[800px] -translate-x-1/2 rounded-t-2xl rounded-b-none border border-b-0 border-slate-200 bg-white text-slate-800 transition-all duration-200 ease-out";

  const handleSend = async () => {
    if (!onSend) {
      return;
    }

    const content = draft.trim();
    if (!content || isSending) {
      return;
    }

    setDraft("");
    await onSend(content);
  };

  const handleInputKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    if (isSending) {
      onStop?.();
      return;
    }
    void handleSend();
  };

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (containerRef.current && !containerRef.current.contains(target)) {
        setIsClearMenuOpen(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <section className="bg-[rgb(var(--background))] px-0 pb-0 pt-0">
      <div ref={containerRef} className="relative mx-auto w-full max-w-3xl">
        <div
          aria-hidden={!isClearMenuOpen}
          className={`${drawerPopupBaseClass} p-3 ${
            isClearMenuOpen
              ? "pointer-events-auto translate-y-0 opacity-100"
              : "pointer-events-none translate-y-2 opacity-0"
          }`}
        >
          <p className="text-sm font-semibold text-slate-800">清空 {clearScopeLabel} 聊天记录？</p>
          <p className="mt-1 text-xs text-slate-500">
            该操作会删除当前来源（{clearScopeLabel}）全部会话中的消息记录与会话记忆。
          </p>
          <div className="mt-3 flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={() => setIsClearMenuOpen(false)}
              className="inline-flex h-8 items-center rounded-md border border-slate-200 px-3 text-xs text-slate-600 hover:bg-slate-50"
              disabled={isClearingMessages}
            >
              取消
            </button>
            <button
              type="button"
              onClick={async () => {
                if (!onClearMessages || isClearingMessages) {
                  return;
                }
                await onClearMessages();
                setIsClearMenuOpen(false);
              }}
              className="inline-flex h-8 items-center rounded-md bg-red-600 px-3 text-xs text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:bg-red-300"
              disabled={isClearingMessages}
            >
              {isClearingMessages ? "清空中..." : "确认清空"}
            </button>
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-2">
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={placeholder}
            className="h-10 w-full border-none bg-transparent px-2 text-sm text-slate-800 outline-none placeholder:text-slate-400"
            onKeyDown={handleInputKeyDown}
          />
          <div className="mt-2 flex items-center justify-between">
            <button
              type="button"
              onClick={() => setIsClearMenuOpen((prev) => !prev)}
              className={`inline-flex h-9 w-9 items-center justify-center rounded-full transition-colors ${
                isClearMenuOpen
                  ? "text-emerald-600 hover:text-emerald-500"
                  : "text-slate-500 hover:text-slate-700"
              }`}
              aria-label="清空当前会话消息"
              aria-haspopup="dialog"
              aria-expanded={isClearMenuOpen}
              disabled={isClearingMessages}
            >
              <Eraser className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={isSending ? onStop : handleSend}
              disabled={isSending ? !onStop : charCount === 0}
              className={`inline-flex h-9 w-9 items-center justify-center rounded-full text-white transition-colors ${
                isSending
                  ? "bg-rose-600 hover:bg-rose-500"
                  : charCount > 0
                    ? "bg-slate-900 hover:bg-slate-700"
                    : "cursor-not-allowed bg-slate-400"
              }`}
              aria-label={isSending ? "停止生成" : "发送消息"}
            >
              {isSending ? <Square className="h-4 w-4" /> : <Send className="h-4 w-4" />}
            </button>
          </div>
        </div>
      </div>
      <div className="mx-auto mt-2 flex w-full max-w-3xl items-center justify-end px-1 text-xs text-slate-400">
        <span>{charCount} chars</span>
      </div>
    </section>
  );
}
