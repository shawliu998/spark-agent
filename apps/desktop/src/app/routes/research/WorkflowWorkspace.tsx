import { useRef, type ChangeEvent, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  FileUp,
  Loader2,
  Table2,
} from "lucide-react";
import type {
  AgentResearchWorkflowSnapshot,
  DatasetAnalysisWorkflowSnapshot,
  InteractionRequest,
  InteractionResponseValue,
  ResearchSource,
  ResearchWorkflowSnapshot,
  ScienceCoreModelDestination,
  WorkflowEvidenceRelationship,
} from "@spark/research-domain";
import type {
  ResearchWorkflowCreateOptions,
  WorkflowConnectionState,
} from "./useResearchWorkflow";
import { cn } from "@/lib/cn";
import { DatasetWorkflowDetails } from "./DatasetWorkflowDetails";
import { ClarificationCard } from "./ClarificationCard";
import {
  WorkflowNeedsAttention,
  WorkflowProgress,
} from "./WorkflowExecution";
import { WorkflowGoalComposer } from "./WorkflowGoalComposer";
import { WorkflowPlanCard } from "./WorkflowPlanCard";
import { WorkflowResultView } from "./WorkflowResultView";
import { RESEARCH_OBJECT_KIND, ResearchObjectIcon } from "./ResearchObjectIcon";
import {
  WorkflowError,
  WorkflowHeader,
  WorkflowWaiting,
} from "./WorkflowWorkspaceChrome";
import { workflowNeedsAttention } from "./workflowModel";

export { WorkflowReviewSummary } from "./WorkflowReviewSummary";

const PROJECT_SETUP_KIND = "project" as const;
const SOURCE_SETUP_KIND = "sources" as const;

function isDatasetDetailsSnapshot(
  snapshot: ResearchWorkflowSnapshot,
): snapshot is DatasetAnalysisWorkflowSnapshot | AgentResearchWorkflowSnapshot {
  return snapshot.workflow.workflowType === "dataset-analysis";
}

export interface WorkflowWorkspaceProps {
  snapshot: ResearchWorkflowSnapshot | null;
  interactions?: InteractionRequest[];
  sources: ResearchSource[];
  loading: boolean;
  loadingInteractions?: boolean;
  mutating: boolean;
  connection: WorkflowConnectionState;
  error: string | null;
  canStart: boolean;
  projectReady?: boolean;
  serviceReady?: boolean;
  sourcesLoading?: boolean;
  importingSource?: boolean;
  importingDataset: boolean;
  projectTitle?: string;
  creatingProject?: boolean;
  remoteDestination: ScienceCoreModelDestination | null;
  legacyContent?: ReactNode;
  onCreate: (
    goal: string,
    options: ResearchWorkflowCreateOptions,
  ) => Promise<void>;
  onProjectTitleChange?: (title: string) => void;
  onCreateProject?: (event: FormEvent) => void | Promise<void>;
  onRespondToInteraction?: (
    interactionId: string,
    response: InteractionResponseValue,
  ) => Promise<void>;
  onApprovePlan: () => Promise<void>;
  onDecideAnalysis: (decision: "approved" | "rejected") => Promise<void>;
  onResolveAgentDecision?: (decision: "approved" | "rejected") => Promise<void>;
  onAcceptReviewWarnings: () => Promise<void>;
  onImportDataset: (event: ChangeEvent<HTMLInputElement>) => void;
  onImportPdfRequest?: () => void;
  onCancel: () => Promise<void>;
  onRetry: () => Promise<void>;
  onResume: () => Promise<void>;
  onRefresh: () => Promise<void>;
  onNew: () => void;
  onSelectEvidence: (evidence: WorkflowEvidenceRelationship) => void;
  onOpenReview: () => void;
  onOpenActivity: () => void;
}

export function WorkflowWorkspace({
  snapshot,
  interactions = [],
  sources,
  loading,
  loadingInteractions = false,
  mutating,
  connection,
  error,
  canStart,
  projectReady = true,
  serviceReady = true,
  sourcesLoading = false,
  importingSource = false,
  importingDataset,
  projectTitle = "",
  creatingProject = false,
  remoteDestination,
  legacyContent,
  onCreate,
  onProjectTitleChange = () => {},
  onCreateProject = () => {},
  onRespondToInteraction = async () => {},
  onApprovePlan,
  onDecideAnalysis,
  onResolveAgentDecision,
  onAcceptReviewWarnings,
  onImportDataset,
  onImportPdfRequest = () => {},
  onCancel,
  onRetry,
  onResume,
  onRefresh,
  onNew,
  onSelectEvidence,
  onOpenReview,
  onOpenActivity,
}: WorkflowWorkspaceProps) {
  const { t } = useTranslation("pages");

  if (!snapshot) {
    const readySources = sources.filter(
      (source) => source.ingestionStatus === "ready",
    );
    const showSourceSetup = projectReady && readySources.length === 0;
    return (
      <div className="space-y-4">
        {error && <WorkflowError message={error} onRefresh={onRefresh} />}
        {!projectReady && (
          <ResearchWorkspaceStart
            kind={PROJECT_SETUP_KIND}
            serviceReady={serviceReady}
            sources={sources}
            sourcesLoading={sourcesLoading}
            importingSource={importingSource}
            importingDataset={importingDataset}
            projectTitle={projectTitle}
            creatingProject={creatingProject}
            onProjectTitleChange={onProjectTitleChange}
            onCreateProject={onCreateProject}
            onImportPdfRequest={onImportPdfRequest}
            onImportDataset={onImportDataset}
          />
        )}
        {showSourceSetup && (
          <ResearchWorkspaceStart
            kind={SOURCE_SETUP_KIND}
            serviceReady={serviceReady}
            sources={sources}
            sourcesLoading={sourcesLoading}
            importingSource={importingSource}
            importingDataset={importingDataset}
            projectTitle={projectTitle}
            creatingProject={creatingProject}
            onProjectTitleChange={onProjectTitleChange}
            onCreateProject={onCreateProject}
            onImportPdfRequest={onImportPdfRequest}
            onImportDataset={onImportDataset}
          />
        )}
        {projectReady && readySources.length > 0 && (
          <>
            <WorkflowGoalComposer
              canStart={canStart}
              busy={mutating}
              sources={sources}
              importingDataset={importingDataset}
              remoteDestination={remoteDestination}
              onCreate={onCreate}
              onImportDataset={onImportDataset}
            />
            {legacyContent}
          </>
        )}
      </div>
    );
  }

  const { workflow, plan } = snapshot;
  const showAttention = workflowNeedsAttention(workflow.status);
  const pendingInteractions = interactions.filter(
    (interaction) => interaction.status === "pending",
  );
  const showPendingInteractions =
    workflow.status === "waiting-clarification" ||
    (workflow.status === "planning" && pendingInteractions.length > 0);
  const revisableInteraction =
    workflow.status === "planning" || workflow.status === "waiting-plan-approval"
      ? [...interactions]
          .reverse()
          .find((interaction) => interaction.status === "answered") ?? null
      : null;

  return (
    <div className="space-y-4">
      <WorkflowHeader
        snapshot={snapshot}
        mutating={mutating}
        connection={connection}
        onCancel={onCancel}
        onNew={onNew}
        onOpenActivity={onOpenActivity}
      />

      {error && <WorkflowError message={error} onRefresh={onRefresh} />}

      {loading &&
        !plan &&
        workflow.status !== "routing" &&
        workflow.status !== "waiting-clarification" &&
        workflow.status !== "unsupported" && (
        <WorkflowWaiting
          label={t("research.workflow.loading", {
            defaultValue: "Loading workflow state…",
          })}
        />
      )}

      {workflow.status === "routing" && (
        <WorkflowWaiting
          label={t("research.workflow.routing", {
            defaultValue: "Understanding the goal and validating selected sources…",
          })}
        />
      )}

      {showPendingInteractions &&
        pendingInteractions.map((interaction) => (
          <ClarificationCard
            key={interaction.id}
            interaction={interaction}
            mutating={mutating}
            onRespond={onRespondToInteraction}
          />
        ))}

      {workflow.status === "waiting-clarification" &&
        loadingInteractions &&
        !interactions.some((interaction) => interaction.status === "pending") && (
          <WorkflowWaiting
            label={t("research.workflow.loadingClarification", {
              defaultValue: "Loading the saved clarification request…",
            })}
          />
        )}

      {workflow.status === "waiting-clarification" &&
        !loadingInteractions &&
        !interactions.some((interaction) => interaction.status === "pending") && (
          <div className="flex items-start gap-2 rounded-card border border-warn/30 bg-warn/5 p-4 text-xs text-muted">
            <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warn" />
            {t("research.workflow.clarificationMissing", {
              defaultValue:
                "This workflow is waiting for clarification, but no pending request was returned. Refresh the task.",
            })}
          </div>
        )}

      {workflow.status === "unsupported" && (
        <div className="flex items-start gap-2 rounded-card border border-warn/30 bg-warn/5 p-4 text-xs text-muted">
          <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warn" />
          <div>
            <p className="font-medium text-text">
              {t("research.workflow.unsupportedTitle", {
                defaultValue: "This goal is outside the supported research scope",
              })}
            </p>
            <p className="mt-1 leading-relaxed">
              {"statusReason" in workflow && workflow.statusReason?.userMessage
                ? workflow.statusReason.userMessage
                : "intentDecision" in snapshot &&
                  snapshot.intentDecision?.reasoningSummary
                ? snapshot.intentDecision.reasoningSummary
                : t("research.workflow.unsupportedFallback", {
                    defaultValue:
                      "Spark cannot safely route this goal with the selected sources.",
                  })}
            </p>
          </div>
        </div>
      )}

      {revisableInteraction && (
        <ClarificationCard
          interaction={revisableInteraction}
          mutating={mutating}
          onRespond={onRespondToInteraction}
        />
      )}

      {workflow.status === "planning" &&
        !plan &&
        pendingInteractions.length === 0 && (
          <WorkflowWaiting
            label={t("research.workflow.planning", {
              defaultValue:
                workflow.workflowType === "dataset-analysis"
                  ? "Validating the dataset and preparing a typed four-step plan…"
                  : "Inspecting the project and preparing a three-step plan…",
            })}
          />
        )}

      {plan && workflow.status === "waiting-plan-approval" && (
        <WorkflowPlanCard
          snapshot={snapshot}
          sources={sources}
          mutating={mutating}
          onApprove={onApprovePlan}
          onCancel={onCancel}
        />
      )}

      {plan &&
        (workflow.status === "running" ||
          workflow.status === "reviewing" ||
          showAttention) && <WorkflowProgress snapshot={snapshot} />}

      {showAttention && (
        <WorkflowNeedsAttention
          snapshot={snapshot}
          mutating={mutating}
          onRetry={onRetry}
          onResume={onResume}
          onNew={onNew}
          onOpenActivity={onOpenActivity}
        />
      )}

      {isDatasetDetailsSnapshot(snapshot) && (
        <DatasetWorkflowDetails
          snapshot={snapshot}
          mutating={mutating}
          onDecision={onDecideAnalysis}
          onResolveAgentDecision={onResolveAgentDecision}
          onCancel={onCancel}
          onAcceptReviewWarnings={onAcceptReviewWarnings}
        />
      )}

      {snapshot.result && (
        <WorkflowResultView
          snapshot={snapshot}
          sources={sources}
          onSelectEvidence={onSelectEvidence}
          onOpenReview={onOpenReview}
        />
      )}

      {workflow.workflowType === "literature-synthesis" &&
        workflow.status === "completed" &&
        !snapshot.result && (
          <div className="flex items-start gap-2 rounded-card border border-warn/30 bg-warn/5 p-4 text-xs text-muted">
            <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warn" />
            {t("research.workflow.resultMissing", {
              defaultValue:
                "The workflow completed, but no research result was returned.",
            })}
          </div>
        )}
    </div>
  );
}

function ResearchWorkspaceStart({
  kind,
  serviceReady,
  sources,
  sourcesLoading,
  importingSource,
  importingDataset,
  projectTitle,
  creatingProject,
  onProjectTitleChange,
  onCreateProject,
  onImportPdfRequest,
  onImportDataset,
}: {
  kind: "project" | "sources";
  serviceReady: boolean;
  sources: ResearchSource[];
  sourcesLoading: boolean;
  importingSource: boolean;
  importingDataset: boolean;
  projectTitle: string;
  creatingProject: boolean;
  onProjectTitleChange: (title: string) => void;
  onCreateProject: (event: FormEvent) => void | Promise<void>;
  onImportPdfRequest: () => void;
  onImportDataset: (event: ChangeEvent<HTMLInputElement>) => void;
}) {
  const { t } = useTranslation("pages");
  const datasetInput = useRef<HTMLInputElement>(null);
  const preparing = sourcesLoading || sources.some(
    (source) =>
      source.ingestionStatus === "pending" ||
      source.ingestionStatus === "processing",
  );
  const failedCount = sources.filter(
    (source) => source.ingestionStatus === "failed",
  ).length;

  if (kind === "project") {
    return (
      <section className="w-full max-w-2xl py-10 lg:py-12">
        <h2 className="text-xl font-semibold tracking-[-0.015em] text-text">
          {t("research.empty.projectTitle", {
            defaultValue: "Create a research project",
          })}
        </h2>
        <p className="mt-2 max-w-xl text-sm leading-6 text-muted">
          {t("research.empty.projectBody", {
            defaultValue:
              "A project keeps sources, plans, approvals, evidence, and results together in one reviewable record.",
          })}
        </p>
        <p className="mt-4 text-xs font-medium text-muted">
          {t("research.projectRecord", {
            defaultValue: "Local sources · approval before execution · traceable citations",
          })}
        </p>
        <form
          onSubmit={(event) => void onCreateProject(event)}
          className="mt-7 w-full max-w-xl"
        >
          <label
            htmlFor="research-project-title"
            className="mb-2 block text-xs font-medium text-text"
          >
            {t("research.projectNameLabel", { defaultValue: "Project name" })}
          </label>
          <div className="flex flex-col gap-2.5 sm:flex-row">
            <input
              id="research-project-title"
              value={projectTitle}
              onChange={(event) => onProjectTitleChange(event.target.value)}
              placeholder={t("research.projectNamePlaceholder", { defaultValue: "Project name" })}
              autoFocus
              disabled={creatingProject}
              aria-describedby={!serviceReady ? "research-project-service-hint" : undefined}
              className="touch-target min-h-10 min-w-0 flex-1 rounded-input border border-border bg-surface px-3 py-2 text-ui text-text placeholder:text-muted focus:border-accent disabled:bg-surface-2 disabled:text-muted"
            />
            <button
              type="submit"
              disabled={!projectTitle.trim() || creatingProject || !serviceReady}
              className={cn(
                "touch-target flex min-h-10 shrink-0 items-center justify-center gap-1.5 rounded-input border px-4 py-2 text-ui font-medium",
                projectTitle.trim() && serviceReady && !creatingProject
                  ? "border-accent bg-accent text-accent-fg hover:opacity-90"
                  : "border-border bg-surface-2 text-muted",
              )}
            >
              {creatingProject && <Loader2 size={13} className="animate-spin" />}
              {t("research.createProject", { defaultValue: "Create project" })}
            </button>
          </div>
        </form>
        {!serviceReady && (
          <p id="research-project-service-hint" className="mt-2.5 text-xs text-muted">
            {t("research.empty.projectOffline", {
              defaultValue: "Science core must be available before a project can be created.",
            })}
          </p>
        )}
      </section>
    );
  }

  return (
    <section className="w-full max-w-3xl py-10 lg:py-12">
      <div className="max-w-2xl">
        <h2 className="text-xl font-semibold tracking-[-0.015em] text-text">
          {preparing
            ? t("research.empty.preparingTitle", { defaultValue: "Preparing your sources" })
            : failedCount > 0
              ? t("research.empty.failedTitle", { defaultValue: "Add a usable source" })
              : t("research.empty.sourcesTitle", { defaultValue: "Add the first source" })}
        </h2>
        <p className="mt-2 max-w-xl text-sm leading-6 text-muted">
          {preparing
            ? t("research.empty.preparingBody", {
                defaultValue:
                  "Spark is parsing and indexing the imported material. A research question can be planned as soon as one source is ready.",
              })
            : failedCount > 0
              ? t("research.sourceFailedBody", {
                  defaultValue:
                    "Spark could not prepare one or more files. The originals remain local; import another PDF or CSV to continue.",
                })
              : t("research.empty.sourcesBody", {
                  defaultValue:
                    "Import a PDF for literature synthesis or a CSV for deterministic analysis. Files remain inside this project workspace.",
                })}
        </p>
        <p className="mt-3 text-xs font-medium text-muted">
          {t("research.sourceLocalBoundary", {
            defaultValue: "Local files · evidence-indexed PDFs · deterministic CSV analysis",
          })}
        </p>
      </div>

      {sources.length > 0 && (
        <div
          role="status"
          aria-live="polite"
          className="mt-6 max-w-2xl border-y border-border"
        >
          <div className="flex items-center justify-between border-b border-border-faint py-2 text-caption text-muted">
            <span className="font-medium">
              {t("research.sourceImportStatus", { defaultValue: "Import status" })}
            </span>
            <span className="tabular-nums">
              {t("research.sourcesHeading", {
                defaultValue: "Sources ({{count}})",
                count: sources.length,
              })}
            </span>
          </div>
          {sources.slice(0, 4).map((source) => (
            <div
              key={source.id}
              className="flex min-h-11 items-center gap-2.5 border-b border-border-faint py-2 text-xs last:border-b-0"
            >
              <ResearchObjectIcon
                kind={source.sourceKind === "dataset" ? RESEARCH_OBJECT_KIND.dataset : RESEARCH_OBJECT_KIND.pdf}
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium text-text">{source.title}</span>
                <span className="mt-0.5 block text-caption text-muted">
                  {t(`research.sourceKind.${source.sourceKind}`, {
                    defaultValue: source.sourceKind,
                  })}
                </span>
              </span>
              <span
                className={cn(
                  "flex shrink-0 items-center gap-1.5 text-muted",
                  source.ingestionStatus === "failed" && "text-error",
                )}
              >
                {source.ingestionStatus === "processing" || source.ingestionStatus === "pending" ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <span
                    className={cn(
                      "h-1.5 w-1.5 rounded-full",
                      source.ingestionStatus === "ready" ? "bg-ok" : "bg-error",
                    )}
                  />
                )}
                {t(`research.sourceStatus.${source.ingestionStatus}`, {
                  defaultValue: source.ingestionStatus,
                })}
              </span>
            </div>
          ))}
        </div>
      )}

      <input
        ref={datasetInput}
        type="file"
        accept=".csv,text/csv"
        onChange={onImportDataset}
        className="hidden"
      />
      <div className="mt-6 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onImportPdfRequest}
          disabled={!serviceReady || importingSource}
          className={cn(
            "touch-target flex min-h-10 items-center gap-2 rounded-input border px-4 text-ui font-medium",
            serviceReady && !importingSource
              ? "border-accent bg-accent text-accent-fg hover:opacity-90"
              : "border-border bg-surface-2 text-muted",
          )}
        >
          {importingSource ? <Loader2 size={15} className="animate-spin" /> : <FileUp size={15} />}
          {importingSource
            ? t("research.importing", { defaultValue: "Indexing PDF…" })
            : t("research.importPdf", { defaultValue: "Import PDF" })}
        </button>
        <button
          type="button"
          onClick={() => datasetInput.current?.click()}
          disabled={!serviceReady || importingDataset}
          className="touch-target flex min-h-10 items-center gap-2 rounded-input border border-border bg-surface px-4 text-ui font-medium text-text hover:bg-surface-2 disabled:bg-surface-2 disabled:text-muted"
        >
          {importingDataset ? <Loader2 size={15} className="animate-spin" /> : <Table2 size={15} />}
          {importingDataset
            ? t("research.workflow.importingDataset", { defaultValue: "Importing…" })
            : t("research.workflow.importDataset", { defaultValue: "Import CSV" })}
        </button>
      </div>
    </section>
  );
}
