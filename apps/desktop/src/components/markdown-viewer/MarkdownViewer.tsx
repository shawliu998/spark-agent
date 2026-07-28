import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/cn";

interface MarkdownAstNode {
  type: string;
  value?: string;
  url?: string;
  children?: MarkdownAstNode[];
}

const REPORT_EVIDENCE_COMMENT =
  /^<!--\s*\[@evidence:[^:\]\s]+:[a-f0-9]{64}\]\s*-->$/i;

function reportCitationPlugin(indices: ReadonlySet<number>) {
  return () => (tree: MarkdownAstNode) => {
    const visit = (node: MarkdownAstNode) => {
      if (
        !node.children ||
        node.type === "link" ||
        node.type === "code" ||
        node.type === "inlineCode"
      ) {
        return;
      }
      node.children = node.children.flatMap((child) => {
        if (
          child.type === "html" &&
          child.value &&
          REPORT_EVIDENCE_COMMENT.test(child.value.trim())
        ) {
          return [];
        }
        if (child.type !== "text" || !child.value) {
          visit(child);
          return [child];
        }
        const fragments: MarkdownAstNode[] = [];
        let offset = 0;
        for (const match of child.value.matchAll(/\[([1-9][0-9]{0,4})\]/g)) {
          const start = match.index ?? 0;
          const index = Number(match[1]);
          if (!indices.has(index)) continue;
          if (start > offset) {
            fragments.push({
              type: "text",
              value: child.value.slice(offset, start),
            });
          }
          fragments.push({
            type: "link",
            url: `#spark-citation-${index}`,
            children: [{ type: "text", value: match[0] }],
          });
          offset = start + match[0].length;
        }
        if (fragments.length === 0) return [child];
        if (offset < child.value.length) {
          fragments.push({ type: "text", value: child.value.slice(offset) });
        }
        return fragments;
      });
    };
    visit(tree);
  };
}

/** Two contexts render markdown: chat bubbles use app theme tokens while the
 *  file preview uses stable paper tokens so exported research reads the same
 *  in light and dark app themes. Both share one deliberate type hierarchy. */
type Variant = "chat" | "document";

const STYLES: Record<Variant, Record<string, string>> = {
  chat: {
    root: "text-base leading-relaxed text-text",
    p: "my-2 first:mt-0 last:mb-0",
    a: "text-link underline underline-offset-2",
    code: "rounded bg-surface-2 px-1 py-0.5 font-mono text-sm text-link",
    pre: "my-3 overflow-x-auto rounded-input bg-surface-2 p-3 font-mono text-sm leading-5 [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-text",
    ul: "my-2 ml-5 list-disc space-y-1",
    ol: "my-2 ml-5 list-decimal space-y-1",
    h1: "mb-3 mt-5 text-document-display font-bold first:mt-0",
    h2: "mb-2 mt-5 text-document-heading font-semibold first:mt-0",
    h3: "mb-2 mt-4 text-document-subheading font-semibold first:mt-0",
    h4: "mb-1.5 mt-3 text-base font-semibold first:mt-0",
    blockquote: "my-2 border-l-2 border-border pl-3 text-muted",
    hr: "my-4 border-border",
    table: "border-collapse text-sm",
    th: "border border-border bg-surface-2 px-3 py-1.5 text-left font-semibold",
    td: "border border-border px-3 py-1.5",
  },
  // A research artifact is a dense, reviewable document, not an editorial
  // landing page. Its neutral paper tokens are stable across app themes while
  // typography stays aligned with the native desktop UI.
  document: {
    root: "mx-auto max-w-[68ch] font-sans text-base leading-relaxed text-[var(--document-text)] antialiased [font-feature-settings:'liga','kern'] selection:bg-[var(--document-selection)]",
    p: "my-4 [text-wrap:pretty] first:mt-0 last:mb-0",
    a: "font-medium text-[var(--document-link)] underline decoration-[var(--document-border)] decoration-1 underline-offset-4 transition-colors hover:decoration-[var(--document-link)]",
    code: "rounded-input bg-[var(--document-subtle)] px-1.5 py-0.5 font-mono text-sm text-[var(--document-code)] ring-1 ring-[var(--document-border)]",
    pre: "my-5 overflow-x-auto rounded-card bg-[var(--document-subtle)] p-4 font-mono text-sm leading-6 ring-1 ring-[var(--document-border)] [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-[var(--document-text)] [&_code]:ring-0",
    ul: "my-4 ml-5 list-disc space-y-2 marker:text-[var(--document-muted)]",
    ol: "my-4 ml-5 list-decimal space-y-2 marker:font-medium marker:text-[var(--document-muted)]",
    h1: "mb-4 mt-10 text-document-display font-bold text-[var(--document-text)] [text-wrap:balance] first:mt-0",
    h2: "mb-3 mt-9 text-document-heading font-semibold text-[var(--document-text)] [text-wrap:balance] first:mt-0",
    h3: "mb-2 mt-7 text-document-subheading font-semibold text-[var(--document-text)] first:mt-0",
    h4: "mb-2 mt-6 text-base font-semibold text-[var(--document-text)] first:mt-0",
    blockquote: "my-5 rounded-card border border-[var(--document-border)] bg-[var(--document-subtle)] px-4 py-2 text-[var(--document-muted)] [&_p]:my-1.5",
    hr: "my-8 border-[var(--document-border)]",
    table: "min-w-full border-collapse text-sm tabular-nums",
    th: "border-b-2 border-[var(--document-border)] bg-[var(--document-subtle)] px-4 py-2.5 text-left font-semibold text-[var(--document-text)]",
    td: "border-b border-[var(--document-border)] px-4 py-2.5",
  },
};

export function MarkdownViewer({
  children,
  className,
  variant = "chat",
  citationIndices = [],
  onCitationClick,
  citationAriaLabel,
}: {
  children: string;
  className?: string;
  variant?: Variant;
  citationIndices?: readonly number[];
  onCitationClick?: (index: number, trigger: HTMLButtonElement) => void;
  citationAriaLabel?: (index: number) => string;
}) {
  const s = STYLES[variant];
  const citationIndexSet = new Set(citationIndices);
  const citationRemarkPlugin =
    citationIndexSet.size > 0 && onCitationClick
      ? reportCitationPlugin(citationIndexSet)
      : null;
  return (
    <div className={cn(s.root, className)}>
      <ReactMarkdown
        remarkPlugins={
          citationRemarkPlugin
            ? [remarkGfm, citationRemarkPlugin]
            : [remarkGfm]
        }
        components={{
          p: ({ children }) => <p className={s.p}>{children}</p>,
          a: ({ children, href }) => {
            const citationMatch = href?.match(/^#spark-citation-([1-9][0-9]{0,4})$/);
            if (citationMatch && onCitationClick) {
              const index = Number(citationMatch[1]);
              return (
                <button
                  type="button"
                  className="citation-link"
                  onClick={(event) => onCitationClick(index, event.currentTarget)}
                  aria-label={citationAriaLabel?.(index)}
                >
                  {children}
                </button>
              );
            }
            return (
              <a href={href} className={s.a}>
                {children}
              </a>
            );
          },
          code: ({ children }) => <code className={s.code}>{children}</code>,
          // Block code: the plain wrapper — its inner <code> is restyled via [&_code].
          pre: ({ children }) => <pre className={s.pre}>{children}</pre>,
          ul: ({ children }) => <ul className={s.ul}>{children}</ul>,
          ol: ({ children }) => <ol className={s.ol}>{children}</ol>,
          li: ({ children }) => <li>{children}</li>,
          // Document elements (headings, quotes, tables, rules) — Tailwind's
          // preflight strips the browser defaults, so each needs explicit style.
          h1: ({ children }) => <h1 className={s.h1}>{children}</h1>,
          h2: ({ children }) => <h2 className={s.h2}>{children}</h2>,
          h3: ({ children }) => <h3 className={s.h3}>{children}</h3>,
          h4: ({ children }) => <h4 className={s.h4}>{children}</h4>,
          blockquote: ({ children }) => <blockquote className={s.blockquote}>{children}</blockquote>,
          hr: () => <hr className={s.hr} />,
          table: ({ children }) => (
            <div className="my-4 overflow-x-auto">
              <table className={s.table}>{children}</table>
            </div>
          ),
          th: ({ children }) => <th className={s.th}>{children}</th>,
          td: ({ children }) => <td className={s.td}>{children}</td>,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
