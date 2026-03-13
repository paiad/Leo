"use client";

import {
  ChevronRight,
  Maximize,
  Minimize,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import { usePathname } from "next/navigation";
import { useWorkspaceShell } from "@/features/workspace/context/workspace-shell-context";
import {
  workspaceNavItems,
  workspaceRouteMeta,
} from "@/features/workspace/config/navigation";
import { useState, useEffect, useCallback } from "react";

function resolveCurrentPage(pathname: string) {
  return workspaceRouteMeta[pathname] ?? workspaceNavItems[0];
}

export function WorkspaceHeader() {
  const pathname = usePathname();
  const { collapsed, toggleCollapsed } = useWorkspaceShell();
  const currentPage = resolveCurrentPage(pathname ?? "/");
  const [isFullscreen, setIsFullscreen] = useState(false);

  useEffect(() => {
    const handleChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };
    document.addEventListener("fullscreenchange", handleChange);
    return () => document.removeEventListener("fullscreenchange", handleChange);
  }, []);

  const toggleFullscreen = useCallback(() => {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  }, []);

  return (
    <header className="sticky top-0 z-10 flex h-16 items-center gap-3 border-b border-slate-200 bg-app px-4 md:px-6">
      <button
        type="button"
        onClick={toggleCollapsed}
        className="hidden h-10 w-10 items-center justify-center rounded-xl text-slate-700 transition-colors hover:text-slate-950 lg:inline-flex"
        aria-label={collapsed ? "展开侧边栏" : "折叠侧边栏"}
      >
        {collapsed ? (
          <PanelLeftOpen className="h-4 w-4" />
        ) : (
          <PanelLeftClose className="h-4 w-4" />
        )}
      </button>

      <div className="hidden items-center gap-2 overflow-hidden text-sm text-slate-500 md:flex">
        <span className="whitespace-nowrap font-medium transition-all duration-300 ease-out">
          Workspace
        </span>
        <ChevronRight className="h-4 w-4 shrink-0 transition-all duration-300 ease-out" />
        <span className="whitespace-nowrap font-medium text-slate-900 transition-all duration-300 ease-out">
          {currentPage.title}
        </span>
      </div>

      <div className="ml-auto flex items-center">
        <button
          type="button"
          onClick={toggleFullscreen}
          className="flex h-10 w-10 items-center justify-center rounded-xl text-slate-700 transition-colors hover:text-slate-950"
          aria-label={isFullscreen ? "退出全屏" : "全屏"}
        >
          {isFullscreen ? (
            <Minimize className="h-4 w-4" />
          ) : (
            <Maximize className="h-4 w-4" />
          )}
        </button>
      </div>
    </header>
  );
}
