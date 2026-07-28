import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  Clock3,
  X,
} from "lucide-react";
import type {
  DatasetAnalysisReview,
  ResearchWorkflowResult,
  ResearchSource,
  ResearchWorkflowReview,
  WorkflowEvidenceRelationship,
  WorkflowEvent,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";
import { WorkflowReviewSummary } from "./WorkflowWorkspace";
import {
  RESEARCH_OBJECT_KIND,
  ResearchObjectIcon,
  type ResearchObjectKind,
} from "./ResearchObjectIcon";
import { useSourcePdfBlob } from "./useSourcePdfBlob";
import {
  RESEARCH_MEMORY_PANEL_DENSITY,
  ResearchMemoryPanel,
  type ResearchMemoryPanelController,
} from "./ResearchMemoryPanel";

export type ResearchInspectorTab = "evidence" | "review" | "memory" | "activity";

export interface ResearchPdfSelection {
  sourceId: string;
  pageIndex: number;
  evidenceId?: string;
  evidence?: {
    text: string;
    pageLabel: string | null;
    quoteHash: string;
    extractionMethod: string;
    confidence: number;
    verified: boolean;
    relationship?: WorkflowEvidenceRelationship["relationship"];
    sourceContentHash?: string | null;
    sourcePageManifestHash?: string | null;
  };
}

export interface ResearchInspectorProps {
  modal?: boolean;
  activeTab: ResearchInspectorTab;
  onTabChange: (tab: ResearchInspectorTab) => void;
  onClose?: () => void;
  selectedSource: ResearchSource | null;
  selection: ResearchPdfSelection | null;
  review: ResearchWorkflowReview | DatasetAnalysisReview | null;
  result?: ResearchWorkflowResult | null;
  onSelectEvidence?: (evidence: WorkflowEvidenceRelationship) => void;
  events: WorkflowEvent[];
  memoryController?: ResearchMemoryPanelController;
}

export function ResearchInspector({
  modal = false,
  activeTab,
  onTabChange,
  onClose,
  selectedSource,
  selection,
  review,
  result = null,
  onSelectEvidence,
  events,
  memoryController,
}: ResearchInspectorProps) {
  const { t } = useTranslation("pages");
  const panelRef = useRef<HTMLElement>(null);
  const activeTabRef = useRef<HTMLButtonElement>(null);
  const pdf = useSourcePdfBlob(
    selectedSource?.id ?? null,
    selection?.pageIndex ?? 0,
  );
  const tabs: Array<{ id: ResearchInspectorTab; label: string; icon: ResearchObjectKind }> = [
    {
      id: "evidence",
      label: t("research.inspector.evidence", { defaultValue: "Evidence" }),
      icon: "evidence",
    },
    {
      id: "review",
      label: t("research.inspector.review", { defaultValue: "Review" }),
      icon: "review",
    },
    ...(memoryController
      ? [{
          id: "memory" as const,
          label: t("research.memory.tab"),
          icon: "memory" as const,
        }]
      : []),
    {
      id: "activity",
      label: t("research.inspector.activity", { defaultValue: "Activity" }),
      icon: "activity",
    },
  ];

  useEffect(() => {
    if (!modal) return;
    queueMicrotask(() => activeTabRef.current?.focus());
  }, [modal]);

  const keepModalFocusInside = (event: React.KeyboardEvent<HTMLElement>) => {
    if (!modal || event.key !== "Tab") return;
    const focusable = Array.from(
      panelRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), iframe, [tabindex]:not([tabindex="-1"])',
      ) ?? [],
    );
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const moveTabFocus = (
    event: React.KeyboardEvent<HTMLButtonElement>,
    currentTab: ResearchInspectorTab,
  ) => {
    const currentIndex = tabs.findIndex((tab) => tab.id === currentTab);
    const nextIndex =
      event.key === "ArrowRight"
        ? (currentIndex + 1) % tabs.length
        : event.key === "ArrowLeft"
          ? (currentIndex - 1 + tabs.length) % tabs.length
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? tabs.length - 1
              : null;
    if (nextIndex === null) return;
    event.preventDefault();
    const nextTab = tabs[nextIndex].id;
    onTabChange(nextTab);
    panelRef.current
      ?.querySelector<HTMLButtonElement>(`#research-inspector-tab-${nextTab}`)
      ?.focus();
  };

  return (
    <section
      ref={panelRef}
      role={modal ? "dialog" : undefined}
      aria-modal={modal || undefined}
      aria-labelledby="research-inspector-title"
      onKeyDown={keepModalFocusInside}
      className="research-inspector flex w-[30rem] min-w-[26rem] shrink-0 flex-col border-l border-border bg-surface max-[1440px]:absolute max-[1440px]:inset-y-0 max-[1440px]:right-0 max-[1440px]:z-30 max-[1440px]:w-[32rem] max-[1440px]:max-w-[calc(100%-2rem)] max-[1440px]:shadow-pop max-[640px]:inset-x-0 max-[640px]:w-full max-[640px]:min-w-0 max-[640px]:max-w-none 2xl:w-[34rem]"
    >
      <h2 id="research-inspector-title" className="sr-only">
        {t("research.inspector.aria", { defaultValue: "Research inspector" })}
      </h2>
      <div className="relative flex h-[57px] shrink-0 items-end border-b border-border px-3 pr-12">
        <div className="flex h-full items-end gap-1" role="tablist">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              ref={activeTab === tab.id ? activeTabRef : undefined}
              type="button"
              role="tab"
              id={`research-inspector-tab-${tab.id}`}
              aria-controls={`research-inspector-panel-${tab.id}`}
              aria-selected={activeTab === tab.id}
              tabIndex={activeTab === tab.id ? 0 : -1}
              onClick={() => onTabChange(tab.id)}
              onKeyDown={(event) => moveTabFocus(event, tab.id)}
              className={cn(
                "flex min-h-11 items-center gap-1.5 border-b-2 px-2.5 text-xs font-medium",
                activeTab === tab.id
                  ? "border-accent text-text"
                  : "border-transparent text-muted hover:text-text",
              )}
            >
              <ResearchObjectIcon kind={tab.icon} size={13} />
              {tab.label}
            </button>
          ))}
        </div>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="touch-target absolute right-2 top-2 flex h-10 w-10 items-center justify-center rounded-input text-muted hover:bg-surface-2 hover:text-text"
            aria-label={t("research.inspector.close", {
              defaultValue: "Close inspector",
            })}
          >
            <X size={14} />
          </button>
        )}
      </div>

      <div className="min-h-0 flex-1">
        {activeTab === "evidence" && (
          <div
            id="research-inspector-panel-evidence"
            role="tabpanel"
            aria-labelledby="research-inspector-tab-evidence"
            className="h-full"
          >
            <EvidencePanel
              selectedSource={selectedSource}
              selection={selection}
              url={pdf.url}
              loading={pdf.loading}
              error={pdf.error}
            />
          </div>
        )}
        {activeTab === "review" && (
          <div
            id="research-inspector-panel-review"
            role="tabpanel"
            aria-labelledby="research-inspector-tab-review"
            className="h-full overflow-y-auto"
          >
            <WorkflowReviewSummary
              review={review}
              result={result}
              onSelectEvidence={onSelectEvidence}
            />
          </div>
        )}
        {activeTab === "activity" && (
          <div
            id="research-inspector-panel-activity"
            role="tabpanel"
            aria-labelledby="research-inspector-tab-activity"
            className="h-full"
          >
            <WorkflowActivity events={events} />
          </div>
        )}
        {activeTab === "memory" && memoryController && (
          <div
            id="research-inspector-panel-memory"
            role="tabpanel"
            aria-labelledby="research-inspector-tab-memory"
            className="h-full"
          >
            <ResearchMemoryPanel
              density={RESEARCH_MEMORY_PANEL_DENSITY.full}
              controller={memoryController}
            />
          </div>
        )}
      </div>
    </section>
  );
}

function EvidencePanel({
  selectedSource,
  selection,
  url,
  loading,
  error,
}: {
  selectedSource: ResearchSource | null;
  selection: ResearchPdfSelection | null;
  url: string | null;
  loading: boolean;
  error: string | null;
}) {
  const { t } = useTranslation("pages");
  const evidence = selection?.evidence;
  const sourceHashMatches = Boolean(
    evidence?.sourceContentHash &&
      selectedSource?.contentHash &&
      evidence.sourceContentHash === selectedSource.contentHash,
  );
  const sourceHashChanged = Boolean(
    evidence?.sourceContentHash &&
      selectedSource?.contentHash &&
      evidence.sourceContentHash !== selectedSource.contentHash,
  );
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3">
        <ResearchObjectIcon kind={RESEARCH_OBJECT_KIND.pdf} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium text-text">
            {selectedSource?.title ?? t("research.previewTitle", { defaultValue: "Source preview" })}
          </p>
          {selectedSource && (
            <p className="text-xs text-muted">
              {t("research.previewPage", {
                defaultValue: "Page {{page}}",
                page: (selection?.pageIndex ?? 0) + 1,
              })}
            </p>
          )}
        </div>
        {selection?.evidenceId && (
          <span className="inline-flex items-center gap-1.5 text-caption font-medium text-muted">
            <span
              aria-hidden="true"
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                evidence?.verified ? "bg-ok" : "bg-warn",
              )}
            />
            {evidence?.verified
              ? t("research.quoteLocated", { defaultValue: "Located locally" })
              : t("research.needsReview", { defaultValue: "Needs review" })}
          </span>
        )}
      </div>
      {evidence && (
        <section
          aria-label={t("research.inspector.selectedPassage", {
            defaultValue: "Selected evidence passage",
          })}
          className="shrink-0 border-b border-border bg-surface px-4 py-3"
        >
          <div className="flex items-center gap-2 text-caption font-medium text-muted">
            <span
              className={
                evidence.relationship === "contradicting" ? "text-warn" : "text-accent"
              }
            >
              {evidence.relationship === "contradicting"
                ? t("research.evidenceRelationship.contradicting", {
                    defaultValue: "Contradicting evidence",
                  })
                : evidence.relationship === "supporting"
                  ? t("research.evidenceRelationship.supporting", {
                      defaultValue: "Supporting evidence",
                    })
                  : t("research.evidenceBadge", { defaultValue: "Evidence passage" })}
            </span>
            <span aria-hidden="true">·</span>
            <span>
              {t("research.previewPage", {
                defaultValue: "Page {{page}}",
                page: evidence.pageLabel ?? selection.pageIndex + 1,
              })}
            </span>
          </div>
          <blockquote className="mt-2 max-h-32 overflow-y-auto border-l border-border pl-3 text-ui leading-5 text-text">
            {evidence.text}
          </blockquote>
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-caption text-muted">
            <span>{evidence.extractionMethod}</span>
            {sourceHashMatches && (
              <span className="text-ok">
                {t("research.inspector.sourceHashMatches", {
                  defaultValue: "Source hash matches cited result",
                })}
              </span>
            )}
            {sourceHashChanged && (
              <span className="text-error">
                {t("research.inspector.sourceHashChanged", {
                  defaultValue: "Local source differs from cited result",
                })}
              </span>
            )}
            {!evidence.sourceContentHash && (
              <span>
                {t("research.inspector.currentSourceOnly", {
                  defaultValue: "No frozen source hash attached",
                })}
              </span>
            )}
          </div>
          <details className="mt-2 border-t border-border-faint pt-1">
            <summary className="flex min-h-8 cursor-pointer items-center text-caption font-medium text-link hover:underline">
              {t("research.workflow.citationProvenance", {
                defaultValue: "Citation provenance",
              })}
            </summary>
            <dl className="grid gap-2 pb-1 font-mono text-caption text-muted">
              <div>
                <dt className="font-sans">{t("research.workflow.evidenceId", { defaultValue: "Evidence ID" })}</dt>
                <dd className="break-all text-text">{selection.evidenceId ?? "—"}</dd>
              </div>
              <div>
                <dt className="font-sans">{t("research.workflow.quoteHash", { defaultValue: "Quote SHA-256" })}</dt>
                <dd className="break-all text-text">{evidence.quoteHash}</dd>
              </div>
              {evidence.sourceContentHash && (
                <div>
                  <dt className="font-sans">{t("research.workflow.fileHash", { defaultValue: "File SHA-256" })}</dt>
                  <dd className="break-all text-text">{evidence.sourceContentHash}</dd>
                </div>
              )}
              {evidence.sourcePageManifestHash && (
                <div>
                  <dt className="font-sans">{t("research.workflow.pageManifestHash", { defaultValue: "Parsed-page manifest SHA-256" })}</dt>
                  <dd className="break-all text-text">{evidence.sourcePageManifestHash}</dd>
                </div>
              )}
            </dl>
          </details>
        </section>
      )}
      <div className="min-h-0 flex-1 bg-surface-2">
        {loading && (
          <div
            className="h-full bg-surface-2 p-4"
            aria-label={t("research.previewLoading", {
              defaultValue: "Loading authenticated PDF…",
            })}
          >
            <div className="h-full animate-pulse bg-surface p-5">
              <div className="h-3 w-2/3 rounded bg-border" />
              <div className="mt-6 space-y-3">
                <div className="h-2.5 w-full rounded bg-border-faint" />
                <div className="h-2.5 w-11/12 rounded bg-border-faint" />
                <div className="h-2.5 w-4/5 rounded bg-border-faint" />
              </div>
            </div>
          </div>
        )}
        {!loading && error && (
          <div className="p-5">
            <div className="flex items-start gap-3">
              <AlertTriangle size={18} className="mt-0.5 shrink-0 text-error" />
              <div>
                <p className="text-sm font-medium text-text">
                  {t("research.previewFailed", { defaultValue: "Could not open the PDF" })}
                </p>
                <p className="mt-1 break-words text-xs leading-relaxed text-muted">{error}</p>
              </div>
            </div>
          </div>
        )}
        {!loading && !error && url && selectedSource && (
          <iframe
            key={`${selectedSource.id}:${selection?.pageIndex ?? 0}`}
            src={url}
            title={t("research.pdfTitle", {
              defaultValue: "PDF preview for {{title}}",
              title: selectedSource.title,
            })}
            className="h-full w-full border-0 bg-white"
          />
        )}
        {!loading && !error && !url && (
          <div className="p-5">
            <div className="flex items-start gap-3">
              <ResearchObjectIcon
                kind={RESEARCH_OBJECT_KIND.evidence}
                size={18}
                className="mt-0.5"
              />
              <div>
                <p className="text-sm font-medium text-text">
                  {evidence || selectedSource
                    ? t("research.previewUnavailableTitle", {
                        defaultValue: "Source document preview unavailable",
                      })
                    : t("research.previewEmptyTitle", { defaultValue: "No evidence selected" })}
                </p>
                <p className="mt-1 max-w-xs text-xs leading-relaxed text-muted">
                  {evidence || selectedSource
                    ? t("research.previewUnavailableBody", {
                        defaultValue:
                          "The selected citation and provenance remain available above, but the PDF cannot be displayed here.",
                      })
                    : t("research.previewEmptyBody", {
                        defaultValue: "Choose a paper from Sources, or open a cited passage from a result.",
                      })}
                </p>
              </div>
            </div>
            {!evidence && !selectedSource && (
              <div className="mt-5 divide-y divide-border-faint border-y border-border text-xs text-muted">
                <p className="py-2.5">
                  {t("research.inspector.sourceHint", {
                    defaultValue: "Source selection opens the PDF at its current page.",
                  })}
                </p>
                <p className="py-2.5">
                  {t("research.inspector.citationHint", {
                    defaultValue: "Citation selection jumps to the exact evidence page.",
                  })}
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function WorkflowActivity({ events }: { events: WorkflowEvent[] }) {
  const { t } = useTranslation("pages");
  if (events.length === 0) {
    return (
      <div className="flex items-start gap-3 p-5">
        <Clock3 size={18} className="mt-0.5 shrink-0 text-muted" />
        <div>
          <p className="text-sm font-medium text-text">
            {t("research.workflow.noActivityTitle", { defaultValue: "No activity yet" })}
          </p>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            {t("research.workflow.noActivityBody", {
              defaultValue: "Workflow events will appear here as work progresses.",
            })}
          </p>
        </div>
      </div>
    );
  }
  const orderedEvents = [...events].sort((left, right) => left.sequence - right.sequence);
  const latestEvent = orderedEvents[orderedEvents.length - 1];
  const failureCount = orderedEvents.filter((event) => activityTone(event) === "error").length;
  const recoveryCount = orderedEvents.filter((event) => activityTone(event) === "recovered").length;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-border px-4 py-3">
        <div className="flex items-baseline gap-2">
          <h3 className="text-xs font-medium text-text">
            {t("research.workflow.runTimeline", { defaultValue: "Run timeline" })}
          </h3>
          <span className="ml-auto text-caption text-muted">
            {t("research.workflow.eventCount", {
              defaultValue: "{{count}} events",
              count: orderedEvents.length,
            })}
          </span>
        </div>
        <p className="mt-1 text-caption text-muted">
          {t("research.workflow.currentTimelineState", {
            defaultValue: "Current: {{state}}",
            state: activityMessage(latestEvent),
          })}
        </p>
        {(failureCount > 0 || recoveryCount > 0) && (
          <p className="mt-1 text-caption text-muted">
            <span className={failureCount > 0 ? "text-error" : undefined}>
              {t("research.workflow.failureEventCount", {
                defaultValue: "{{count}} failure events",
                count: failureCount,
              })}
            </span>
            <span aria-hidden="true"> · </span>
            <span className={recoveryCount > 0 ? "text-ok" : undefined}>
              {t("research.workflow.recoveryEventCount", {
                defaultValue: "{{count}} recovery events",
                count: recoveryCount,
              })}
            </span>
          </p>
        )}
      </div>
      <ol className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {orderedEvents.map((event, index) => {
          const approvalDetails = approvalActivityDetails(event);
          const details = [...activityDetails(event), ...approvalDetails];
          const tone = activityTone(event);
          return (
            <li key={event.id} className="relative border-l border-border pb-5 pl-4 last:pb-1">
              <span
                className={cn(
                  "absolute -left-1 top-1 h-2 w-2 rounded-full ring-2 ring-surface",
                  tone === "error"
                    ? "bg-error"
                    : tone === "recovered" || tone === "success"
                      ? "bg-ok"
                      : index === orderedEvents.length - 1
                        ? "bg-accent"
                        : "bg-border",
                )}
              />
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <p className="text-caption font-medium text-muted">
                    {t(`research.workflow.activityPhase.${activityPhase(event)}`, {
                      defaultValue: activityPhase(event),
                    })}
                  </p>
                  <p className="mt-0.5 text-xs font-medium leading-relaxed text-text">
                    {activityMessage(event)}
                  </p>
                </div>
                <time dateTime={event.createdAt} className="shrink-0 text-caption text-muted">
                  {formatTime(event.createdAt)}
                </time>
              </div>
              {details.length > 0 && (
                <div className="mt-1.5 space-y-1 border-t border-border-faint pt-1.5">
                  {details.slice(0, 2).map((detail) => (
                    <p key={detail} className="break-words text-caption leading-4 text-muted">
                      {detail}
                    </p>
                  ))}
                  {details.length > 2 && (
                    <details className="text-caption text-muted">
                      <summary className="min-h-7 cursor-pointer py-1 text-link hover:underline">
                        {t("research.workflow.auditDetails", { defaultValue: "Audit details" })}
                      </summary>
                      {details.slice(2).map((detail) => (
                        <p key={detail} className="mt-1 break-all font-mono text-caption text-muted">
                          {detail}
                        </p>
                      ))}
                    </details>
                  )}
                </div>
              )}
              <p className="mt-1 text-caption text-muted">
                {t("research.workflow.eventSequence", {
                  defaultValue: "Event {{sequence}}",
                  sequence: event.sequence,
                })}
              </p>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

type ActivityTone = "neutral" | "success" | "error" | "recovered";
type ActivityPhase =
  | "setup"
  | "planning"
  | "approval"
  | "execution"
  | "artifacts"
  | "review"
  | "recovery";

function activityPhase(event: WorkflowEvent): ActivityPhase {
  if (
    event.type.startsWith("approval.") ||
    event.type.includes("approval-requested") ||
    event.type === "analysis.approved" ||
    event.type === "analysis.rejected" ||
    event.type === "remote-data.approved"
  ) return "approval";
  if (
    event.type.startsWith("analysis.run-") ||
    event.type === "analysis.execution-started" ||
    event.type.startsWith("step.")
  ) return "execution";
  if (event.type === "artifact.created" || event.type === "analysis.structured-result-created") {
    return "artifacts";
  }
  if (event.type.includes("review")) return "review";
  if (
    event.type === "job.failed" ||
    event.type === "job.retried" ||
    event.type.includes("retry") ||
    event.type.includes("revision") ||
    activityTone(event) === "recovered"
  ) return "recovery";
  if (event.type.startsWith("plan.") || event.type.startsWith("analysis.spec-")) {
    return "planning";
  }
  return "setup";
}

function activityTone(event: WorkflowEvent): ActivityTone {
  if (
    event.type === "job.failed" ||
    event.type === "step.failed" ||
    event.type === "analysis.run-failed" ||
    event.type === "analysis.unsupported" ||
    event.type === "agent.loop-limit-reached" ||
    (event.type === "workflow.status-changed" &&
      ["failed", "blocked"].includes(event.data.status))
  ) return "error";
  if (
    event.type === "workflow.status-changed" &&
    ["failed", "blocked"].includes(event.data.previousStatus) &&
    ["planning", "running", "reviewing"].includes(event.data.status)
  ) return "recovered";
  if (
    event.type === "step.completed" ||
    event.type === "analysis.run-completed" ||
    event.type === "review.completed" ||
    event.type === "analysis.review-completed" ||
    (event.type === "workflow.status-changed" && event.data.status === "completed")
  ) return "success";
  return "neutral";
}

function activityDetails(event: WorkflowEvent): string[] {
  const data = event.data;
  const details: string[] = [];
  if ("errorCode" in data && typeof data.errorCode === "string") {
    details.push(`Failure code: ${data.errorCode}`);
  }
  if ("reasonCode" in data && typeof data.reasonCode === "string" && data.reasonCode) {
    details.push(`Reason: ${data.reasonCode}`);
  }
  if ("attempt" in data && typeof data.attempt === "number") {
    details.push(`Attempt: ${data.attempt}`);
  }
  if ("path" in data && typeof data.path === "string") {
    details.push(`Artifact: ${data.path}`);
  }
  if ("artifactCount" in data && typeof data.artifactCount === "number") {
    details.push(`Artifacts: ${data.artifactCount}`);
  }
  if ("verdict" in data && typeof data.verdict === "string") {
    details.push(`Verdict: ${data.verdict}`);
  }
  if ("runId" in data && typeof data.runId === "string") {
    details.push(`Run: ${data.runId}`);
  }
  if ("jobId" in data && typeof data.jobId === "string") {
    details.push(`Job: ${data.jobId}`);
  }
  return details;
}

function activityMessage(event: WorkflowEvent): string {
  const data = event.data;
  if (
    "stage" in data &&
    typeof data.stage === "string" &&
    "elapsedSeconds" in data &&
    typeof data.elapsedSeconds === "number"
  ) {
    const stage = data.stage.split("-").join(" ");
    return `Analysis ${stage} · ${formatElapsedSeconds(data.elapsedSeconds)} elapsed`;
  }
  if (
    "stepKey" in data &&
    typeof data.stepKey === "string" &&
    "status" in data &&
    typeof data.status === "string"
  ) {
    return `${data.stepKey.split("-").join(" ")}: ${data.status}`;
  }
  if (event.type === "job.failed") {
    return `${event.data.kind.split("-").join(" ")} job failed`;
  }
  if (event.type === "job.retried") {
    return `${event.data.kind.split("-").join(" ")} job retried`;
  }
  if (event.type === "analysis.run-started" || event.type === "analysis.execution-started") {
    return "Isolated analysis run started";
  }
  if (event.type === "analysis.run-completed") {
    return `Analysis run completed with ${event.data.artifactCount} artifacts`;
  }
  if (event.type === "analysis.run-failed") return "Analysis run failed safely";
  if (event.type === "artifact.created") return `Artifact recorded: ${event.data.path}`;
  if (event.type === "analysis.structured-result-created") {
    return "Structured result recorded";
  }
  if (event.type === "agent.step-retry-requested") {
    return `Retry requested for ${event.data.targetStepKey.split("-").join(" ")}`;
  }
  if ("status" in data && typeof data.status === "string") {
    return `Workflow ${data.status.split("-").join(" ")}`;
  }
  if ("verdict" in data) return `Review ${data.verdict.split("-").join(" ")}`;
  if (
    "approvalId" in data &&
    "action" in data &&
    typeof data.action === "string"
  ) {
    return `Approval requested: ${data.action.split("-").join(" ")}`;
  }
  if (
    "provider" in data &&
    typeof data.provider === "string" &&
    "dataCategories" in data &&
    Array.isArray(data.dataCategories)
  ) {
    const destination =
      "model" in data && typeof data.model === "string"
        ? data.model
        : data.provider;
    return `Remote goal disclosure approved for ${destination}`;
  }
  if ("planId" in data && "version" in data) return `Plan version ${data.version} updated`;
  if ("requested" in data) return data.requested ? "Cancellation requested" : "Cancellation updated";
  return event.type.split(".").join(" ");
}

function formatElapsedSeconds(value: number): string {
  return `${Math.max(0, value).toFixed(1)} seconds`;
}

function approvalActivityDetails(event: WorkflowEvent): string[] {
  const data = event.data;
  if (!("approvalId" in data) || !("payloadSha256" in data)) return [];
  const details: string[] = [];
  if ("riskLevel" in data && typeof data.riskLevel === "string") {
    details.push(`Risk: ${data.riskLevel}`);
  }
  if ("reason" in data && typeof data.reason === "string") {
    details.push(`Reason: ${data.reason}`);
  }
  if ("affectedResources" in data && Array.isArray(data.affectedResources)) {
    details.push(...data.affectedResources.map((resource) => `Resource: ${resource}`));
  }
  if (typeof data.approvalSchemaVersion === "string") {
    details.push(`Schema: ${data.approvalSchemaVersion}`);
  }
  if (typeof data.payloadSha256 === "string") {
    details.push(`Approval envelope: ${data.payloadSha256}`);
  }
  return details;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}
