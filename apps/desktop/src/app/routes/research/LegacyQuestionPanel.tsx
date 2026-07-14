import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Loader2,
  Quote,
  Search,
} from "lucide-react";
import type { EvidenceSpan, ResearchAnswer, ResearchSource } from "@spark/research-domain";
import { MarkdownViewer } from "@/components/markdown-viewer/MarkdownViewer";
import { cn } from "@/lib/cn";
import type { ResearchPdfSelection } from "./ResearchInspector";

export interface LegacyQuestionPanelProps {
  question: string;
  approved: boolean;
  asking: boolean;
  answer: ResearchAnswer | null;
  projectReady: boolean;
  literatureReady: boolean;
  sources: ResearchSource[];
  readySourceCount: number;
  selection: ResearchPdfSelection | null;
  onQuestionChange: (value: string) => void;
  onApprovalChange: (approved: boolean) => void;
  onSubmit: (event: React.FormEvent) => void | Promise<void>;
  onSelectEvidence: (evidence: EvidenceSpan) => void;
}

function percent(value: number): string {
  const normalized = value > 1 ? value / 100 : value;
  return new Intl.NumberFormat(undefined, {
    style: "percent",
    maximumFractionDigits: 0,
  }).format(Math.max(0, Math.min(1, normalized)));
}

export function LegacyQuestionPanel({
  question,
  approved,
  asking,
  answer,
  projectReady,
  literatureReady,
  sources,
  readySourceCount,
  selection,
  onQuestionChange,
  onApprovalChange,
  onSubmit,
  onSelectEvidence,
}: LegacyQuestionPanelProps) {
  const { t } = useTranslation("pages");
  return (
    <details className="rounded-card border border-border bg-surface">
      <summary className="cursor-pointer px-4 py-3 text-xs font-medium text-muted hover:text-text">
        {t("research.legacy.heading", { defaultValue: "Quick question (legacy)" })}
      </summary>
      <div className="border-t border-border p-4">
        <form onSubmit={(event) => void onSubmit(event)}>
          <label htmlFor="research-question" className="text-xs font-medium text-text">
            {t("research.questionLabel", { defaultValue: "Research question" })}
          </label>
          <textarea
            id="research-question"
            value={question}
            onChange={(event) => onQuestionChange(event.target.value)}
            rows={3}
            placeholder={t("research.questionPlaceholder", {
              defaultValue: "What does the evidence say about…?",
            })}
            className="mt-2 w-full resize-y rounded-input border border-border bg-bg px-3 py-2 text-sm leading-relaxed text-text outline-none placeholder:text-muted focus:border-accent"
          />
          {literatureReady && readySourceCount > 0 && (
            <label className="mt-2 flex cursor-pointer items-start gap-2 rounded-input border border-warn/25 bg-warn/5 px-3 py-2.5 text-[11px] leading-relaxed text-muted">
              <input
                type="checkbox"
                checked={approved}
                onChange={(event) => onApprovalChange(event.target.checked)}
                className="mt-0.5 h-3.5 w-3.5 accent-[var(--color-accent)]"
              />
              <span>
                {t("research.remoteApproval", {
                  defaultValue:
                    "For this request, allow the configured remote model gateway to receive the question and PDF text needed for embedding, retrieval, and answering.",
                })}
              </span>
            </label>
          )}
          <div className="mt-3 flex items-center gap-3 border-t border-border-faint pt-3">
            <span className="min-w-0 flex-1 truncate text-[11px] text-muted">
              {readySourceCount > 0
                ? t("research.readySourceCount", {
                    defaultValue: "{{count}} indexed sources",
                    count: readySourceCount,
                  })
                : t("research.needsSource", {
                    defaultValue: "Import an indexed PDF to ask a question.",
                  })}
            </span>
            <button
              type="submit"
              disabled={
                !question.trim() ||
                !projectReady ||
                !literatureReady ||
                readySourceCount === 0 ||
                !approved ||
                asking
              }
              className="flex shrink-0 items-center gap-1.5 rounded-input bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-40"
            >
              {asking ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />}
              {asking
                ? t("research.searching", { defaultValue: "Building evidence…" })
                : t("research.ask", { defaultValue: "Ask library" })}
            </button>
          </div>
        </form>

        {asking && !answer && (
          <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted">
            <Loader2 size={15} className="animate-spin" />
            {t("research.searchingSources", {
              defaultValue: "Searching and verifying source passages…",
            })}
          </div>
        )}
        {answer && (
          <AnswerView
            answer={answer}
            sources={sources}
            selection={selection}
            onSelectEvidence={onSelectEvidence}
          />
        )}
      </div>
    </details>
  );
}

function AnswerView({
  answer,
  sources,
  selection,
  onSelectEvidence,
}: {
  answer: ResearchAnswer;
  sources: ResearchSource[];
  selection: ResearchPdfSelection | null;
  onSelectEvidence: (evidence: EvidenceSpan) => void;
}) {
  const { t } = useTranslation("pages");
  const hasVerifiedEvidence = answer.claims.some((claim) =>
    claim.evidence.some((evidence) => evidence.verified),
  );
  return (
    <article className="mt-5 space-y-4">
      <section className="rounded-card border border-border bg-surface p-4 shadow-card">
        <div className="mb-3 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-muted">
          {hasVerifiedEvidence ? (
            <CheckCircle2 size={14} className="text-ok" />
          ) : (
            <AlertTriangle size={14} className="text-warn" />
          )}
          {hasVerifiedEvidence
            ? t("research.answerHeading", { defaultValue: "Evidence-grounded answer" })
            : t("research.unverifiedAnswerHeading", {
                defaultValue: "Answer — evidence needs review",
              })}
        </div>
        <MarkdownViewer>{answer.answer}</MarkdownViewer>
      </section>

      <div className="flex items-center gap-2 pt-1">
        <h3 className="text-xs font-medium uppercase tracking-wider text-muted">
          {t("research.claimsHeading", {
            defaultValue: "Claims ({{count}})",
            count: answer.claims.length,
          })}
        </h3>
        <div className="h-px flex-1 bg-border" />
      </div>

      {answer.claims.map((claim, index) => (
        <section key={claim.id} className="rounded-card border border-border bg-surface p-4">
          <div className="flex items-start gap-3">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface-2 text-[10px] font-semibold text-muted ring-1 ring-border">
              {index + 1}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium leading-relaxed text-text">{claim.statement}</p>
              <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px]">
                <span className="rounded-full bg-surface-2 px-2 py-0.5 text-muted ring-1 ring-border">
                  {t("research.confidence", {
                    defaultValue: "{{value}} confidence",
                    value: percent(claim.confidence),
                  })}
                </span>
                <span
                  className={cn(
                    "rounded-full px-2 py-0.5 ring-1",
                    claim.reviewStatus === "verified"
                      ? "bg-ok/10 text-ok ring-ok/20"
                      : claim.reviewStatus === "rejected"
                        ? "bg-error/10 text-error ring-error/20"
                        : "bg-warn/10 text-warn ring-warn/20",
                  )}
                >
                  {t(`research.reviewStatus.${claim.reviewStatus}`, {
                    defaultValue: claim.reviewStatus,
                  })}
                </span>
              </div>
            </div>
          </div>

          <div className="mt-3 space-y-2 border-t border-border-faint pt-3">
            {claim.evidence.length === 0 && (
              <div className="flex items-center gap-2 text-xs text-warn">
                <AlertTriangle size={13} />
                {t("research.noEvidence", { defaultValue: "No verified source passage attached." })}
              </div>
            )}
            {claim.evidence.map((evidence) => {
              const active = selection?.evidenceId === evidence.id;
              const sourceTitle =
                sources.find((source) => source.id === evidence.sourceId)?.title ??
                t("research.unknownSource", { defaultValue: "Unknown source" });
              return (
                <button
                  key={evidence.id}
                  type="button"
                  onClick={() => onSelectEvidence(evidence)}
                  className={cn(
                    "group w-full rounded-input border px-3 py-2.5 text-left transition-colors",
                    active
                      ? "border-accent/50 bg-accent/5"
                      : "border-border bg-bg hover:border-accent/30 hover:bg-surface-2",
                  )}
                >
                  <span className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-wide text-muted">
                    <Quote size={12} className="text-accent" />
                    {t("research.evidencePage", {
                      defaultValue: "{{source}}, page {{page}}",
                      source: sourceTitle,
                      page: evidence.pageLabel ?? evidence.pageIndex + 1,
                    })}
                    <span className="ml-auto normal-case tracking-normal">
                      {evidence.verified
                        ? t("research.verified", { defaultValue: "verified" })
                        : t("research.needsReview", { defaultValue: "needs review" })}
                    </span>
                    <ChevronRight size={12} className="transition-transform group-hover:translate-x-0.5" />
                  </span>
                  <span className="mt-1.5 block line-clamp-4 font-serif text-[13px] leading-relaxed text-text/90">
                    {evidence.text}
                  </span>
                </button>
              );
            })}
          </div>
        </section>
      ))}

      {answer.unresolvedQuestions.length > 0 && (
        <section className="rounded-card border border-warn/30 bg-warn/5 p-4">
          <h3 className="flex items-center gap-2 text-xs font-medium text-text">
            <AlertTriangle size={14} className="text-warn" />
            {t("research.unresolvedHeading", { defaultValue: "Unresolved questions" })}
          </h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed text-muted">
            {answer.unresolvedQuestions.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </section>
      )}
    </article>
  );
}
