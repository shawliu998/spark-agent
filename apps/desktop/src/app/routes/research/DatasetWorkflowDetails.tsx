import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  Loader2,
  Table2,
  XCircle,
} from "lucide-react";
import type {
  AgentDecisionOut,
  AgentDecisionSummary,
  AgentLoopLimitState,
  AgentResearchWorkflowSnapshot,
  DatasetAnalysisWorkflowSnapshot,
  DatasetProfile,
  ResearchWorkflowAllowedAction,
  StepObservationOut,
  WorkflowAnalysisArtifact,
  WorkflowAnalysisExecutionPendingApproval,
  WorkflowAnalysisIntent,
  WorkflowAnalysisRun,
  WorkflowAnalysisSpec,
  WorkflowStructuredAnalysisResult,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";
import { parseTableFile, type ParsedTable } from "@/lib/csv";
import { parseIpynb } from "@/lib/notebook-file";
import { scienceCore } from "@/lib/scienceCore";
import { toast } from "@/lib/toast";
import { TableChart } from "@/components/inspector/TableChart";
import { WorkflowReviewSummary } from "./WorkflowReviewSummary";
import {
  RESEARCH_OBJECT_KIND,
  ResearchObjectIcon,
  type ResearchObjectKind,
} from "./ResearchObjectIcon";
import { statusLabel } from "./workflowModel";
import { AgentDecisionCard } from "./agent-loop/AgentDecisionCard";
import { AgentLoopLimitsCard } from "./agent-loop/AgentLoopLimitsCard";
import { AgentProgressSummary } from "./agent-loop/AgentProgressSummary";
import { DecisionHistory } from "./agent-loop/DecisionHistory";
import { ObservationCard } from "./agent-loop/ObservationCard";
import {
  type ArtifactPresentation,
  displayValue,
  presentArtifact,
  presentDatasetProfile,
  presentTable,
} from "./researchPresentation";

type DatasetDetailsSnapshot =
  | DatasetAnalysisWorkflowSnapshot
  | AgentResearchWorkflowSnapshot;

const RESULTS_CONTENT = "results" as const;

type AgentLoopDetailsSnapshot = DatasetDetailsSnapshot & {
  latestObservation: StepObservationOut | null;
  pendingDecision: AgentDecisionOut | null;
  decisionHistory: AgentDecisionSummary[];
  agentLoopLimits: AgentLoopLimitState;
};

function compactIdentifier(value: string): string {
  return value.length <= 20 ? value : `${value.slice(0, 10)}…${value.slice(-6)}`;
}

interface DatasetWorkflowDetailsProps {
  snapshot: DatasetDetailsSnapshot;
  view?: "all" | "dataset" | "analysis" | "results" | "notebook" | "artifacts";
  mutating: boolean;
  onDecision: (decision: "approved" | "rejected") => Promise<void>;
  onResolveAgentDecision?: (decision: "approved" | "rejected") => Promise<void>;
  onCancel: () => Promise<void>;
  onAcceptReviewWarnings: () => Promise<void>;
}

export function DatasetWorkflowDetails({
  snapshot,
  view = "all",
  mutating,
  onDecision,
  onResolveAgentDecision,
  onCancel,
  onAcceptReviewWarnings,
}: DatasetWorkflowDetailsProps) {
  const { t } = useTranslation("pages");
  const agentLoop = hasAgentLoopState(snapshot) ? snapshot : null;
  const executionApprovalPending =
    snapshot.analysisIntent != null &&
    snapshot.analysisIntent.status === "waiting-approval" &&
    snapshot.analysisIntent.decision === null &&
    snapshot.pendingApprovals.some(
      (approval) =>
        approval.kind === "analysis-execution" &&
        approval.analysisIntentId === snapshot.analysisIntent?.id,
    );
  const completedPresentation =
    (view === "all" || view === "results") &&
    snapshot.workflow.status === "completed";
  if (completedPresentation) {
    const hasMethodDetails =
      snapshot.datasetProfile != null ||
      snapshot.analysisSpec != null ||
      snapshot.analysisIntent != null;
    return (
      <div className="space-y-4">
        {(snapshot.structuredResult || snapshot.analysisRun) && (
          <section className="border-b border-border pb-4">
            <div className="flex items-center gap-2">
              <CheckCircle2 size={16} className="text-ok" />
              <h2 className="text-lg font-semibold tracking-tight text-text">
                {t("research.workflow.completedResultsHeading", {
                  defaultValue: "Analysis results",
                })}
              </h2>
              <span className="state-badge state-badge-ok ml-auto">
                <span aria-hidden="true" />
                {t("research.workflowStatus.completed", {
                  defaultValue: "Completed",
                })}
              </span>
            </div>
            <p className="mt-1 max-w-[70ch] text-xs leading-relaxed text-muted">
              {t("research.workflow.completedResultsBody", {
                defaultValue:
                  "Start with the primary analysis outputs, key statistics, figures, and tables. Integrity review, method, and reproducibility records remain available below.",
              })}
            </p>
          </section>
        )}

        {snapshot.structuredResult && (
          <StructuredResultCard structuredResult={snapshot.structuredResult} />
        )}
        {snapshot.analysisRun && (
          <AnalysisRunCard
            run={snapshot.analysisRun}
            content={RESULTS_CONTENT}
            collapseTableCharts
          />
        )}
        {snapshot.latestReview && (
          <DatasetReviewGate
            snapshot={snapshot}
            mutating={mutating}
            onAccept={onAcceptReviewWarnings}
          />
        )}

        {view === "all" && hasMethodDetails && (
          <details className="border-y border-border bg-surface">
            <summary className="flex min-h-12 cursor-pointer items-center gap-2 px-4 text-sm font-medium text-text hover:bg-surface-2">
              <Table2 size={15} className="text-accent" />
              {t("research.workflow.methodAndReproducibility", {
                defaultValue: "Method and reproducibility",
              })}
              <span className="ml-auto text-caption font-normal text-muted">
                {t("research.workflow.progressiveDetails", {
                  defaultValue: "Dataset, method, code, and hashes",
                })}
              </span>
            </summary>
            <div className="space-y-4 border-t border-border-faint py-4">
              {snapshot.datasetProfile && (
                <DatasetProfileCard profile={snapshot.datasetProfile} />
              )}
              {snapshot.analysisSpec && (
                <AnalysisMethodCard analysisSpec={snapshot.analysisSpec} />
              )}
              {snapshot.analysisIntent && (
                <AnalysisExecutionApprovalCard
                  snapshot={snapshot}
                  intent={snapshot.analysisIntent}
                  mutating={mutating}
                  onDecision={onDecision}
                  onCancel={onCancel}
                />
              )}
            </div>
          </details>
        )}

        {view === "all" && agentLoop && (
          <details className="border-y border-border bg-surface">
            <summary className="flex min-h-12 cursor-pointer items-center px-4 text-sm font-medium text-text hover:bg-surface-2">
              {t("research.workflow.agentActivityDetails", {
                defaultValue: "Agent activity and decisions",
              })}
              <span className="ml-auto text-caption font-normal text-muted">
                {agentLoop.decisionHistory.length}
              </span>
            </summary>
            <div className="space-y-4 border-t border-border-faint py-4">
              <AgentLoopPanelStack
                agentLoop={agentLoop}
                mutating={mutating}
                onResolveAgentDecision={onResolveAgentDecision}
              />
            </div>
          </details>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {agentLoop && (view === "all" || view === "analysis") && (
        <AgentLoopPanelStack
          agentLoop={agentLoop}
          mutating={mutating}
          onResolveAgentDecision={onResolveAgentDecision}
        />
      )}
      {snapshot.analysisIntent && executionApprovalPending && (view === "all" || view === "analysis") && (
        <AnalysisExecutionApprovalCard
          snapshot={snapshot}
          intent={snapshot.analysisIntent}
          mutating={mutating}
          onDecision={onDecision}
          onCancel={onCancel}
        />
      )}
      {snapshot.structuredResult && (view === "all" || view === "results") && (
        <StructuredResultCard structuredResult={snapshot.structuredResult} />
      )}
      {snapshot.latestReview && (view === "all" || view === "results") && (
        <DatasetReviewGate
          snapshot={snapshot}
          mutating={mutating}
          onAccept={onAcceptReviewWarnings}
        />
      )}
      {snapshot.datasetProfile && (view === "all" || view === "dataset") && (
        <DatasetProfileCard profile={snapshot.datasetProfile} />
      )}
      {snapshot.analysisSpec && (view === "all" || view === "analysis") && (
        <AnalysisMethodCard analysisSpec={snapshot.analysisSpec} />
      )}
      {snapshot.analysisIntent && !executionApprovalPending && (view === "all" || view === "analysis") && (
        <AnalysisExecutionApprovalCard
          snapshot={snapshot}
          intent={snapshot.analysisIntent}
          mutating={mutating}
          onDecision={onDecision}
          onCancel={onCancel}
        />
      )}
      {snapshot.analysisRun &&
        (view === "all" || view === "results" || view === "notebook" || view === "artifacts") && (
          <AnalysisRunCard run={snapshot.analysisRun} content={view} />
        )}
      {view === "notebook" && !snapshot.analysisRun && (
        <DatasetSurfaceEmpty
          title={t("research.workflow.noExecutedNotebookYet")}
          body={t("research.workflow.noExecutedNotebookYetBody")}
        />
      )}
      {view === "results" && !snapshot.structuredResult && !snapshot.analysisRun && (
        <DatasetSurfaceEmpty
          title={t("research.workflow.noRecordedResultsYet")}
          body={t("research.workflow.noRecordedResultsYetBody")}
        />
      )}
      {view === "artifacts" && !snapshot.analysisRun && (
        <DatasetSurfaceEmpty
          title={t("research.workflow.noRecordedArtifactsYet")}
          body={t("research.workflow.noRecordedArtifactsYetBody")}
        />
      )}
    </div>
  );
}

function DatasetSurfaceEmpty({ title, body }: { title: string; body: string }) {
  return (
    <section className="border-y border-border bg-surface px-4 py-8">
      <h3 className="text-sm font-semibold text-text">{title}</h3>
      <p className="mt-1 max-w-[68ch] text-xs leading-relaxed text-muted">{body}</p>
    </section>
  );
}

function hasAgentLoopState(
  snapshot: DatasetDetailsSnapshot,
): snapshot is AgentLoopDetailsSnapshot {
  return (
    "agentLoopLimits" in snapshot &&
    snapshot.agentLoopLimits !== undefined &&
    "decisionHistory" in snapshot &&
    Array.isArray(snapshot.decisionHistory) &&
    "latestObservation" in snapshot &&
    "pendingDecision" in snapshot
  );
}

function AgentLoopPanelStack({
  agentLoop,
  mutating,
  onResolveAgentDecision,
}: {
  agentLoop: AgentLoopDetailsSnapshot;
  mutating: boolean;
  onResolveAgentDecision?: (decision: "approved" | "rejected") => Promise<void>;
}) {
  return (
    <>
      <AgentProgressSummary
        latestObservation={agentLoop.latestObservation}
        pendingDecision={agentLoop.pendingDecision}
        decisionHistory={agentLoop.decisionHistory}
      />
      {agentLoop.latestObservation && (
        <ObservationCard observation={agentLoop.latestObservation} />
      )}
      {agentLoop.pendingDecision && (
        <AgentDecisionCard
          decision={agentLoop.pendingDecision}
          mutating={mutating}
          onResolve={onResolveAgentDecision}
        />
      )}
      <AgentLoopLimitsCard limits={agentLoop.agentLoopLimits} />
      <DecisionHistory decisions={agentLoop.decisionHistory} />
    </>
  );
}

function AnalysisMethodCard({
  analysisSpec,
}: {
  analysisSpec: WorkflowAnalysisSpec;
}) {
  const { t } = useTranslation("pages");
  const { spec } = analysisSpec;
  const operation = spec.operation;
  const method =
    operation.type === "descriptive" ? "descriptive" : operation.method;
  const methodTitle =
    analysisSpec.status === "pending-approval"
      ? t("research.workflow.proposedAnalysisMethod", {
          defaultValue: "Proposed analysis method",
        })
      : analysisSpec.status === "approved"
        ? t("research.workflow.approvedAnalysisMethod", {
            defaultValue: "Approved analysis method",
          })
        : analysisSpec.status === "superseded"
          ? t("research.workflow.supersededAnalysisMethod", {
              defaultValue: "Superseded analysis method",
            })
          : t("research.workflow.rejectedAnalysisMethod", {
              defaultValue: "Rejected analysis method",
            });

  return (
    <section className="border-y border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border-faint px-4 py-3">
        <ResearchObjectIcon
          kind={RESEARCH_OBJECT_KIND.method}
          size={15}
          className="text-accent"
        />
        <h3 className="text-sm font-medium text-text">
          {methodTitle}
        </h3>
        <span className="ml-auto inline-flex items-center gap-1.5 text-caption text-muted">
          <span
            aria-hidden="true"
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              analysisSpec.status === "approved"
                ? "bg-ok"
                : analysisSpec.status === "rejected"
                  ? "bg-error"
                  : analysisSpec.status === "pending-approval"
                    ? "bg-warn"
                    : "bg-muted",
            )}
          />
          {t(`research.workflow.analysisSpecStatus.${analysisSpec.status}`)}
        </span>
        <span className="font-mono text-caption text-muted">
          {method}
        </span>
      </div>
      <div className="space-y-3 p-4">
        <p className="text-xs font-medium text-text">{spec.objective}</p>
        <dl className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
          <MetadataCell
            label={t("research.workflow.analysisOperation", {
              defaultValue: "Operation",
            })}
            value={operation.type}
          />
          <MetadataCell
            label={t("research.workflow.analysisMethodLabel", {
              defaultValue: "Requested method",
            })}
            value={method}
          />
          <MetadataCell
            label={t("research.workflow.confidenceLevel", {
              defaultValue: "Confidence level",
            })}
            value={formatPercent(spec.confidenceLevel)}
          />
          <MetadataCell
            label={t("research.workflow.missingValuePolicy", {
              defaultValue: "Missing-value policy",
            })}
            value={spec.missingValuePolicy}
          />
          {operation.type === "descriptive" && (
            <>
              <MetadataCell
                label={t("research.workflow.analysisColumns", {
                  defaultValue: "Columns",
                })}
                value={operation.columns.join(", ")}
                wide
              />
              <MetadataCell
                label={t("research.workflow.descriptiveStatistics", {
                  defaultValue: "Statistics",
                })}
                value={operation.statistics.join(", ")}
                wide
              />
            </>
          )}
          {operation.type === "two-group-comparison" && (
            <>
              <MetadataCell
                label={t("research.workflow.outcomeColumn", {
                  defaultValue: "Outcome column",
                })}
                value={operation.outcomeColumn}
              />
              <MetadataCell
                label={t("research.workflow.groupColumn", {
                  defaultValue: "Group column",
                })}
                value={operation.groupColumn}
              />
              <MetadataCell
                label={t("research.workflow.comparedGroups", {
                  defaultValue: "Compared groups",
                })}
                value={operation.groups.join(" ↔ ")}
              />
              <MetadataCell
                label={t("research.workflow.effectSize", {
                  defaultValue: "Effect size",
                })}
                value={operation.effectSize}
              />
            </>
          )}
          {operation.type === "correlation" && (
            <>
              <MetadataCell
                label={t("research.workflow.xColumn", {
                  defaultValue: "X column",
                })}
                value={operation.xColumn}
              />
              <MetadataCell
                label={t("research.workflow.yColumn", {
                  defaultValue: "Y column",
                })}
                value={operation.yColumn}
              />
            </>
          )}
        </dl>

        <div className="border-y border-border-faint py-3 text-xs text-muted">
          <p>
            {t("research.workflow.methodSelectedBy", {
              defaultValue: "Selected by {{selector}} · revision {{revision}}",
              selector: analysisSpec.selectorKind,
              revision: analysisSpec.revision,
            })}
          </p>
          {analysisSpec.selectorReason && (
            <p className="mt-1 leading-relaxed text-text">
              {analysisSpec.selectorReason}
            </p>
          )}
        </div>

        {(spec.assumptions.length > 0 || spec.limitations.length > 0) && (
          <div className="grid gap-3 lg:grid-cols-2">
            <MethodNotes
              label={t("research.workflow.methodAssumptions", {
                defaultValue: "Assumptions",
              })}
              items={spec.assumptions}
            />
            <MethodNotes
              label={t("research.workflow.methodLimitations", {
                defaultValue: "Limitations",
              })}
              items={spec.limitations}
              warning
            />
          </div>
        )}

        <dl className="grid gap-2 text-xs sm:grid-cols-2">
          <MetadataCell
            label={t("research.workflow.analysisSpecHash", {
              defaultValue: "AnalysisSpec SHA-256",
            })}
            value={analysisSpec.specSha256}
            mono
          />
          <MetadataCell
            label={t("research.workflow.datasetProfileHash", {
              defaultValue: "Dataset profile SHA-256",
            })}
            value={analysisSpec.datasetProfileSha256}
            mono
          />
        </dl>
      </div>
    </section>
  );
}

function MethodNotes({
  label,
  items,
  warning = false,
}: {
  label: string;
  items: string[];
  warning?: boolean;
}) {
  if (items.length === 0) return null;
  return (
    <div
      className={cn(
        "border-y px-1 py-3",
        warning
          ? "border-warn/25"
          : "border-border-faint",
      )}
    >
      <p className="text-xs font-medium text-muted">
        {label}
      </p>
      <ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-relaxed text-muted">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function DatasetProfileCard({ profile }: { profile: DatasetProfile }) {
  const { t } = useTranslation("pages");
  const presented = useMemo(() => presentDatasetProfile(profile), [profile]);
  return (
    <section className="border-y border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border-faint px-4 py-3">
        <Table2 size={15} className="text-accent" />
        <h3 className="text-sm font-medium text-text">
          {t("research.workflow.datasetProfile", {
            defaultValue: "Dataset profile",
          })}
        </h3>
        <span className="ml-auto text-caption text-muted">
          {t("research.workflow.profileShape", {
            defaultValue: "{{rows}} rows · {{columns}} columns",
            rows: displayValue(presented.rowCount),
            columns: displayValue(presented.columnCount),
          })}
        </span>
      </div>
      <div className="space-y-3 p-4">
        {presented.issues.length > 0 && (
          <div
            role="status"
            className="flex items-start gap-2 rounded-input border border-warn/25 bg-warn/5 px-3 py-2.5 text-xs text-muted"
          >
            <AlertTriangle size={13} className="mt-0.5 shrink-0 text-warn" />
            <div className="min-w-0">
              <p className="font-medium text-text">
                {t("research.workflow.profileCompatibilityTitle", {
                  defaultValue: "Some profile fields use a different schema",
                })}
              </p>
              <p className="mt-0.5 leading-relaxed">
                {t("research.workflow.profileCompatibilityBody", {
                  defaultValue:
                    "Unavailable values are shown as —. The original profile and provenance remain unchanged.",
                })}
              </p>
              <p className="mt-1 font-mono text-caption">
                {presented.issues.join(" · ")}
              </p>
            </div>
          </div>
        )}
        <dl className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
          <MetadataCell
            label={t("research.workflow.profileFile", {
              defaultValue: "File",
            })}
            value={presented.filename}
          />
          <MetadataCell
            label={t("research.workflow.profileSize", {
              defaultValue: "Size",
            })}
            value={formatBytes(presented.fileSizeBytes)}
          />
          <MetadataCell
            label={t("research.workflow.profileEncoding", {
              defaultValue: "Encoding",
            })}
            value={presented.encoding}
            mono
          />
          <MetadataCell
            label={t("research.workflow.profileDelimiter", {
              defaultValue: "Delimiter",
            })}
            value={presented.delimiter ? JSON.stringify(presented.delimiter) : null}
            mono
          />
          <MetadataCell
            label={t("research.workflow.datasetSourceId", {
              defaultValue: "Dataset source ID",
            })}
            value={presented.datasetSourceId}
            mono
            wide
          />
          <MetadataCell
            label={t("research.workflow.datasetHash", {
              defaultValue: "Dataset SHA-256",
            })}
            value={presented.contentHash}
            mono
            wide
          />
        </dl>

        <div className="overflow-x-auto rounded-input border border-border">
          <table className="min-w-full text-left text-xs">
            <thead className="bg-surface-2 text-caption font-medium text-muted">
              <tr>
                <th className="px-3 py-2">
                  {t("research.workflow.profileColumn", {
                    defaultValue: "Column",
                  })}
                </th>
                <th className="px-3 py-2">
                  {t("research.workflow.profileType", {
                    defaultValue: "Type",
                  })}
                </th>
                <th className="px-3 py-2">
                  {t("research.workflow.profileMissing", {
                    defaultValue: "Missing",
                  })}
                </th>
                <th className="px-3 py-2">
                  {t("research.workflow.profileUnique", {
                    defaultValue: "Unique",
                  })}
                </th>
                <th className="px-3 py-2">
                  {t("research.workflow.profileFlags", {
                    defaultValue: "Flags",
                  })}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-faint bg-bg text-text">
              {presented.columns.map((column) => {
                const flags = [
                  column.potentialDate
                    ? t("research.workflow.profileFlagDate", {
                        defaultValue: "date",
                      })
                    : null,
                  column.potentialId
                    ? t("research.workflow.profileFlagIdentifier", {
                        defaultValue: "identifier",
                      })
                    : null,
                  column.mixedType
                    ? t("research.workflow.profileFlagMixed", {
                        defaultValue: "mixed type",
                      })
                    : null,
                ].filter((value) => value !== null);
                return (
                  <tr key={column.key}>
                    <td className="px-3 py-2 font-medium">{column.name}</td>
                    <td className="px-3 py-2 font-mono text-muted">
                      {column.inferredType}
                    </td>
                    <td className="px-3 py-2">{displayValue(column.missingCount)}</td>
                    <td className="px-3 py-2">{displayValue(column.uniqueCount)}</td>
                    <td className="px-3 py-2 text-muted">
                      {flags.join(" · ") || "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <p className="text-caption leading-relaxed text-muted">
          {t("research.workflow.profileSampling", {
            defaultValue:
              "Profiled {{profiled}} of {{read}} rows with {{method}} (limit {{limit}}, seed {{seed}}).",
            profiled: displayValue(presented.sampling.rowsProfiled),
            read: displayValue(presented.sampling.rowsRead),
            method: displayValue(presented.sampling.method),
            limit: displayValue(presented.sampling.maxSampleRows),
            seed: displayValue(presented.sampling.seed),
          })}
        </p>

        {presented.warnings.length > 0 && (
          <ul className="space-y-1.5 rounded-input border border-warn/25 bg-warn/5 p-3 text-xs text-muted">
            {presented.warnings.map((warning) => (
              <li
                key={warning.key}
                className="flex items-start gap-2"
              >
                <AlertTriangle
                  size={13}
                  className="mt-0.5 shrink-0 text-warn"
                />
                <span>
                  <strong className="font-medium text-text">
                    {warning.code}
                  </strong>
                  {warning.columnName ? ` (${warning.columnName})` : ""}: {warning.message}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function AnalysisExecutionApprovalCard({
  snapshot,
  intent,
  mutating,
  onDecision,
  onCancel,
}: {
  snapshot: DatasetDetailsSnapshot;
  intent: WorkflowAnalysisIntent;
  mutating: boolean;
  onDecision: (decision: "approved" | "rejected") => Promise<void>;
  onCancel: () => Promise<void>;
}) {
  const { t } = useTranslation("pages");
  const pendingApproval = snapshot.pendingApprovals.find(
    (item) =>
      item.kind === "analysis-execution" &&
      item.analysisIntentId === intent.id,
  );
  const approval: WorkflowAnalysisExecutionPendingApproval | null =
    pendingApproval?.kind === "analysis-execution" ? pendingApproval : null;
  const allowedActions: readonly ResearchWorkflowAllowedAction[] =
    snapshot.allowedActions;
  const canApprove = allowedActions.includes("approve-analysis");
  const canReject = allowedActions.includes("reject-analysis");
  const canCancel = allowedActions.includes("cancel");
  const awaitingApproval = approval !== null &&
    intent.status === "waiting-approval" && intent.decision === null;
  // The mutable intent is an execution record after approval. Only a matching
  // pending envelope is authoritative for an approval decision.
  const code = awaitingApproval ? approval.code : intent.code;
  const codeDiff = awaitingApproval ? approval.codeDiff : intent.codeDiff;
  const envelope = awaitingApproval ? approval : intent;
  const approvedRunScope = awaitingApproval || intent.decision === "approved";
  const recordedOnly = !awaitingApproval && intent.decision !== "approved";
  const statusTitle = executionRecordStatus(intent.status, awaitingApproval);
  const localizedStatusTitle = t(`research.workflow.executionRecordStatus.${intent.status}`, {
    defaultValue: statusTitle,
  });
  const statusTone = executionRecordTone(intent.status, awaitingApproval);

  return (
    <section className="execution-approval-container border-y border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border-faint px-4 py-3">
        <h3 className="text-sm font-semibold text-text">
          {awaitingApproval
            ? t("research.workflow.executionApproval", {
                defaultValue: "High-risk execution approval",
              })
            : t("research.workflow.executionRecord", {
                defaultValue: "Immutable execution record — {{status}}",
                status: localizedStatusTitle,
              })}
        </h3>
        <span className={cn("ml-auto inline-flex items-center gap-1.5 text-caption font-medium", statusTone.text)}>
          <span aria-hidden="true" className={cn("h-1.5 w-1.5 rounded-full", statusTone.dot)} />
          {awaitingApproval
            ? t("research.workflow.exactPayloadApprovalRequired", {
                defaultValue: "Exact payload approval required",
              })
            : localizedStatusTitle}
        </span>
      </div>
      <div className="p-4 pb-0">
        <p className="max-w-[72ch] text-xs leading-relaxed text-muted">
          {awaitingApproval
            ? t("research.workflow.executionApprovalBoundary", {
                defaultValue:
                  "Review the exact immutable payload. Approving it queues execution automatically; there is no separate manual execute action, and any repair creates a new intent and approval.",
              })
            : recordedOnly
              ? t("research.workflow.recordedOutputBoundary", {
                  defaultValue: "This record does not authorize or prove execution.",
                })
              : t("research.workflow.executionRecordBoundary", {
                  defaultValue:
                    "This immutable record preserves the recorded payload and provenance. It is not an approval request.",
                })}
        </p>

        {intent.errorSummary && (
          <div className="mt-4 border-y border-error/20 bg-error/5 px-3 py-2.5 text-xs text-muted">
            <p className="font-medium text-error">
              {t("research.workflow.previousAttemptFailed", {
                defaultValue: "Previous attempt failed safely",
              })}
              {": "}{intent.errorSummary.userMessage}
            </p>
            <p className="mt-1 font-mono text-caption text-muted">
              {intent.errorSummary.code}
            </p>
            {intent.errorSummary.stderrExcerpt && (
              <pre
                tabIndex={0}
                aria-label={t("research.workflow.previousAttemptStderrAria", {
                  defaultValue: "Previous attempt error output",
                })}
                className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-input bg-surface p-2 font-mono text-caption text-text"
              >
                {intent.errorSummary.stderrExcerpt}
              </pre>
            )}
          </div>
        )}

        <dl className="mt-4 divide-y divide-border-faint border-y border-border-faint">
          <ExecutionApprovalRow
            label={t("research.workflow.approvalRisk", {
              defaultValue: "Risk",
            })}
            value={t("research.workflow.highRisk", { defaultValue: "High risk" })}
            detail={awaitingApproval
              ? t("research.workflow.highRiskDetail", {
                  defaultValue:
                    "Approval queues the exact displayed Python automatically; it does not approve future repairs.",
                })
              : t("research.workflow.recordedPayloadDetail", {
                  defaultValue: "This recorded payload is retained for provenance and cannot authorize execution.",
                })}
            warn
          />
          <ExecutionApprovalRow
            label={t("research.workflow.executionDataScope", {
              defaultValue: "Data scope",
            })}
              value={envelope.datasetSourceId}
            detail={
              <span>
                {t("research.workflow.readOnlyHashBoundDataset", {
                  defaultValue: "Read-only dataset · SHA-256",
                })}
                {": "}
                <code className="break-all font-mono text-caption text-text">
                  {envelope.datasetContentHash}
                </code>
              </span>
            }
            valueMono
          />
          <ExecutionApprovalRow
            label={t("research.workflow.executionPermissions", {
              defaultValue: "Permissions",
            })}
            value={t("research.workflow.isolatedPythonRuntime", {
              defaultValue: "Isolated Python runtime",
            })}
            detail={t("research.workflow.runtimeRestrictionIsolation", {
              defaultValue: "The runtime/container applies the recorded runtime policy and resource scope; recorded artifacts are hash-checked after execution.",
            })}
          />
          <ExecutionApprovalRow
            label={t("research.workflow.executionLimits", {
              defaultValue: "Limits",
            })}
            value={t("research.workflow.timeoutSeconds", {
              defaultValue: "{{count}} second timeout",
              count: envelope.timeoutSeconds,
            })}
            detail={t("research.workflow.verifiedArtifactsOnly", {
              defaultValue: "Recorded artifacts are hash-checked after the run.",
            })}
          />
          <ExecutionApprovalRow
            label={approvedRunScope
              ? t("research.workflow.executionOutputs", { defaultValue: "Creates" })
              : t("research.workflow.expectedExecutionOutputs", { defaultValue: "Expected outputs" })}
            value={envelope.expectedOutputs.join(" · ")}
            detail={approvedRunScope
              ? t("research.workflow.outputBoundary", {
                  defaultValue: "Outputs are written only to the approved workspace run directory.",
                })
              : t("research.workflow.recordedOutputBoundary", {
                  defaultValue: "This record does not authorize or prove execution.",
                })}
          />
          <ExecutionApprovalRow
            label={t("research.workflow.payloadChanges", {
              defaultValue: "Payload changes",
            })}
            value={
              codeDiff
                ? t("research.workflow.payloadHasDiff", {
                    defaultValue: "Diff present — review required",
                  })
                : t("research.workflow.payloadNoDiff", {
                    defaultValue: "No repair diff",
                  })
            }
            detail={
              codeDiff
                ? awaitingApproval
                  ? t("research.workflow.payloadDiffDetail", {
                      defaultValue: "The repair diff below is part of this new immutable approval.",
                    })
                  : t("research.workflow.recordedPayloadDiffDetail", {
                      defaultValue: "The recorded repair diff is retained with this execution record.",
                    })
                : awaitingApproval
                  ? t("research.workflow.payloadNoDiffDetail", {
                      defaultValue: "This is the first immutable payload for this step.",
                    })
                  : t("research.workflow.recordedPayloadNoDiffDetail", {
                      defaultValue: "No recorded repair diff is attached to this execution record.",
                    })
            }
            warn={Boolean(codeDiff)}
          />
        </dl>

        <div className={cn("mt-4 grid gap-4", codeDiff && "xl:grid-cols-2")}>
          <div className="min-w-0">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted">
              <span className="font-medium text-text">
                {awaitingApproval
                  ? t("research.workflow.readOnlyCode", {
                      defaultValue: "Read-only approved Python",
                    })
                  : t("research.workflow.recordedPythonPayload", {
                      defaultValue: "Recorded Python payload",
                    })}
              </span>
            </div>
            <pre
              tabIndex={0}
              aria-label={t("research.workflow.analysisCodeAria", {
                defaultValue: "Analysis code",
              })}
              className="max-h-80 overflow-auto whitespace-pre rounded-input border border-border-faint bg-surface-2 p-3 font-mono text-xs leading-5 text-text"
            >
              {code}
            </pre>
          </div>

          {codeDiff && (
            <div className="min-w-0">
              <p className="mb-2 text-xs font-medium text-warn">
                {awaitingApproval
                  ? t("research.workflow.repairDiff", {
                      defaultValue: "Repair diff — requires this new approval",
                    })
                  : t("research.workflow.recordedRepairDiff", {
                      defaultValue: "Recorded repair diff",
                    })}
              </p>
              <pre
                tabIndex={0}
                aria-label={t("research.workflow.repairDiffAria", {
                  defaultValue: "Recorded repair diff",
                })}
                className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-input border border-warn/25 bg-warn/5 p-3 font-mono text-xs leading-5 text-text"
              >
                {codeDiff}
              </pre>
            </div>
          )}
        </div>

        <details className="mt-5 border-t border-border-faint">
          <summary className="flex min-h-11 cursor-pointer items-center text-xs font-medium text-link hover:underline">
            {t("research.workflow.auditDetails", { defaultValue: "Audit details" })}
          </summary>
          <dl className="grid gap-x-5 gap-y-4 pb-4 text-xs sm:grid-cols-2 lg:grid-cols-4">
          {awaitingApproval && (
            <>
              <MetadataCell label={t("research.workflow.approvalReasonLabel")} value={approval.reason} />
              <MetadataCell
                label={t("research.workflow.approvalSchemaLabel")}
                value={approval.approvalSchemaVersion}
                mono
              />
              <MetadataCell
                label={t("research.workflow.workflowRevisionLabel")}
                value={String(approval.expectedWorkflowRevision)}
              />
              <div className="min-w-0 sm:col-span-2">
                <dt className="text-xs font-medium text-muted">{t("research.workflow.affectedResourcesLabel")}</dt>
                <dd className="mt-1 space-y-1">
                  {approval.affectedResources.map((resource) => (
                    <code key={resource} className="block break-all font-mono text-caption text-text">
                      {resource}
                    </code>
                  ))}
                </dd>
              </div>
            </>
          )}
          <MetadataCell
            label={t("research.workflow.repairAttempt", {
              defaultValue: "Repair attempt",
            })}
            value={`${intent.repairAttempt} of 2`}
          />
          <MetadataCell
            label={t("research.workflow.intentId", {
              defaultValue: "Intent ID",
            })}
            value={intent.id}
            mono
            wide
          />
          <MetadataCell
            label={awaitingApproval
              ? t("research.workflow.approvalPayloadHash", { defaultValue: "Payload SHA-256" })
              : t("research.workflow.recordedPayloadHash", { defaultValue: "Payload SHA-256" })}
            value={envelope.payloadSha256}
            mono
            wide
          />
          {envelope.analysisSpecId && (
            <MetadataCell
              label={t("research.workflow.analysisSpecId", {
                defaultValue: "AnalysisSpec ID",
              })}
              value={envelope.analysisSpecId}
              mono
              wide
            />
          )}
          {envelope.specSha256 && (
            <MetadataCell
              label={t("research.workflow.analysisSpecHash", {
                defaultValue: "AnalysisSpec SHA-256",
              })}
              value={envelope.specSha256}
              mono
              wide
            />
          )}
          {envelope.datasetProfileSha256 && (
            <MetadataCell
              label={t("research.workflow.datasetProfileHash", {
                defaultValue: "Dataset profile SHA-256",
              })}
              value={envelope.datasetProfileSha256}
              mono
              wide
            />
          )}
          {envelope.compilerVersion && (
            <MetadataCell
              label={t("research.workflow.compilerVersion", {
                defaultValue: "Compiler version",
              })}
              value={envelope.compilerVersion}
              mono
            />
          )}
          {envelope.codeSha256 && (
            <MetadataCell
              label={t("research.workflow.codeHash", {
                defaultValue: "Code SHA-256",
              })}
              value={envelope.codeSha256}
              mono
              wide
            />
          )}
          {envelope.runtimePolicyId && (
            <MetadataCell
              label={t("research.workflow.runtimePolicy", {
                defaultValue: "Runtime policy",
              })}
              value={envelope.runtimePolicyId}
              mono
            />
          )}
          <MetadataCell
            label={t("research.workflow.datasetSourceId", {
              defaultValue: "Dataset source ID",
            })}
            value={envelope.datasetSourceId}
            mono
            wide
          />
          <MetadataCell
            label={t("research.workflow.datasetHash", {
              defaultValue: "Dataset SHA-256",
            })}
            value={envelope.datasetContentHash}
            mono
            wide
          />
          </dl>
        </details>

        {awaitingApproval && (canApprove || canReject || canCancel) && (
          <div className="-mx-4 mt-5 flex min-h-[68px] flex-wrap items-center gap-3 border-t border-border bg-surface px-4 py-3">
            <p
              id="exact-payload-approval-consequence"
              className="mr-auto max-w-xl text-xs leading-relaxed text-muted"
            >
              {t("research.workflow.approveExactPayloadBoundary", {
                defaultValue:
                  "Approval queues only this immutable payload. Any repair requires a new approval.",
              })}
            </p>
            {canCancel && (
              <button
                type="button"
                onClick={() => void onCancel()}
                disabled={mutating}
                aria-describedby="exact-payload-approval-consequence"
                className="flex min-h-11 items-center gap-1.5 rounded-input border border-border px-4 py-2 text-xs text-text hover:bg-surface-2 disabled:opacity-40"
              >
                {mutating && <Loader2 size={13} className="animate-spin" />}
                {t("research.workflow.cancelTask", { defaultValue: "Cancel task" })}
              </button>
            )}
            {canReject && (
              <button
                type="button"
                onClick={() => void onDecision("rejected")}
                disabled={mutating}
                aria-describedby="exact-payload-approval-consequence"
                className="flex min-h-11 items-center gap-1.5 rounded-input border border-error/30 px-4 py-2 text-xs text-error hover:bg-error/5 disabled:opacity-40"
              >
                {mutating && <Loader2 size={13} className="animate-spin" />}
                {t("research.workflow.rejectExecution", {
                  defaultValue: "Reject payload",
                })}
              </button>
            )}
            {canApprove && (
              <button
                type="button"
                onClick={() => void onDecision("approved")}
                disabled={mutating}
                aria-describedby="exact-payload-approval-consequence"
                className="flex min-h-11 items-center gap-1.5 rounded-input bg-accent px-4 py-2 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-40"
              >
                {mutating && <Loader2 size={13} className="animate-spin" />}
                {t("research.workflow.approveExactPayload", {
                  defaultValue: "Approve exact payload",
                })}
              </button>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function executionRecordStatus(
  status: WorkflowAnalysisIntent["status"],
  awaitingApproval: boolean,
): string {
  if (awaitingApproval) return "Awaiting approval";
  if (status === "waiting-approval") return "Execution envelope unavailable";
  if (status === "approved") return "Approved";
  if (status === "executing") return "Executing";
  if (status === "completed") return "Completed";
  if (status === "failed") return "Failed";
  if (status === "rejected") return "Rejected";
  return statusLabel(status);
}

function executionRecordTone(
  status: WorkflowAnalysisIntent["status"],
  awaitingApproval: boolean,
): { dot: string; text: string } {
  if (awaitingApproval) return { dot: "bg-warn", text: "text-warn" };
  if (status === "failed" || status === "rejected") {
    return { dot: "bg-error", text: "text-error" };
  }
  if (status === "completed") return { dot: "bg-ok", text: "text-ok" };
  return { dot: "bg-muted", text: "text-muted" };
}

function ExecutionApprovalRow({
  label,
  value,
  detail,
  valueMono = false,
  warn = false,
}: {
  label: string;
  value: ReactNode;
  detail: ReactNode;
  valueMono?: boolean;
  warn?: boolean;
}) {
  return (
    <div className="execution-approval-row grid min-w-0 gap-x-4 gap-y-1 py-3">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd
        className={cn(
          "min-w-0 break-words text-xs font-medium text-text",
          valueMono && "break-all font-mono text-caption",
          warn && "text-warn",
        )}
      >
        {value}
      </dd>
      <dd className="min-w-0 break-words text-xs leading-relaxed text-muted">
        {detail}
      </dd>
    </div>
  );
}

type RenderableArtifact = ArtifactPresentation & {
  original: WorkflowAnalysisArtifact;
};

function AnalysisRunCard({
  run,
  content = "all",
  collapseTableCharts = false,
}: {
  run: WorkflowAnalysisRun;
  content?: "all" | "results" | "notebook" | "artifacts";
  collapseTableCharts?: boolean;
}) {
  const { t } = useTranslation("pages");
  const artifacts = useMemo(
    () =>
      (Array.isArray(run.artifacts) ? run.artifacts : []).map((artifact, index) =>
        presentArtifact(artifact, index),
      ),
    [run.artifacts],
  );
  const figures = artifacts.filter(
    (artifact): artifact is RenderableArtifact =>
      artifact.previewMode === "image" && artifact.original !== null,
  );
  const tables = artifacts.filter(
    (artifact): artifact is RenderableArtifact =>
      artifact.previewMode === "table" && artifact.original !== null,
  );
  const environments = artifacts.filter(
    (artifact): artifact is RenderableArtifact =>
      artifact.kind === "environment" && artifact.original !== null,
  );
  const logs = artifacts.filter(
    (artifact): artifact is RenderableArtifact =>
      artifact.kind === "log" && artifact.original !== null,
  );
  const notebooks = artifacts.filter(
    (artifact): artifact is RenderableArtifact =>
      artifact.kind === "notebook" &&
      artifact.artifactType === "notebook-executed" &&
      artifact.original !== null,
  );
  const showResults = content === "all" || content === "results";
  const showNotebook = content === "all" || content === "notebook";
  const showArtifacts = content === "all" || content === "artifacts";
  return (
    <section className="border-y border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border-faint px-4 py-3">
        <ResearchObjectIcon kind={RESEARCH_OBJECT_KIND.run} size={15} className="text-accent" />
        <h3 className="text-sm font-medium text-text">
          {content === "results"
            ? t("research.workflow.figuresAndTables", {
                defaultValue: "Figures and tables",
              })
            : t("research.workflow.analysisRun", {
                defaultValue: "Analysis run",
              })}
        </h3>
        {content !== "results" && (
          <>
            <RunStatus status={run.status} />
            <code className="ml-auto text-caption text-muted" title={run.id}>
              {compactIdentifier(run.id)}
            </code>
          </>
        )}
      </div>
      <div className="space-y-4 p-4">
        {run.error && (
          <div className="flex items-start gap-2 rounded-input border border-error/25 bg-error/5 px-3 py-2.5 text-xs text-error">
            <XCircle size={13} className="mt-0.5 shrink-0" />
            <span className="break-words">{run.error}</span>
          </div>
        )}

        {content !== "results" && (
          <dl className="grid gap-2 text-xs sm:grid-cols-2">
            <MetadataCell
              label={t("research.workflow.environmentHash", {
                defaultValue: "Environment SHA-256",
              })}
              value={
                run.environmentHash ??
                t("research.workflow.environmentPending", {
                  defaultValue: "Not available before completion",
                })
              }
              mono
            />
            <MetadataCell
              label={t("research.workflow.approvalPayloadHash", {
                defaultValue: "Payload SHA-256",
              })}
              value={run.payloadSha256}
              mono
            />
          </dl>
        )}

        {showArtifacts && <ArtifactDirectory artifacts={artifacts} />}

        {showArtifacts && environments.map((artifact) => (
          <TextArtifactPreview
            key={artifact.key}
            artifact={artifact}
            heading={t("research.workflow.environmentManifest", {
              defaultValue: "Environment manifest",
            })}
          />
        ))}

        {showArtifacts && logs.map((artifact) => (
          <TextArtifactPreview
            key={artifact.key}
            artifact={artifact}
            heading={t("research.workflow.analysisLogArtifact", {
              defaultValue: "Analysis log artifact",
            })}
          />
        ))}

        {showResults && figures.length > 0 && (
          <div className="space-y-3">
            <h4 className="flex items-center gap-2 text-xs font-medium text-muted">
              <ResearchObjectIcon kind={RESEARCH_OBJECT_KIND.figure} />
              {t("research.workflow.figureOutputs", {
                defaultValue: "Figure outputs",
              })}
            </h4>
            <div className="grid gap-3 xl:grid-cols-2">
              {figures.map((artifact) => (
                <FigureArtifactPreview key={artifact.key} artifact={artifact} />
              ))}
            </div>
          </div>
        )}

        {showResults && tables.length > 0 && (
          <div className="space-y-3">
            <h4 className="flex items-center gap-2 text-xs font-medium text-muted">
              <ResearchObjectIcon kind={RESEARCH_OBJECT_KIND.table} />
              {t("research.workflow.tableOutputs", {
                defaultValue: "Table outputs",
              })}
            </h4>
            {tables.map((artifact) => (
              <TableArtifactPreview
                key={artifact.key}
                artifact={artifact}
                collapseChart={collapseTableCharts}
              />
            ))}
          </div>
        )}

        {content === "results" && (
          <details className="border-t border-border-faint pt-1">
            <summary className="flex min-h-10 cursor-pointer items-center text-xs font-medium text-link hover:underline">
              {t("research.workflow.runReproducibility", {
                defaultValue: "Run and reproducibility record",
              })}
            </summary>
            <dl className="grid gap-3 pb-2 text-xs sm:grid-cols-2">
              <MetadataCell
                label={t("research.workflow.runIdentifier", {
                  defaultValue: "Run ID",
                })}
                value={run.id}
                mono
              />
              <MetadataCell
                label={t("research.workflow.runState", {
                  defaultValue: "Run status",
                })}
                value={t(`research.workflow.runStatus.${run.status}`, {
                  defaultValue: statusLabel(run.status),
                })}
              />
              <MetadataCell
                label={t("research.workflow.environmentHash", {
                  defaultValue: "Environment SHA-256",
                })}
                value={
                  run.environmentHash ??
                  t("research.workflow.environmentPending", {
                    defaultValue: "Not available before completion",
                  })
                }
                mono
              />
              <MetadataCell
                label={t("research.workflow.approvalPayloadHash", {
                  defaultValue: "Payload SHA-256",
                })}
                value={run.payloadSha256}
                mono
              />
            </dl>
          </details>
        )}

        {showNotebook && notebooks.map((artifact) => (
          <div key={artifact.key} className="space-y-3">
            {content === "notebook" && (
              <ul className="border-y border-border-faint">
                <ArtifactDownloadRow artifact={artifact} />
              </ul>
            )}
            <NotebookArtifactPreview artifact={artifact} />
          </div>
        ))}

        {showNotebook && notebooks.length === 0 && (
          <DatasetSurfaceEmpty
            title={t("research.workflow.noExecutedNotebookRecorded")}
            body={t("research.workflow.noExecutedNotebookRecordedBody")}
          />
        )}

        {showArtifacts && <details className="border-t border-border-faint pt-1">
          <summary className="flex min-h-10 cursor-pointer items-center text-xs font-medium text-link hover:underline">
            {t("research.workflow.runtimeLogs", { defaultValue: "Runtime logs" })}
          </summary>
          <div className="grid gap-3 pb-2 xl:grid-cols-2">
            <ConsolePanel
              label={t("research.workflow.stdout", { defaultValue: "stdout" })}
              value={run.stdout}
            />
            <ConsolePanel
              label={t("research.workflow.stderr", { defaultValue: "stderr" })}
              value={run.stderr}
              error
            />
            <div className="xl:col-span-2">
              <ConsolePanel
                label={t("research.workflow.executionLog", {
                  defaultValue: "execution log",
                })}
                value={run.log || run.logs}
              />
            </div>
          </div>
        </details>}
      </div>
    </section>
  );
}

function StructuredResultCard({
  structuredResult,
}: {
  structuredResult: WorkflowStructuredAnalysisResult;
}) {
  const { t } = useTranslation("pages");
  const result = structuredResult.result;
  const operation = result.result;

  return (
    <section className="border-y border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border-faint px-4 py-3">
        <ResearchObjectIcon
          kind={RESEARCH_OBJECT_KIND.result}
          size={15}
          className="text-ok"
        />
        <h3 className="text-sm font-medium text-text">
          {t("research.workflow.structuredResults", {
            defaultValue: "Structured statistical results",
          })}
        </h3>
        <span className="ml-auto text-caption font-medium text-muted">
          {result.resolvedMethod}
        </span>
      </div>
      <div className="space-y-4 p-4">
        <p className="text-xs font-medium text-text">{result.objective}</p>

        <dl className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
          <MetadataCell
            label={t("research.workflow.requestedMethod", {
              defaultValue: "Requested method",
            })}
            value={result.requestedMethod}
          />
          <MetadataCell
            label={t("research.workflow.resolvedMethod", {
              defaultValue: "Resolved method",
            })}
            value={result.resolvedMethod}
          />
          {operation.type === "descriptive" ? (
            <MetadataCell
              label={t("research.workflow.totalRows", {
                defaultValue: "Dataset rows",
              })}
              value={`${result.sampleSummary.totalRows}`}
            />
          ) : (
            <>
              <MetadataCell
                label={t("research.workflow.analyzedRows", {
                  defaultValue: "Analyzed rows",
                })}
                value={`${result.sampleSummary.analyzedRows} / ${result.sampleSummary.totalRows}`}
              />
              <MetadataCell
                label={t("research.workflow.missingRows", {
                  defaultValue: "Excluded or missing rows",
                })}
                value={`${result.sampleSummary.missingRows}`}
              />
            </>
          )}
        </dl>

        <div className="border-y border-border-faint py-2.5 text-xs leading-relaxed text-muted">
          <span className="font-medium text-text">
            {t("research.workflow.methodSelectionReason", {
              defaultValue: "Method selection",
            })}
            :{" "}
          </span>
          {result.methodSelectionReason}
        </div>

        {operation.type === "descriptive" && (
          <div className="overflow-x-auto rounded-input border border-border">
            <table className="min-w-full text-left text-xs">
              <thead className="bg-surface-2 text-caption font-medium text-muted">
                <tr>
                  <th className="px-3 py-2">
                    {t("research.workflow.profileColumn", {
                      defaultValue: "Column",
                    })}
                  </th>
                  <th className="px-3 py-2">
                    {t("research.workflow.sampleSize", {
                      defaultValue: "Sample / missing",
                    })}
                  </th>
                  <th className="px-3 py-2">
                    {t("research.workflow.descriptiveStatistics", {
                      defaultValue: "Statistics",
                    })}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-faint bg-bg text-text">
                {operation.columns.map((column) => (
                  <tr key={column.column}>
                    <td className="px-3 py-2 font-medium">{column.column}</td>
                    <td className="px-3 py-2 text-muted">
                      {column.sampleSize} / {column.missingCount}
                    </td>
                    <td className="px-3 py-2 font-mono text-caption text-muted">
                      {Object.entries(column.statistics)
                        .map(([key, value]) => `${key}=${formatResultValue(value)}`)
                        .join(" · ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {operation.type === "two-group-comparison" && (
          <div className="space-y-3">
            <dl className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
              <MetadataCell
                label={t("research.workflow.comparison", {
                  defaultValue: "Comparison",
                })}
                value={`${operation.outcomeColumn} · ${operation.groups.join(" ↔ ")}`}
                wide
              />
              <MetadataCell
                label={t("research.workflow.sampleSizes", {
                  defaultValue: "Sample sizes",
                })}
                value={formatRecord(operation.sampleSizes)}
                mono
              />
              <MetadataCell
                label={t("research.workflow.testStatistic", {
                  defaultValue: "Test statistic",
                })}
                value={formatNumber(operation.testStatistic)}
              />
              <MetadataCell
                label={t("research.workflow.pValue", {
                  defaultValue: "p-value",
                })}
                value={formatNumber(operation.pValue)}
              />
              <MetadataCell
                label={t("research.workflow.effectSize", {
                  defaultValue: "Effect size",
                })}
                value={`${operation.effectSizeName}: ${formatNumber(operation.effectSize)}`}
              />
              <MetadataCell
                label={t("research.workflow.confidenceInterval", {
                  defaultValue: "Confidence interval",
                })}
                value={formatInterval(operation.confidenceInterval)}
              />
            </dl>
            <div className="overflow-x-auto rounded-input border border-border">
              <table className="min-w-full text-left text-xs">
                <thead className="bg-surface-2 text-caption font-medium text-muted">
                  <tr>
                    <th className="px-3 py-2">
                      {t("research.workflow.group", { defaultValue: "Group" })}
                    </th>
                    <th className="px-3 py-2">
                      {t("research.workflow.descriptiveStatistics", {
                        defaultValue: "Statistics",
                      })}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-faint bg-bg text-text">
                  {operation.groups.map((group) => (
                    <tr key={group}>
                      <td className="px-3 py-2 font-medium">{group}</td>
                      <td className="px-3 py-2 font-mono text-caption text-muted">
                        {formatRecord(operation.descriptiveStatistics[group] ?? {})}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {operation.type === "correlation" && (
          <dl className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
            <MetadataCell
              label={t("research.workflow.variables", {
                defaultValue: "Variables",
              })}
              value={`${operation.xColumn} ↔ ${operation.yColumn}`}
              wide
            />
            <MetadataCell
              label={t("research.workflow.pairedSamples", {
                defaultValue: "Valid pairs",
              })}
              value={`${operation.sampleSize}`}
            />
            <MetadataCell
              label={t("research.workflow.correlation", {
                defaultValue: "Correlation",
              })}
              value={formatNumber(operation.correlation)}
            />
            <MetadataCell
              label={t("research.workflow.pValue", {
                defaultValue: "p-value",
              })}
              value={formatNumber(operation.pValue)}
            />
            <MetadataCell
              label={t("research.workflow.confidenceInterval", {
                defaultValue: "Confidence interval",
              })}
              value={
                operation.confidenceInterval
                  ? formatInterval(operation.confidenceInterval)
                  : t("research.workflow.notCalculated", {
                      defaultValue: "Not calculated",
                    })
              }
            />
          </dl>
        )}

        {(result.warnings.length > 0 || result.limitations.length > 0) && (
          <div className="grid gap-3 lg:grid-cols-2">
            <MethodNotes
              label={t("research.workflow.resultWarnings", {
                defaultValue: "Result warnings",
              })}
              items={result.warnings}
              warning
            />
            <MethodNotes
              label={t("research.workflow.methodLimitations", {
                defaultValue: "Limitations",
              })}
              items={result.limitations}
              warning
            />
          </div>
        )}

        <p className="border-y border-warn/25 py-2.5 text-xs leading-relaxed text-muted">
          {t("research.workflow.nonCausalBoundary", {
            defaultValue:
              "These results describe the observed sample. A group difference or correlation is not, by itself, evidence of causation.",
          })}
        </p>

        <dl className="grid gap-2 text-xs sm:grid-cols-2">
          <MetadataCell
            label={t("research.workflow.structuredResultHash", {
              defaultValue: "Structured result SHA-256",
            })}
            value={structuredResult.resultSha256}
            mono
          />
          <MetadataCell
            label={t("research.workflow.resultLineage", {
              defaultValue: "Spec · Intent · Run",
            })}
            value={`${structuredResult.analysisSpecId} · ${structuredResult.analysisIntentId} · ${structuredResult.runId}`}
            mono
          />
        </dl>
      </div>
    </section>
  );
}

function DatasetReviewGate({
  snapshot,
  mutating,
  onAccept,
}: {
  snapshot: DatasetDetailsSnapshot;
  mutating: boolean;
  onAccept: () => Promise<void>;
}) {
  const { t } = useTranslation("pages");
  const review = snapshot.latestReview;
  if (!review) return null;
  const allowedActions: readonly ResearchWorkflowAllowedAction[] =
    snapshot.allowedActions;
  const canAccept = allowedActions.includes("accept-review-warnings");

  return (
    <section className="border-y border-border bg-surface">
      <WorkflowReviewSummary review={review} />
      {review.verdict === "passed-with-warnings" && (
        <div className="border-t border-warn/20 bg-warn/5 px-4 py-3">
          <div className="flex flex-wrap items-start gap-3">
            <AlertTriangle size={15} className="mt-0.5 shrink-0 text-warn" />
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium text-text">
                {snapshot.reviewWarningAcceptance
                  ? t("research.workflow.reviewWarningsAccepted", {
                      defaultValue: "Review warnings explicitly accepted",
                    })
                  : t("research.workflow.reviewWarningsNeedAcceptance", {
                      defaultValue:
                        "Explicit acceptance is required before completion",
                    })}
              </p>
              <details className="mt-1 text-caption text-muted">
                <summary className="min-h-7 cursor-pointer py-1 font-mono text-link">
                  {t("research.workflow.reviewInputHash", {
                    defaultValue: "Review input SHA-256: {{hash}}",
                    hash: compactIdentifier(review.inputSha256),
                  })}
                </summary>
                <p className="break-all pb-1 font-mono text-text">{review.inputSha256}</p>
              </details>
            </div>
            {canAccept && (
              <button
                type="button"
                onClick={() => void onAccept()}
                disabled={mutating}
                className="flex min-h-11 shrink-0 items-center gap-1.5 rounded-input bg-warn px-4 py-2 text-xs font-medium text-white hover:opacity-90 disabled:opacity-40"
              >
                {mutating ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <CheckCircle2 size={13} />
                )}
                {t("research.workflow.acceptReviewWarnings", {
                  defaultValue: "Accept warnings and complete",
                })}
              </button>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function MetadataCell({
  label,
  value,
  mono = false,
  wide = false,
  warn = false,
}: {
  label: string;
  value: string | number | null | undefined;
  mono?: boolean;
  wide?: boolean;
  warn?: boolean;
}) {
  return (
    <div className={cn("min-w-0", wide && "lg:col-span-2")}>
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd
        className={cn(
          "mt-1 break-all text-xs text-text",
          mono && "font-mono text-caption",
          warn && "font-medium text-warn",
        )}
      >
        {displayValue(value)}
      </dd>
    </div>
  );
}

function ConsolePanel({
  label,
  value,
  error = false,
}: {
  label: string;
  value: string;
  error?: boolean;
}) {
  return (
    <div className="min-w-0">
      <p className="text-xs font-medium text-muted">
        {label}
      </p>
      <pre
        tabIndex={0}
        aria-label={`${label} output`}
        className={cn(
          "mt-1.5 max-h-52 overflow-auto whitespace-pre-wrap break-all rounded-input border bg-surface-2 p-3 font-mono text-xs leading-5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
          error ? "border-error/25 text-error" : "border-border-faint text-text",
        )}
      >
        {value || `No ${label} recorded.`}
      </pre>
    </div>
  );
}

function RunStatus({ status }: { status: WorkflowAnalysisRun["status"] }) {
  const { t } = useTranslation("pages");
  const tone =
    status === "completed"
      ? "bg-ok"
      : status === "failed"
        ? "bg-error"
        : "bg-warn";
  return (
    <span
      role="status"
      aria-live="polite"
      className="inline-flex items-center gap-1.5 text-caption font-medium text-muted"
    >
      <span aria-hidden="true" className={cn("h-1.5 w-1.5 rounded-full", tone)} />
      {t(`research.workflow.runStatus.${status}`, {
        defaultValue: statusLabel(status),
      })}
    </span>
  );
}

function ArtifactDownloadRow({
  artifact,
  previewable = false,
}: {
  artifact: ArtifactPresentation;
  previewable?: boolean;
}) {
  const { t } = useTranslation("pages");
  const [downloading, setDownloading] = useState(false);
  const integrityNotReported = t("research.workflow.integrityNotReported", {
    defaultValue: "Integrity not reported",
  });
  const previewId = artifactPreviewId(artifact);

  const download = async () => {
    if (downloading || !artifact.original) return;
    setDownloading(true);
    try {
      const blob = await scienceCore.fetchArtifactBlob(artifact.original.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = artifact.name;
      anchor.rel = "noreferrer";
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch (error) {
      toast.error(
        t("research.workflow.downloadArtifactFailed", {
          defaultValue: "Could not download artifact: {{error}}",
          error: error instanceof Error ? error.message : String(error),
        }),
      );
    } finally {
      setDownloading(false);
    }
  };

  return (
    <li className="border-b border-border-faint px-1 py-2.5 outline-none focus-within:bg-surface-2 focus-within:ring-1 focus-within:ring-inset focus-within:ring-accent last:border-b-0">
      <div className="flex min-w-0 items-center gap-2">
      <ArtifactKindIcon kind={artifact.kind} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium text-text">
          {artifact.name}
        </p>
        <p className="mt-0.5 truncate text-caption text-muted">
          {t(`research.workflow.artifactKind.${artifact.kind}`, {
            defaultValue: artifact.kind,
          })} · {formatBytes(artifact.sizeBytes)} ·{" "}
          {artifact.integrityStatus === "hash-bound" ? (
            <span title={artifact.contentHash ?? undefined}>
              {compactIdentifier(artifact.contentHash ?? "")}
            </span>
          ) : (
            <span className="text-warn">{integrityNotReported}</span>
          )}
        </p>
      </div>
      {previewable && (
        <a
          href={`#${previewId}`}
          onClick={(event) => {
            const preview = document.getElementById(previewId);
            if (!preview) return;
            event.preventDefault();
            preview.focus();
            preview.scrollIntoView?.({ block: "nearest" });
          }}
          className="flex min-h-9 shrink-0 items-center px-2 text-caption font-medium text-link hover:underline"
        >
          {t("research.workflow.previewArtifact", { defaultValue: "Preview" })}
        </a>
      )}
      <button
        type="button"
        onClick={() => void download()}
        disabled={downloading || !artifact.original}
        aria-label={t("research.workflow.downloadArtifactAria", {
          defaultValue: "Download {{name}}",
          name: artifact.name,
        })}
        className="flex h-11 w-11 items-center justify-center rounded-input text-muted hover:bg-surface-2 hover:text-text disabled:opacity-40"
      >
        {downloading ? (
          <Loader2 size={13} className="animate-spin" />
        ) : (
          <Download size={13} />
        )}
      </button>
      </div>
      <details className="ml-5 border-t border-border-faint pt-1 text-caption text-muted">
        <summary className="min-h-7 cursor-pointer py-1 text-link hover:underline">
          {t("research.workflow.artifactProvenance", {
            defaultValue: "Artifact provenance",
          })}
        </summary>
        <dl className="grid gap-2 pb-1 sm:grid-cols-2">
          <div className="min-w-0">
            <dt>{t("research.workflow.artifactPath", { defaultValue: "Workspace path" })}</dt>
            <dd className="break-all font-mono text-text">{artifact.path ?? "—"}</dd>
          </div>
          <div className="min-w-0">
            <dt>{t("research.workflow.artifactMime", { defaultValue: "MIME type" })}</dt>
            <dd className="break-all font-mono text-text">{artifact.mimeType ?? "—"}</dd>
          </div>
          <div className="min-w-0 sm:col-span-2">
            <dt>{t("research.workflow.artifactHash", { defaultValue: "Content SHA-256" })}</dt>
            <dd className={cn("break-all font-mono", artifact.contentHash ? "text-text" : "text-warn")}>
              {artifact.contentHash ?? integrityNotReported}
            </dd>
          </div>
        </dl>
      </details>
    </li>
  );
}

function ArtifactDirectory({ artifacts }: { artifacts: ArtifactPresentation[] }) {
  const { t } = useTranslation("pages");
  const groups: Array<{
    key: string;
    label: string;
    kinds: ArtifactPresentation["kind"][];
  }> = [
    {
      key: "results",
      label: t("research.workflow.artifactGroupResults", { defaultValue: "Results" }),
      kinds: ["figure", "table", "structured-data"],
    },
    {
      key: "reproducibility",
      label: t("research.workflow.artifactGroupReproducibility", {
        defaultValue: "Reproducibility",
      }),
      kinds: ["notebook", "environment", "log"],
    },
    {
      key: "other",
      label: t("research.workflow.artifactGroupOther", { defaultValue: "Other files" }),
      kinds: ["generic"],
    },
  ];
  const verified = artifacts.filter((artifact) => artifact.integrityStatus === "hash-bound").length;

  return (
    <section className="border-y border-border-faint py-3">
      <div className="flex items-baseline gap-2">
        <h4 className="text-xs font-medium text-text">
          {t("research.workflow.artifactBrowser", {
            defaultValue: "Artifact browser ({{count}})",
            count: artifacts.length,
          })}
        </h4>
        <span className="ml-auto text-caption text-muted">
          {t("research.workflow.hashBoundArtifactCount", {
            defaultValue: "{{verified}} hash-bound",
            verified,
          })}
        </span>
      </div>
      <p className="mt-1 text-caption leading-4 text-muted">
        {t("research.workflow.artifactBrowserBoundary", {
          defaultValue:
            "Files are grouped for navigation; integrity remains tied to each recorded content hash.",
        })}
      </p>
      {artifacts.length === 0 ? (
        <p className="mt-3 text-xs text-muted">
          {t("research.workflow.noRunArtifacts", {
            defaultValue: "No accepted artifacts were recorded for this run.",
          })}
        </p>
      ) : (
        <div className="mt-3 divide-y divide-border">
          {groups.map((group) => {
            const groupedArtifacts = artifacts.filter((artifact) =>
              group.kinds.includes(artifact.kind),
            );
            if (groupedArtifacts.length === 0) return null;
            return (
              <section key={group.key} className="py-2 first:pt-0 last:pb-0">
                <h5 className="flex items-center text-caption font-medium text-muted">
                  {group.label}
                  <span className="ml-auto font-normal">{groupedArtifacts.length}</span>
                </h5>
                <ul className="mt-1">
                  {groupedArtifacts.map((artifact) => (
                    <ArtifactDownloadRow
                      key={artifact.key}
                      artifact={artifact}
                      previewable={artifact.previewMode !== "none"}
                    />
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      )}
    </section>
  );
}

function artifactPreviewId(artifact: ArtifactPresentation): string {
  return `artifact-preview-${artifact.key.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function ArtifactKindIcon({ kind }: { kind: ArtifactPresentation["kind"] }) {
  const objectKind: ResearchObjectKind =
    kind === "figure" || kind === "table" || kind === "notebook" ||
    kind === "log" || kind === "environment"
      ? kind
      : "artifact";
  return <ResearchObjectIcon kind={objectKind} size={13} />;
}

function FigureArtifactPreview({
  artifact,
}: {
  artifact: RenderableArtifact;
}) {
  const { t } = useTranslation("pages");
  const resource = useArtifactResource(artifact.original, "url");
  const integrityNotReported = t("research.workflow.integrityNotReported", {
    defaultValue: "Integrity not reported",
  });
  return (
    <figure
      id={artifactPreviewId(artifact)}
      tabIndex={-1}
      className="scroll-mt-4 overflow-hidden rounded-input border border-[var(--document-border)] bg-[var(--document-bg)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      {resource.loading ? (
        <PreviewLoading />
      ) : resource.error ? (
        <PreviewError message={resource.error} />
      ) : resource.url ? (
        <img
          src={resource.url}
          alt={t("research.workflow.analysisFigureAlt", {
            defaultValue: "Analysis figure {{name}}",
            name: artifact.name,
          })}
          className="max-h-[28rem] w-full object-contain"
        />
      ) : null}
      <figcaption className="border-t border-border-faint px-3 py-2 text-caption text-muted">
        <span className="font-medium text-text">{artifact.name}</span> ·{" "}
        {artifact.mimeType ??
          t("research.workflow.unknownArtifactType", {
            defaultValue: "Unknown type",
          })} ·{" "}
        {artifact.integrityStatus === "hash-bound" ? (
          <span title={artifact.contentHash ?? undefined}>
            {compactIdentifier(artifact.contentHash ?? "")}
          </span>
        ) : (
          <span className="text-warn">{integrityNotReported}</span>
        )}
      </figcaption>
    </figure>
  );
}

function TableArtifactPreview({
  artifact,
  collapseChart = false,
}: {
  artifact: RenderableArtifact;
  collapseChart?: boolean;
}) {
  const { t } = useTranslation("pages");
  const resource = useArtifactResource(artifact.original, "text");
  const table = useMemo(
    () =>
      resource.text == null
        ? null
        : parseTableFile(artifact.path ?? artifact.name, resource.text),
    [artifact.name, artifact.path, resource.text],
  );
  return (
    <div
      id={artifactPreviewId(artifact)}
      tabIndex={-1}
      className="scroll-mt-4 overflow-hidden rounded-input border border-[var(--document-border)] bg-[var(--document-bg)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      {resource.loading ? (
        <PreviewLoading />
      ) : resource.error ? (
        <PreviewError message={resource.error} />
      ) : table ? (
        <ParsedTablePreview table={table} collapseChart={collapseChart} />
      ) : null}
      <p className="border-t border-border-faint px-3 py-2 text-caption text-muted">
        {artifact.name} ·{" "}
        {artifact.contentHash ??
          t("research.workflow.integrityNotReported", {
            defaultValue: "Integrity not reported",
          })}
      </p>
    </div>
  );
}

function TextArtifactPreview({
  artifact,
  heading,
}: {
  artifact: RenderableArtifact;
  heading: string;
}) {
  const { t } = useTranslation("pages");
  const resource = useArtifactResource(artifact.original, "text");
  return (
    <div
      id={artifactPreviewId(artifact)}
      tabIndex={-1}
      className="scroll-mt-4 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      <p className="text-xs font-medium text-muted">
        {heading}
      </p>
      {resource.loading ? (
        <PreviewLoading />
      ) : resource.error ? (
        <PreviewError message={resource.error} />
      ) : (
        <pre
          tabIndex={0}
          aria-label={`${heading} content`}
          className="mt-1.5 max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-input border border-[var(--document-border)] bg-[var(--document-subtle)] p-3 font-mono text-caption leading-4 text-[var(--document-text)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {resource.text ||
            t("research.workflow.emptyEnvironmentManifest", {
              defaultValue: "Empty manifest",
            })}
        </pre>
      )}
    </div>
  );
}

function ParsedTablePreview({
  table,
  collapseChart,
}: {
  table: ParsedTable;
  collapseChart: boolean;
}) {
  const { t } = useTranslation("pages");
  const presented = useMemo(() => presentTable(table), [table]);
  return (
    <div className="dataset-results-grid grid min-h-0">
      <div
        className="dataset-results-table max-h-96 overflow-auto border-b border-[var(--document-border)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        role="region"
        tabIndex={0}
        aria-label={t("research.workflow.tablePreviewAria", {
          defaultValue: "Verified research table preview",
        })}
      >
        <table className="min-w-full border-separate border-spacing-0 text-left text-caption">
        <thead className="sticky top-0 z-[1] bg-[var(--document-subtle)] text-[var(--document-muted)]">
          <tr>
            {presented.columns.map((column, index) => (
              <th
                key={`${column}:${index}`}
                scope="col"
                className="whitespace-nowrap border-b border-[var(--document-border)] px-3 py-2 font-medium"
              >
                {column || `Column ${index + 1}`}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="text-[var(--document-text)]">
          {presented.rows.slice(0, 50).map((row, rowIndex) => (
            <tr
              key={rowIndex}
              className="even:bg-[var(--document-subtle)] hover:bg-[var(--document-selection)]"
            >
              {row.map((cell, cellIndex) => (
                <td
                  key={cellIndex}
                  className="max-w-64 whitespace-pre-wrap break-words border-b border-[var(--document-border)] px-3 py-2 tabular-nums"
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        </table>
        {(presented.truncated || presented.rows.length > 50) && (
          <p className="border-t border-border-faint px-3 py-2 text-caption text-muted">
            {t("research.workflow.tablePreviewTruncated", {
              defaultValue:
                "Preview truncated; download the verified artifact for the complete table.",
            })}
          </p>
        )}
      </div>
      {collapseChart ? (
        <details className="bg-surface">
          <summary className="flex min-h-11 cursor-pointer items-center border-t border-border-faint px-3 text-xs font-medium text-link hover:underline">
            {t("research.workflow.exploreTableChart", {
              defaultValue: "Explore this table as a chart",
            })}
          </summary>
          <div className="min-h-72 border-t border-border-faint">
            <TableChart table={table} />
          </div>
        </details>
      ) : (
        <div className="min-h-72 bg-surface">
          <TableChart table={table} />
        </div>
      )}
    </div>
  );
}

function NotebookArtifactPreview({ artifact }: { artifact: RenderableArtifact }) {
  const { t } = useTranslation("pages");
  const resource = useArtifactResource(artifact.original, "text");
  const parsed = useMemo(() => {
    if (!resource.text) return null;
    try {
      return { cells: parseIpynb(resource.text), error: null };
    } catch (error) {
      return {
        cells: null,
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }, [resource.text]);

  return (
    <section className="overflow-hidden border-y border-border bg-surface">
      <header className="flex min-h-11 items-center gap-2 border-b border-border-faint px-4">
        <ResearchObjectIcon kind={RESEARCH_OBJECT_KIND.notebook} size={14} />
        <h4 className="min-w-0 flex-1 truncate text-xs font-semibold text-text">
          {artifact.name}
        </h4>
        <span className="shrink-0 text-caption text-muted">{t("research.workflow.executedReadOnly")}</span>
      </header>
      {resource.loading ? (
        <PreviewLoading />
      ) : resource.error ? (
        <PreviewError message={resource.error} />
      ) : parsed?.error ? (
        <PreviewError message={parsed.error} />
      ) : parsed?.cells ? (
        <ol className="divide-y divide-border-faint">
          {parsed.cells.map((cell) => (
            <li key={cell.index} className="grid min-w-0 grid-cols-[3rem_minmax(0,1fr)] focus-within:bg-surface-2">
              <span className="border-r border-border-faint px-2 py-3 text-right font-mono text-caption text-muted">
                [{cell.index}]
              </span>
              <div className="min-w-0 px-4 py-3">
                <pre
                  tabIndex={0}
                  aria-label={t("research.workflow.notebookCellCodeAria", { index: cell.index })}
                  className="overflow-x-auto whitespace-pre-wrap break-all rounded-input font-mono text-xs leading-5 text-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  {cell.code || t("research.workflow.emptyNotebookCell")}
                </pre>
                {cell.output && (
                  <pre
                    tabIndex={0}
                    aria-label={t("research.workflow.notebookCellOutputAria", { index: cell.index })}
                    className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-input border-t border-border-faint pt-3 font-mono text-caption leading-5 text-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    {cell.output}
                  </pre>
                )}
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <DatasetSurfaceEmpty
          title={t("research.workflow.notebookContentUnavailable")}
          body={t("research.workflow.notebookContentUnavailableBody")}
        />
      )}
      <footer className="border-t border-border-faint px-4 py-2 text-caption text-muted">
        {t("research.workflow.artifactPreviewFooter", { integrity: artifact.contentHash ?? t("research.workflow.integrityNotReported") })}
      </footer>
    </section>
  );
}

function useArtifactResource(
  artifact: WorkflowAnalysisArtifact,
  mode: "url" | "text",
): {
  loading: boolean;
  error: string | null;
  url: string | null;
  text: string | null;
} {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setLoading(true);
    setError(null);
    setUrl(null);
    setText(null);
    void scienceCore
      .fetchArtifactBlob(artifact.id, { signal: controller.signal })
      .then(async (blob) => {
        if (controller.signal.aborted) return;
        if (mode === "url") {
          objectUrl = URL.createObjectURL(blob);
          setUrl(objectUrl);
        } else {
          const nextText = await blob.text();
          if (!controller.signal.aborted) setText(nextText);
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [artifact.id, mode]);

  return { loading, error, url, text };
}

function PreviewLoading() {
  const { t } = useTranslation("pages");
  return (
    <div className="flex items-center justify-center gap-2 p-6 text-xs text-muted">
      <Loader2 size={13} className="animate-spin" />
      {t("research.workflow.loadingArtifact", {
        defaultValue: "Loading verified artifact…",
      })}
    </div>
  );
}

function PreviewError({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 p-4 text-xs text-error">
      <AlertTriangle size={13} className="mt-0.5 shrink-0" />
      <span className="break-words">{message}</span>
    </div>
  );
}

function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value) || value < 0) {
    return "—";
  }
  if (value < 1_024) return `${value} B`;
  if (value < 1_024 ** 2) return `${(value / 1_024).toFixed(1)} KiB`;
  return `${(value / 1_024 ** 2).toFixed(1)} MiB`;
}

function formatPercent(value: number): string {
  return `${(value * 100).toLocaleString(undefined, {
    maximumFractionDigits: 2,
  })}%`;
}

function formatNumber(value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (value === 0) return "0";
  const absolute = Math.abs(value);
  if (absolute < 0.0001 || absolute >= 1_000_000) {
    return value.toExponential(4);
  }
  return value.toLocaleString(undefined, { maximumSignificantDigits: 6 });
}

function formatResultValue(value: number | string | null): string {
  if (value === null) return "—";
  return typeof value === "number" ? formatNumber(value) : value;
}

function formatRecord(values: Record<string, number | null>): string {
  return Object.entries(values)
    .map(([key, value]) => `${key}=${formatResultValue(value)}`)
    .join(" · ");
}

function formatInterval(value: [number, number]): string {
  return `[${formatNumber(value[0])}, ${formatNumber(value[1])}]`;
}
