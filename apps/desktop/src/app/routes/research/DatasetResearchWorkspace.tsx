import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  Database,
  FileCode2,
  FolderArchive,
  Loader2,
  RefreshCw,
  Table2,
} from "lucide-react";
import type {
  AgentResearchWorkflowSnapshot,
  DatasetAnalysisWorkflowSnapshot,
  ResearchSource,
  ResearchWorkflowSnapshot,
  WorkflowEvent,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { DatasetWorkflowDetails } from "./DatasetWorkflowDetails";
import {
  WorkflowNeedsAttention,
  WorkflowProgress,
} from "./WorkflowExecution";

type DatasetSurface = "dataset" | "analysis" | "results" | "notebook" | "artifacts";
type DatasetSnapshot = DatasetAnalysisWorkflowSnapshot | AgentResearchWorkflowSnapshot;

const SURFACES: Array<{ id: DatasetSurface; label: string; icon: typeof Database }> = [
  { id: "dataset", label: "Dataset", icon: Database },
  { id: "analysis", label: "Analysis", icon: BarChart3 },
  { id: "results", label: "Results", icon: Table2 },
  { id: "notebook", label: "Notebook", icon: FileCode2 },
  { id: "artifacts", label: "Artifacts", icon: FolderArchive },
];
// eslint-disable-next-line i18next/no-literal-string -- canonical internal detail view discriminant
const DATASET_DETAIL_VIEW = "dataset" as const;
// eslint-disable-next-line i18next/no-literal-string -- canonical internal detail view discriminant
const ANALYSIS_DETAIL_VIEW = "analysis" as const;

interface DatasetResearchWorkspaceProps {
  projectTitle: string | null;
  sources: ResearchSource[];
  snapshot: ResearchWorkflowSnapshot | null;
  events: WorkflowEvent[];
  mutating: boolean;
  importing: boolean;
  serviceReady: boolean;
  literatureAvailable: boolean;
  onImportDatasetRequest: () => void;
  onOpenWorkflow: () => void;
  onOpenLiterature: () => void;
  onRefresh: () => Promise<void>;
  onDecision: (decision: "approved" | "rejected") => Promise<void>;
  onResolveAgentDecision?: (decision: "approved" | "rejected") => Promise<void>;
  onCancel: () => Promise<void>;
  onRetry: () => Promise<void>;
  onResume: () => Promise<void>;
  onAcceptReviewWarnings: () => Promise<void>;
}

function hasDatasetState(snapshot: ResearchWorkflowSnapshot | null): snapshot is DatasetSnapshot {
  return Boolean(
    snapshot &&
      (snapshot.workflow.workflowType === "dataset-analysis" ||
        snapshot.datasetProfile ||
        snapshot.analysisIntent ||
        snapshot.analysisRun),
  );
}

function sourceStatus(source: ResearchSource, t: TFunction<"pages">): string {
  return t(`research.sourceStatus.${source.ingestionStatus}`);
}

export function datasetWorkflowDisplayState(
  status: ResearchWorkflowSnapshot["workflow"]["status"] | null,
  events: readonly WorkflowEvent[],
): string | null {
  void events;
  if (!status) return null;
  if (status === "waiting-plan-approval") return "Awaiting approval";
  if (status === "running") return "Running";
  if (status === "reviewing") return "Reviewing";
  if (status === "failed" || status === "blocked") return "Failed";
  if (status === "completed") return "Completed";
  return status.split("-").join(" ");
}

export function shouldDefaultToDatasetWorkspace(
  sources: readonly ResearchSource[],
  workflow: { workflowType: string | null; status: string } | null,
): boolean {
  if (workflow?.workflowType === "dataset-analysis") return true;
  const hasDataset = sources.some((source) => source.sourceKind === "dataset");
  const hasPdf = sources.some((source) => source.sourceKind === "pdf");
  return hasDataset && !hasPdf;
}

export function DatasetResearchWorkspace({
  projectTitle,
  sources,
  snapshot,
  events,
  mutating,
  importing,
  serviceReady,
  literatureAvailable,
  onImportDatasetRequest,
  onOpenWorkflow,
  onOpenLiterature,
  onRefresh,
  onDecision,
  onResolveAgentDecision,
  onCancel,
  onRetry,
  onResume,
  onAcceptReviewWarnings,
}: DatasetResearchWorkspaceProps) {
  const { t } = useTranslation("pages");
  const initialSurface =
    snapshot?.workflow.status === "completed" ? "results" : "dataset";
  const [surface, setSurface] = useState<DatasetSurface>(initialSurface);
  const datasetSources = useMemo(
    () => sources.filter((source) => source.sourceKind === "dataset"),
    [sources],
  );
  const datasetSnapshot = hasDatasetState(snapshot) ? snapshot : null;
  const boundSourceId = datasetSnapshot?.workflow.datasetSourceId ?? null;
  const selectedSource =
    datasetSources.find((source) => source.id === boundSourceId) ?? datasetSources[0] ?? null;
  const workflowState = datasetWorkflowDisplayState(datasetSnapshot?.workflow.status ?? null, events);
  const workflowLabel = datasetSnapshot ? t(`research.workflowStatus.${datasetSnapshot.workflow.status}`) : null;
  const completedContinuity =
    datasetSnapshot?.workflow.status === "completed" &&
    datasetSnapshot.analysisRun?.status === "completed"
      ? `projectId=${encodeURIComponent(datasetSnapshot.workflow.projectId)}&workflowId=${encodeURIComponent(datasetSnapshot.workflow.id)}`
      : null;

  useEffect(() => {
    if (datasetSnapshot?.workflow.status === "completed") {
      setSurface("results");
    }
  }, [datasetSnapshot?.workflow.id, datasetSnapshot?.workflow.status]);

  return (
    <section className="dataset-workspace-container flex h-full min-h-0 flex-col bg-bg" data-testid="dataset-research-workspace">
      <header className="flex min-h-14 shrink-0 flex-wrap items-center gap-3 border-b border-border bg-surface px-4 py-1.5">
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-ui font-semibold text-text">
            {projectTitle || t("research.literature.datasetTitle", { defaultValue: "Dataset analysis" })}
          </h1>
          <p className="truncate text-caption text-muted">
            {selectedSource ? selectedSource.title : t("research.literature.noDataset", { defaultValue: "No dataset imported" })}
          </p>
        </div>
        {workflowState && workflowLabel && (
          <span
            className={cn(
              "state-badge",
              workflowState === "Failed"
                ? "state-badge-error"
                : workflowState === "Completed"
                  ? "state-badge-ok"
                  : workflowState === "Awaiting approval"
                    ? "state-badge-warn"
                    : "state-badge-neutral",
            )}
            aria-label={t("research.literature.workflowStatus", { status: workflowLabel })}
          >
            <span aria-hidden="true" />{workflowLabel}
          </span>
        )}
        {completedContinuity && (
          <>
            <a
              href={`/notebooks?${completedContinuity}`}
              className="compact-button primary-button"
            >
              <FileCode2 size={13} aria-hidden />
              {t("research.literature.openNotebook", {
                defaultValue: "Open notebook",
              })}
            </a>
            <a
              href={`/files?${completedContinuity}`}
              className="compact-button secondary-button"
            >
              <FolderArchive size={13} aria-hidden />
              {t("research.literature.viewArtifacts", {
                defaultValue: "View artifacts",
              })}
            </a>
          </>
        )}
        <button
          type="button"
          onClick={onOpenLiterature}
          disabled={!literatureAvailable}
          title={literatureAvailable ? undefined : t("research.literature.importPdfToOpen", { defaultValue: "Import a PDF to open the literature workspace" })}
          className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"
        >
          {t("research.literature.literatureReview", { defaultValue: "Literature review" })}
        </button>
        <button type="button" onClick={onOpenWorkflow} className="compact-button secondary-button">
          {t("research.literature.workflowDetails", { defaultValue: "Workflow details" })}
        </button>
        <button
          type="button"
          onClick={() => void onRefresh()}
          disabled={mutating}
          className="icon-button"
          aria-label={t("research.literature.refreshDataset", { defaultValue: "Refresh dataset workflow" })}
        >
          <RefreshCw size={14} className={cn(mutating && "animate-spin")} />
        </button>
      </header>

      <nav className="flex min-h-12 shrink-0 items-end overflow-x-auto border-b border-border bg-surface px-4" aria-label={t("research.literature.datasetWorkspace")}>
        {SURFACES.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => setSurface(item.id)}
              aria-current={surface === item.id ? "page" : undefined}
              className={cn(
                "touch-target flex min-h-11 shrink-0 items-center gap-2 border-b-2 px-3 text-xs font-medium",
                surface === item.id
                  ? "border-accent text-text"
                  : "border-transparent text-muted hover:text-text",
              )}
            >
              <Icon size={14} />{t(`research.literature.datasetSurfaces.${item.id}`, { defaultValue: item.label })}
            </button>
          );
        })}
      </nav>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-[86rem] px-4 py-5 xl:px-6">
          {surface === "dataset" && (
            <DatasetSurfaceSummary
              sources={datasetSources}
              selectedSource={selectedSource}
              importing={importing}
              serviceReady={serviceReady}
              onImportDatasetRequest={onImportDatasetRequest}
            />
          )}

          {!datasetSnapshot && surface !== "dataset" && (
            <section className="border-y border-border bg-surface px-4 py-10">
              <h2 className="text-base font-semibold text-text">{t("research.literature.noDatasetWorkflow", { defaultValue: "No dataset workflow selected" })}</h2>
              <p className="mt-1 max-w-[68ch] text-xs leading-relaxed text-muted">
                {t("research.literature.noDatasetWorkflowBody", { defaultValue: "Create or select a dataset-analysis workflow to populate this surface. Analysis state is never inferred from imported files alone." })}
              </p>
              <button type="button" onClick={onOpenWorkflow} className="compact-button primary-button mt-4">
                {t("research.literature.openWorkflowSetup", { defaultValue: "Open workflow setup" })}
              </button>
            </section>
          )}

          {datasetSnapshot && surface === "dataset" && (
            <div className="mt-4">
              <DatasetWorkflowDetails
                snapshot={datasetSnapshot}
                view={DATASET_DETAIL_VIEW}
                mutating={mutating}
                onDecision={onDecision}
                onResolveAgentDecision={onResolveAgentDecision}
                onCancel={onCancel}
                onAcceptReviewWarnings={onAcceptReviewWarnings}
              />
            </div>
          )}

          {datasetSnapshot && surface === "analysis" && (
            <div className="space-y-4">
              <WorkflowProgress snapshot={datasetSnapshot} />
              {(["failed", "blocked", "cancelled"] as const).includes(
                datasetSnapshot.workflow.status as "failed" | "blocked" | "cancelled",
              ) && (
                <WorkflowNeedsAttention
                  snapshot={datasetSnapshot}
                  mutating={mutating}
                  onRetry={onRetry}
                  onResume={onResume}
                  onNew={onOpenWorkflow}
                  onOpenActivity={onOpenWorkflow}
                />
              )}
              <DatasetWorkflowDetails
                snapshot={datasetSnapshot}
                view={ANALYSIS_DETAIL_VIEW}
                mutating={mutating}
                onDecision={onDecision}
                onResolveAgentDecision={onResolveAgentDecision}
                onCancel={onCancel}
                onAcceptReviewWarnings={onAcceptReviewWarnings}
              />
            </div>
          )}

          {datasetSnapshot && surface !== "dataset" && surface !== "analysis" && (
            <DatasetWorkflowDetails
              snapshot={datasetSnapshot}
              view={surface}
              mutating={mutating}
              onDecision={onDecision}
              onResolveAgentDecision={onResolveAgentDecision}
              onCancel={onCancel}
              onAcceptReviewWarnings={onAcceptReviewWarnings}
            />
          )}
        </div>
      </div>
    </section>
  );
}

function DatasetSurfaceSummary({
  sources,
  selectedSource,
  importing,
  serviceReady,
  onImportDatasetRequest,
}: {
  sources: ResearchSource[];
  selectedSource: ResearchSource | null;
  importing: boolean;
  serviceReady: boolean;
  onImportDatasetRequest: () => void;
}) {
  const { t } = useTranslation("pages");
  return (
    <section className="border-y border-border bg-surface">
      <header className="flex flex-wrap items-center gap-3 border-b border-border-faint px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-text">{t("research.literature.datasetSummary", { defaultValue: "Dataset summary" })}</h2>
          <p className="mt-0.5 text-caption text-muted">
            {t("research.literature.projectDatasets", { defaultValue: "{{count}} project dataset(s) · canonical source records", count: sources.length })}
          </p>
        </div>
        <button
          type="button"
          onClick={onImportDatasetRequest}
          disabled={!serviceReady || importing}
          className="compact-button primary-button"
        >
          {importing && <Loader2 size={13} className="animate-spin" />}
          {importing ? t("research.workflow.importingDataset", { defaultValue: "Importing…" }) : t("research.workflow.importDataset", { defaultValue: "Import CSV" })}
        </button>
      </header>
      {selectedSource ? (
        <div className="dataset-source-grid grid gap-0 divide-y divide-border-faint">
          <SourceDatum label={t("research.literature.file", { defaultValue: "File" })} value={selectedSource.title} />
          <SourceDatum label={t("research.literature.importState")} value={sourceStatus(selectedSource, t)} />
          <SourceDatum label={t("research.literature.workspacePath", { defaultValue: "Workspace path" })} value={selectedSource.localPath} mono />
          <SourceDatum label={t("research.literature.contentHash", { defaultValue: "Content SHA-256" })} value={selectedSource.contentHash} mono compact />
        </div>
      ) : (
        <div className="flex items-start gap-3 px-4 py-8">
          <AlertTriangle size={15} className="mt-0.5 shrink-0 text-warn" />
          <div>
            <h3 className="text-sm font-medium text-text">{t("research.literature.importDatasetEmpty", { defaultValue: "Import a public or synthetic CSV" })}</h3>
            <p className="mt-1 max-w-[68ch] text-xs leading-relaxed text-muted">
              {t("research.literature.importDatasetEmptyBody", { defaultValue: "Spark records the source first. Profiling, analysis, results, notebooks, and artifacts remain empty until their canonical stages exist." })}
            </p>
          </div>
        </div>
      )}
    </section>
  );
}

function SourceDatum({
  label,
  value,
  mono = false,
  compact = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
  compact?: boolean;
}) {
  return (
    <div className="min-w-0 px-4 py-3">
      <p className="text-caption text-muted">{label}</p>
      <p
        className={cn(
          "mt-1 truncate text-xs font-medium text-text",
          mono && "font-mono text-caption",
        )}
        title={compact ? value : undefined}
      >
        {compact && value.length > 20 ? `${value.slice(0, 10)}…${value.slice(-6)}` : value}
      </p>
    </div>
  );
}
