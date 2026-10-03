import { memo, useState, type ComponentProps, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Message markdown. Styled with tokens, never raw colors. Links open in the
 * system browser path (Tauri) instead of navigating the WebView.
 */

function CodeBlock({ language, children }: { language: string; children: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard
      ?.writeText(children)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => undefined);
  };
  return (
    <div className="group/code my-2 overflow-hidden rounded-lg border border-border bg-slate-2">
      <div className="flex h-8 items-center justify-between border-b border-border px-3 text-xs text-muted-foreground">
        <span className="font-mono">{language || "text"}</span>
        <button
          type="button"
          onClick={copy}
          aria-label={copied ? "Copied" : "Copy code"}
          className="inline-flex items-center gap-1 rounded px-1 text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
        >
          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <pre className="m-0 overflow-x-auto px-3 py-2.5 font-mono text-[12.5px] leading-relaxed text-foreground">
        <code>{children}</code>
      </pre>
    </div>
  );
}

function textOf(node: ReactNode): string {
  if (typeof node === "string") return node;
  if (Array.isArray(node)) return node.map(textOf).join("");
  return "";
}

const components: Components = {
  p: (props) => <p className="my-2 first:mt-0 last:mb-0" {...props} />,
  h1: (props) => <h1 className="mt-4 mb-2 text-lg font-semibold tracking-tight first:mt-0" {...props} />,
  h2: (props) => <h2 className="mt-4 mb-2 text-base font-semibold tracking-tight first:mt-0" {...props} />,
  h3: (props) => <h3 className="mt-3 mb-1.5 text-sm font-semibold first:mt-0" {...props} />,
  h4: (props) => <h4 className="mt-3 mb-1.5 text-sm font-semibold first:mt-0" {...props} />,
  ul: (props) => <ul className="my-2 list-disc space-y-1 pl-5 marker:text-muted-foreground" {...props} />,
  ol: (props) => <ol className="my-2 list-decimal space-y-1 pl-5 marker:text-muted-foreground" {...props} />,
  li: (props) => <li className="pl-0.5" {...props} />,
  blockquote: (props) => (
    <blockquote className="my-2 border-l-2 border-border pl-3 text-muted-foreground" {...props} />
  ),
  hr: () => <hr className="my-4 border-border" />,
  a: ({ href, children, ...rest }: ComponentProps<"a">) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className="text-blue-11 underline decoration-blue-a6 underline-offset-2 hover:decoration-blue-11"
      {...rest}
    >
      {children}
    </a>
  ),
  table: (props) => (
    <div className="my-2 overflow-x-auto rounded-lg border border-border">
      <table className="w-full border-collapse text-left text-[12.5px]" {...props} />
    </div>
  ),
  thead: (props) => <thead className="bg-slate-2" {...props} />,
  th: (props) => <th className="border-b border-border px-3 py-1.5 font-medium" {...props} />,
  td: (props) => <td className="border-t border-border px-3 py-1.5 align-top" {...props} />,
  pre: ({ children }) => <>{children}</>,
  code: ({ className, children, ...rest }: ComponentProps<"code">) => {
    const match = /language-([\w-]+)/.exec(className ?? "");
    const text = textOf(children).replace(/\n$/, "");
    const isBlock = Boolean(match) || text.includes("\n");
    if (isBlock) return <CodeBlock language={match?.[1] ?? ""}>{text}</CodeBlock>;
    return (
      <code
        className="rounded bg-slate-3 px-1 py-0.5 font-mono text-[12.5px] text-foreground"
        {...rest}
      >
        {children}
      </code>
    );
  },
};

export const Markdown = memo(function Markdown({
  text,
  className,
}: {
  text: string;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0 leading-relaxed break-words select-text", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
});
