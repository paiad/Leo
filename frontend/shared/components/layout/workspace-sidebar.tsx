"use client";

import { workspaceNavItems } from "@/features/workspace/config/navigation";
import { useWorkspaceShell } from "@/features/workspace/context/workspace-shell-context";
import { WorkspaceNavItemButton } from "@/shared/components/layout/workspace-nav-item";

export function WorkspaceSidebar() {
  const { collapsed } = useWorkspaceShell();

  return (
    <aside
      className={`sticky top-0 hidden h-screen shrink-0 border-r border-slate-200 bg-slate-50 transition-[width] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] lg:flex lg:flex-col ${
        collapsed ? "w-[68px]" : "w-[210px]"
      }`}
    >
      <div className="flex h-14 items-center justify-center">
        <img
          src="/Leo.png"
          alt="Claude"
          className="h-8 w-8 shrink-0"
        />
      </div>

      <div
        className={`flex-1 py-2 transition-[padding] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] ${
          collapsed ? "px-0" : "px-2"
        }`}
      >
        <div
          className={
            collapsed
              ? "flex flex-col items-center gap-1"
              : "space-y-1"
          }
        >
          {workspaceNavItems.map((item) => (
            <WorkspaceNavItemButton
              key={item.href}
              item={item}
              collapsed={collapsed}
            />
          ))}
        </div>
      </div>
    </aside>
  );
}
