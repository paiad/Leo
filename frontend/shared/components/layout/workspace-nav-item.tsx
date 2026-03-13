"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/shared/lib/utils";
import type { WorkspaceNavItem } from "@/features/workspace/config/navigation";

export function WorkspaceNavItemButton({
  item,
  collapsed,
}: {
  item: WorkspaceNavItem;
  collapsed: boolean;
}) {
  const pathname = usePathname();
  const isActive = pathname === item.href;
  const Icon = item.icon;

  return (
    <Link
      href={item.href}
      title={item.title}
      className={cn(
        collapsed
          ? "mx-auto flex h-10 w-10 min-w-10 max-w-10 items-center justify-center rounded-xl px-0 text-sm font-medium transition-colors duration-200"
          : "flex h-10 w-full items-center gap-3 rounded-xl px-3 text-left text-sm font-medium transition-colors duration-200",
        isActive
          ? "bg-slate-200 text-slate-900"
          : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
      )}
    >
      <Icon className="h-4 w-4 shrink-0" />
      <span
        className={cn(
          "min-w-0 overflow-hidden whitespace-nowrap transition-all duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]",
          collapsed
            ? "ml-0 max-w-0 -translate-x-1 opacity-0"
            : "ml-0 max-w-[132px] translate-x-0 opacity-100"
        )}
      >
        {item.title}
      </span>
    </Link>
  );
}
