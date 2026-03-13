import type { LucideIcon } from "lucide-react";

type WorkspacePageHeaderProps = {
  title: string;
  description: string;
  icon: LucideIcon;
};

export function WorkspacePageHeader({
  title,
  description,
  icon: Icon,
}: WorkspacePageHeaderProps) {
  return (
    <header>
      <div className="flex items-center gap-2.5">
        <div className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200/80 bg-slate-50 text-slate-700">
          <Icon className="h-4.5 w-4.5" />
        </div>
        <h1 className="text-[30px] font-semibold tracking-[-0.025em] text-slate-900 md:text-[32px]">
          {title}
        </h1>
      </div>
      <p className="mt-1.5 pl-0.5 text-[16px] font-normal tracking-[-0.005em] text-slate-500 md:text-[17px]">
        {description}
      </p>
    </header>
  );
}
