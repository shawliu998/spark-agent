import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  BookOpen,
  Clock3,
  Loader2,
  ScrollText,
  ShieldCheck,
} from "lucide-react";
import type {
  ResearchSource,
  ResearchWorkflowReview,
  WorkflowEvent,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";
import { WorkflowReviewSummary } from "./WorkflowWorkspace";
import { useSourcePdfBlob } from "./useSourcePdfBlob";

export type ResearchInspectorTab = "evidence" | "review" | "activity";

export interface ResearchPdfSelection {
  sourceId: string;
  pageIndex: number;
  evidenceId?: string;
}

export interface ResearchInspectorProps {
  activeTab: ResearchInspectorTab;
  onTabChange: (tab: ResearchInspectorTab) => void;
  selectedSource: ResearchSource | null;
  selection: ResearchPdfSelection | null;
  review: ResearchWorkflowReview | null;
  events: WorkflowEvent[];
}

export function ResearchInspector({
  activeTab,
  onTabChange,
  selectedSource,
  selection,
  review,
  events,
}: ResearchInspectorProps) {
  const { t } = useTranslation("pages");
  const pdf = useSourcePdfBlob(
    selectedSource?.id ?? null,
    selection?.pageIndex ?? 0,
  );
  const tabs: Array<{ id: ResearchInspectorTab; label: string; icon: React.ReactNode }> = [
    {
      id: "evidence",
      label: t("research.inspector.evidence", { defaultValue: "Evidence" }),
      icon: <BookOpen size={13} />,
    },
    {
      id: "review",
      label: t("research.inspector.review", { defaultValue: "Review" }),
      icon: <ShieldCheck size={13} />,
    },
    {
      id: "activity",
      label: t("research.inspector.activity", { defaultValue: "Activity" }),
      icon: <ScrollText size={13} />,
    },
  ];

  return (
    <section className="flex w-[38%] min-w-[18rem] shrink-0 flex-col border-l border-border bg-surface xl:w-[44%]">
      <div className="flex h-[53px] shrink-0 items-end gap-1 border-b border-border px-3" role="tablist">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            onClick={() => onTabChange(tab.id)}
            className={cn(
              "flex h-9 items-center gap-1.5 border-b-2 px-2 text-[11px] font-medium",
              activeTab === tab.id
                ? "border-accent text-text"
                : "border-transparent text-muted hover:text-text",
            )}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1">
        {activeTab === "evidence" && (
          <EvidencePanel
            selectedSource={selectedSource}
            selection={selection}
            url={pdf.url}
            loading={pdf.loading}
            error={pdf.error}
          />
        )}
        {activeTab === "review" && (
          <div className="h-full overflow-y-auto">
            <WorkflowReviewSummary review={review} />
          </div>
        )}
        {activeTab === "activity" && <WorkflowActivity events={events} />}
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
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-2.5">
        <BookOpen size={14} className="shrink-0 text-muted" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium text-text">
            {selectedSource?.title ?? t("research.previewTitle", { defaultValue: "Source preview" })}
          </p>
          {selectedSource && (
            <p className="text-[10px] text-muted">
              {t("research.previewPage", {
                defaultValue: "Page {{page}}",
                page: (selection?.pageIndex ?? 0) + 1,
              })}
            </p>
          )}
        </div>
        {selection?.evidenceId && (
          <span className="rounded-full bg-ok/10 px-2 py-0.5 text-[10px] font-medium text-ok ring-1 ring-ok/20">
            {t("research.evidenceBadge", { defaultValue: "Evidence" })}
          </span>
        )}
      </div>
      <div className="min-h-0 flex-1 bg-surface-2">
        {loading && (
          <div className="flex h-full items-center justify-center gap-2 text-xs text-muted">
            <Loader2 size={14} className="animate-spin" />
            {t("research.previewLoading", { defaultValue: "Loading authenticated PDF…" })}
          </div>
        )}
        {!loading && error && (
          <div className="flex h-full flex-col items-center justify-center px-6 text-center">
            <AlertTriangle size={22} className="text-error" />
            <p className="mt-3 text-sm font-medium text-text">
              {t("research.previewFailed", { defaultValue: "Could not open the PDF" })}
            </p>
            <p className="mt-1 max-w-sm break-words text-xs leading-relaxed text-muted">{error}</p>
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
          <div className="flex h-full flex-col items-center justify-center px-6 text-center">
            <BookOpen size={25} strokeWidth={1.4} className="text-muted" />
            <p className="mt-3 text-sm font-medium text-text">
              {t("research.previewEmptyTitle", { defaultValue: "Open a source" })}
            </p>
            <p className="mt-1 max-w-xs text-xs leading-relaxed text-muted">
              {t("research.previewEmptyBody", {
                defaultValue: "Select a paper or click an evidence quote to jump to its page.",
              })}
            </p>
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
      <div className="flex h-full flex-col items-center justify-center px-6 text-center">
        <Clock3 size={23} className="text-muted" />
        <p className="mt-3 text-sm font-medium text-text">
          {t("research.workflow.noActivityTitle", { defaultValue: "No activity yet" })}
        </p>
        <p className="mt-1 text-xs text-muted">
          {t("research.workflow.noActivityBody", {
            defaultValue: "Workflow events will appear here as work progresses.",
          })}
        </p>
      </div>
    );
  }
  return (
    <ol className="h-full overflow-y-auto p-4">
      {[...events].reverse().map((event) => (
        <li key={event.id} className="relative border-l border-border pb-4 pl-4 last:pb-0">
          <span className="absolute -left-1 top-1 h-2 w-2 rounded-full bg-accent ring-2 ring-surface" />
          <div className="flex items-start gap-2">
            <p className="min-w-0 flex-1 text-xs font-medium leading-relaxed text-text">
              {activityMessage(event)}
            </p>
            <span className="shrink-0 text-[9px] text-muted">
              {formatTime(event.createdAt)}
            </span>
          </div>
          <p className="mt-1 text-[10px] text-muted">
            {t("research.workflow.eventSequence", {
              defaultValue: "Event {{sequence}}",
              sequence: event.sequence,
            })}
          </p>
          {approvalActivityDetails(event).map((detail) => (
            <p key={detail} className="mt-1 break-all font-mono text-[9px] text-muted">
              {detail}
            </p>
          ))}
        </li>
      ))}
    </ol>
  );
}

function activityMessage(event: WorkflowEvent): string {
  const data = event.data;
  if (
    "stepKey" in data &&
    typeof data.stepKey === "string" &&
    "status" in data &&
    typeof data.status === "string"
  ) {
    return `${data.stepKey.split("-").join(" ")}: ${data.status}`;
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

function approvalActivityDetails(event: WorkflowEvent): string[] {
  const data = event.data;
  if (!("approvalId" in data) || !("payloadSha256" in data)) return [];
  const details: string[] = [];
  if (typeof data.riskLevel === "string") details.push(`Risk: ${data.riskLevel}`);
  if (typeof data.reason === "string") details.push(`Reason: ${data.reason}`);
  if (Array.isArray(data.affectedResources)) {
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
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(date);
}
