import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import { cn } from "@/shared/lib/utils";

type MarkdownMessageProps = {
  content: string;
  inverse?: boolean;
};

function isImageLink(value: string): boolean {
  return /^https?:\/\/\S+\.(?:png|jpe?g|gif|webp|bmp|svg)(?:\?\S*)?$/i.test(value.trim());
}

export function MarkdownMessage({ content, inverse = false }: MarkdownMessageProps) {
  return (
    <div
      className={cn(
        "min-w-0 break-words",
        "[&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
        inverse && "text-white",
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        components={{
          p: ({ className, ...props }) => (
            <p className={cn("my-2 whitespace-pre-wrap", className)} {...props} />
          ),
          h1: ({ className, ...props }) => (
            <h1 className={cn("mb-2 mt-4 text-lg font-semibold", className)} {...props} />
          ),
          h2: ({ className, ...props }) => (
            <h2 className={cn("mb-2 mt-4 text-base font-semibold", className)} {...props} />
          ),
          h3: ({ className, ...props }) => (
            <h3 className={cn("mb-1.5 mt-3 text-sm font-semibold", className)} {...props} />
          ),
          ul: ({ className, ...props }) => (
            <ul className={cn("my-2 list-disc space-y-1 pl-5", className)} {...props} />
          ),
          ol: ({ className, ...props }) => (
            <ol className={cn("my-2 list-decimal space-y-1 pl-5", className)} {...props} />
          ),
          li: ({ className, ...props }) => <li className={cn("my-0.5", className)} {...props} />,
          a: ({ className, ...props }) => (
            (() => {
              const href = typeof props.href === "string" ? props.href : "";
              if (isImageLink(href)) {
                return (
                  <span className="my-1 inline-flex max-w-full flex-col gap-2 align-top">
                    <a
                      className={cn(
                        "break-all underline underline-offset-2",
                        inverse ? "text-slate-100" : "text-blue-700 hover:text-blue-800",
                        className,
                      )}
                      target="_blank"
                      rel="noopener noreferrer nofollow"
                      {...props}
                    />
                    <img
                      src={href}
                      alt="image preview"
                      loading="lazy"
                      className="max-h-[360px] max-w-full rounded-lg border border-slate-200 object-contain"
                    />
                  </span>
                );
              }
              return (
                <a
                  className={cn(
                    "break-all underline underline-offset-2",
                    inverse ? "text-slate-100" : "text-blue-700 hover:text-blue-800",
                    className,
                  )}
                  target="_blank"
                  rel="noopener noreferrer nofollow"
                  {...props}
                />
              );
            })()
          ),
          img: ({ className, ...props }) => (
            <img
              loading="lazy"
              className={cn(
                "my-2 max-h-[420px] max-w-full rounded-lg border border-slate-200 object-contain",
                className,
              )}
              {...props}
            />
          ),
          blockquote: ({ className, ...props }) => (
            <blockquote
              className={cn(
                "my-2 border-l-2 pl-3 italic",
                inverse ? "border-slate-300/60 text-slate-100" : "border-slate-300 text-slate-600",
                className,
              )}
              {...props}
            />
          ),
          pre: ({ className, ...props }) => (
            <pre
              className={cn(
                "my-2 overflow-x-auto rounded-lg border p-3 text-xs leading-6",
                inverse
                  ? "border-white/30 bg-slate-900/40 text-slate-100"
                  : "border-slate-200 bg-slate-50 text-slate-800",
                className,
              )}
              {...props}
            />
          ),
          code: ({ className, children, ...props }) => {
            const raw = String(children).replace(/\n$/, "");
            const isBlock = Boolean(className?.includes("language-")) || raw.includes("\n");

            if (isBlock) {
              return (
                <code className={cn("font-mono", className)} {...props}>
                  {children}
                </code>
              );
            }

            return (
              <code
                className={cn(
                  "rounded px-1 py-0.5 font-mono text-[0.85em]",
                  inverse ? "bg-white/20 text-white" : "bg-slate-100 text-slate-900",
                  className,
                )}
                {...props}
              >
                {children}
              </code>
            );
          },
          table: ({ className, ...props }) => (
            <div className="my-2 overflow-x-auto">
              <table className={cn("min-w-full border-collapse text-xs", className)} {...props} />
            </div>
          ),
          thead: ({ className, ...props }) => (
            <thead
              className={cn(
                inverse ? "bg-white/10 text-white" : "bg-slate-100 text-slate-700",
                className,
              )}
              {...props}
            />
          ),
          th: ({ className, ...props }) => (
            <th
              className={cn(
                "border px-2 py-1 text-left font-semibold",
                inverse ? "border-white/20" : "border-slate-200",
                className,
              )}
              {...props}
            />
          ),
          td: ({ className, ...props }) => (
            <td
              className={cn(
                "border px-2 py-1 align-top",
                inverse ? "border-white/20" : "border-slate-200",
                className,
              )}
              {...props}
            />
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
