import * as React from "react";
import { cn } from "@/shared/lib/utils";

interface CheckboxProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "type"> {
  label?: string;
}

const Checkbox = React.forwardRef<HTMLInputElement, CheckboxProps>(
  ({ className, label, id, ...props }, ref) => {
    const fallbackId = React.useId();
    const actualId = id ?? fallbackId;

    return (
      <label htmlFor={actualId} className="inline-flex cursor-pointer items-center gap-2 text-sm text-slate-700">
        <input
          id={actualId}
          ref={ref}
          type="checkbox"
          className={cn(
            "h-4 w-4 rounded border-slate-300 text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400",
            className,
          )}
          {...props}
        />
        {label ? <span>{label}</span> : null}
      </label>
    );
  },
);

Checkbox.displayName = "Checkbox";

export { Checkbox };
