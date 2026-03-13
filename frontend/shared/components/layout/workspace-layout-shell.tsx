"use client";

import { WorkspaceShellProvider } from "@/features/workspace/context/workspace-shell-context";
import { useWorkspaceShell } from "@/features/workspace/context/workspace-shell-context";
import { WorkspaceHeader } from "@/shared/components/layout/workspace-header";
import { WorkspaceSidebar } from "@/shared/components/layout/workspace-sidebar";

function WorkspaceLayoutFrame({
  children,
}: {
  children: React.ReactNode;
}) {
  const { collapsed } = useWorkspaceShell();

  return (
    <div className="min-h-screen bg-app text-foreground">
      <div className="mx-auto flex min-h-screen max-w-[1680px]">
        <WorkspaceSidebar />
        <div
          className={`min-w-0 flex-1 transition-[padding] duration-300 ease-out ${
            collapsed ? "lg:pl-0" : "lg:pl-0"
          }`}
        >
          <WorkspaceHeader />
          <main className="px-4 pb-8 pt-6 transition-all duration-300 ease-out md:px-6">
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}

export function WorkspaceLayoutShell({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <WorkspaceShellProvider>
      <WorkspaceLayoutFrame>{children}</WorkspaceLayoutFrame>
    </WorkspaceShellProvider>
  );
}
