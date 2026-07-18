import type { ChangeEvent, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle } from "lucide-react";
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
import { DatasetWorkflowDetails } from "./DatasetWorkflowDetails";
import { ClarificationCard } from "./ClarificationCard";
import {
  WorkflowNeedsAttention,
  WorkflowProgress,
} from "./WorkflowExecution";
import { WorkflowGoalComposer } from "./WorkflowGoalComposer";
import { WorkflowPlanCard } from "./WorkflowPlanCard";
import { WorkflowResultView } from "./WorkflowResultView";
import {
  WorkflowError,
  WorkflowHeader,
  WorkflowWaiting,
} from "./WorkflowWorkspaceChrome";
import { workflowNeedsAttention } from "./workflowModel";

export { WorkflowReviewSummary } from "./WorkflowReviewSummary";

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
  importingDataset: boolean;
  remoteDestination: ScienceCoreModelDestination | null;
  legacyContent?: ReactNode;
  onCreate: (
    goal: string,
    options: ResearchWorkflowCreateOptions,
  ) => Promise<void>;
  onRespondToInteraction?: (
    interactionId: string,
    response: InteractionResponseValue,
  ) => Promise<void>;
  onApprovePlan: () => Promise<void>;
  onDecideAnalysis: (decision: "approved" | "rejected") => Promise<void>;
  onAcceptReviewWarnings: () => Promise<void>;
  onImportDataset: (event: ChangeEvent<HTMLInputElement>) => void;
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
  importingDataset,
  remoteDestination,
  legacyContent,
  onCreate,
  onRespondToInteraction = async () => {},
  onApprovePlan,
  onDecideAnalysis,
  onAcceptReviewWarnings,
  onImportDataset,
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
    return (
      <div className="space-y-4">
        {error && <WorkflowError message={error} onRefresh={onRefresh} />}
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
      </div>
    );
  }

  const { workflow, plan } = snapshot;
  const showAttention = workflowNeedsAttention(workflow.status);
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

      {workflow.status === "waiting-clarification" &&
        interactions
          .filter((interaction) => interaction.status === "pending")
          .map((interaction) => (
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

      {workflow.status === "planning" && !plan && (
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
        />
      )}

      {isDatasetDetailsSnapshot(snapshot) && (
        <DatasetWorkflowDetails
          snapshot={snapshot}
          mutating={mutating}
          onDecision={onDecideAnalysis}
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
