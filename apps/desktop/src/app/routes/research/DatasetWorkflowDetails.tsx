import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Download,
  FileCode2,
  FileOutput,
  Hash,
  Loader2,
  ShieldAlert,
  Square,
  Table2,
  SquareTerminal,
  XCircle,
} from "lucide-react";
import type {
  DatasetAnalysisWorkflowSnapshot,
  DatasetProfile,
  ResearchWorkflowAllowedAction,
  WorkflowAnalysisArtifact,
  WorkflowAnalysisExecutionPendingApproval,
  WorkflowAnalysisIntent,
  WorkflowAnalysisRun,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";
import { parseTableFile, type ParsedTable } from "@/lib/csv";
import { scienceCore } from "@/lib/scienceCore";
import { toast } from "@/lib/toast";
import { WorkflowReviewSummary } from "./WorkflowReviewSummary";
import { statusLabel } from "./workflowModel";

interface DatasetWorkflowDetailsProps {
  snapshot: DatasetAnalysisWorkflowSnapshot;
  mutating: boolean;
  onDecision: (decision: "approved" | "rejected") => Promise<void>;
  onCancel: () => Promise<void>;
  onAcceptReviewWarnings: () => Promise<void>;
}

export function DatasetWorkflowDetails({
  snapshot,
  mutating,
  onDecision,
  onCancel,
  onAcceptReviewWarnings,
}: DatasetWorkflowDetailsProps) {
  return (
    <div className="space-y-4">
      {snapshot.datasetProfile && (
        <DatasetProfileCard profile={snapshot.datasetProfile} />
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
      {snapshot.analysisRun && <AnalysisRunCard run={snapshot.analysisRun} />}
      {snapshot.latestReview && (
        <DatasetReviewGate
          snapshot={snapshot}
          mutating={mutating}
          onAccept={onAcceptReviewWarnings}
        />
      )}
    </div>
  );
}

function DatasetProfileCard({ profile }: { profile: DatasetProfile }) {
  const { t } = useTranslation("pages");
  return (
    <section className="overflow-hidden rounded-card border border-border bg-surface shadow-card">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <Table2 size={15} className="text-accent" />
        <h3 className="text-sm font-medium text-text">
          {t("research.workflow.datasetProfile", {
            defaultValue: "Dataset profile",
          })}
        </h3>
        <span className="ml-auto text-[10px] text-muted">
          {t("research.workflow.profileShape", {
            defaultValue: "{{rows}} rows · {{columns}} columns",
            rows: profile.rowCount,
            columns: profile.columnCount,
          })}
        </span>
      </div>
      <div className="space-y-3 p-4">
        <dl className="grid gap-2 text-[11px] sm:grid-cols-2 lg:grid-cols-4">
          <MetadataCell
            label={t("research.workflow.profileFile", {
              defaultValue: "File",
            })}
            value={profile.filename}
          />
          <MetadataCell
            label={t("research.workflow.profileSize", {
              defaultValue: "Size",
            })}
            value={formatBytes(profile.fileSizeBytes)}
          />
          <MetadataCell
            label={t("research.workflow.profileEncoding", {
              defaultValue: "Encoding",
            })}
            value={profile.encoding}
            mono
          />
          <MetadataCell
            label={t("research.workflow.profileDelimiter", {
              defaultValue: "Delimiter",
            })}
            value={JSON.stringify(profile.delimiter)}
            mono
          />
          <MetadataCell
            label={t("research.workflow.datasetSourceId", {
              defaultValue: "Dataset source ID",
            })}
            value={profile.datasetSourceId}
            mono
            wide
          />
          <MetadataCell
            label={t("research.workflow.datasetHash", {
              defaultValue: "Dataset SHA-256",
            })}
            value={profile.contentHash}
            mono
            wide
          />
        </dl>

        <div className="overflow-x-auto rounded-input border border-border">
          <table className="min-w-full text-left text-[11px]">
            <thead className="bg-surface-2 text-[9px] uppercase tracking-wider text-muted">
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
              {profile.columns.map((column) => {
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
                  <tr key={`${column.index}:${column.name}`}>
                    <td className="px-3 py-2 font-medium">{column.name}</td>
                    <td className="px-3 py-2 font-mono text-muted">
                      {column.inferredType}
                    </td>
                    <td className="px-3 py-2">{column.missingCount}</td>
                    <td className="px-3 py-2">{column.uniqueCount}</td>
                    <td className="px-3 py-2 text-muted">
                      {flags.join(" · ") || "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <p className="text-[10px] leading-relaxed text-muted">
          {t("research.workflow.profileSampling", {
            defaultValue:
              "Profiled {{profiled}} of {{read}} rows with {{method}} (limit {{limit}}, seed {{seed}}).",
            profiled: profile.sampling.rowsProfiled,
            read: profile.sampling.rowsRead,
            method: profile.sampling.method,
            limit: profile.sampling.maxSampleRows,
            seed: profile.sampling.seed,
          })}
        </p>

        {profile.warnings.length > 0 && (
          <ul className="space-y-1.5 rounded-input border border-warn/25 bg-warn/5 p-3 text-[11px] text-muted">
            {profile.warnings.map((warning, index) => (
              <li
                key={`${warning.code}:${warning.columnName ?? "dataset"}:${index}`}
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
  snapshot: DatasetAnalysisWorkflowSnapshot;
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
  const code = approval?.code ?? intent.code;
  const codeDiff = approval?.codeDiff ?? intent.codeDiff;

  return (
    <section className="overflow-hidden rounded-card border border-warn/40 bg-surface shadow-card">
      <div className="flex items-center gap-2 border-b border-warn/25 bg-warn/5 px-4 py-3">
        <ShieldAlert size={16} className="text-warn" />
        <h3 className="text-sm font-medium text-text">
          {t("research.workflow.executionApproval", {
            defaultValue: "High-risk execution approval",
          })}
        </h3>
        <span className="ml-auto rounded-full bg-surface px-2 py-0.5 text-[10px] font-medium text-muted ring-1 ring-border">
          {statusLabel(intent.status)}
        </span>
      </div>
      <div className="space-y-3 p-4">
        <p className="text-xs leading-relaxed text-muted">
          {t("research.workflow.executionApprovalBoundary", {
            defaultValue:
              "Review the exact immutable payload. Approving it queues execution automatically; there is no separate manual execute action, and any repair creates a new intent and approval.",
          })}
        </p>

        <dl className="grid gap-2 text-[11px] sm:grid-cols-2 lg:grid-cols-4">
          <MetadataCell
            label={t("research.workflow.approvalRisk", {
              defaultValue: "Risk",
            })}
            value={intent.riskLevel}
            warn
          />
          <MetadataCell
            label={t("research.workflow.runtimeTimeout", {
              defaultValue: "Runtime timeout",
            })}
            value={`${intent.timeoutSeconds} seconds`}
          />
          <MetadataCell
            label={t("research.workflow.repairAttempt", {
              defaultValue: "Repair attempt",
            })}
            value={`${intent.repairAttempt} of 2`}
          />
          <MetadataCell
            label={t("research.workflow.runtimeNetwork", {
              defaultValue: "Network",
            })}
            value={t("research.workflow.runtimeNetworkDisabled", {
              defaultValue: "Disabled in isolated runtime",
            })}
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
            label={t("research.workflow.approvalPayloadHash", {
              defaultValue: "Payload SHA-256",
            })}
            value={intent.payloadSha256}
            mono
            wide
          />
          <MetadataCell
            label={t("research.workflow.datasetSourceId", {
              defaultValue: "Dataset source ID",
            })}
            value={intent.datasetSourceId}
            mono
            wide
          />
          <MetadataCell
            label={t("research.workflow.datasetHash", {
              defaultValue: "Dataset SHA-256",
            })}
            value={intent.datasetContentHash}
            mono
            wide
          />
        </dl>

        <div className="rounded-input border border-border bg-bg p-3">
          <div className="mb-2 flex flex-wrap items-center gap-2 text-[10px] text-muted">
            <FileCode2 size={13} />
            <span className="font-medium uppercase tracking-wider">
              {t("research.workflow.readOnlyCode", {
                defaultValue: "Read-only approved Python",
              })}
            </span>
            <span className="ml-auto">
              {intent.expectedOutputs.join(" · ")}
            </span>
          </div>
          <pre
            aria-label={t("research.workflow.analysisCodeAria", {
              defaultValue: "Analysis code",
            })}
            className="max-h-80 overflow-auto whitespace-pre rounded-input bg-[#17161b] p-3 font-mono text-[10px] leading-4 text-[#d8d4cc]"
          >
            {code}
          </pre>
        </div>

        {codeDiff && (
          <div className="rounded-input border border-warn/25 bg-warn/5 p-3">
            <p className="mb-2 text-[10px] font-medium uppercase tracking-wider text-warn">
              {t("research.workflow.repairDiff", {
                defaultValue: "Repair diff — requires this new approval",
              })}
            </p>
            <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px] leading-4 text-text">
              {codeDiff}
            </pre>
          </div>
        )}

        <div className="rounded-input border border-border-faint bg-bg px-3 py-2.5 text-[11px] text-muted">
          <p className="font-medium text-text">
            {t("research.workflow.runtimeRestrictions", {
              defaultValue: "Runtime restrictions",
            })}
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            <li>
              {t("research.workflow.runtimeRestrictionDataset", {
                defaultValue:
                  "Raw dataset mounted read-only and bound to the displayed hash.",
              })}
            </li>
            <li>
              {t("research.workflow.runtimeRestrictionIsolation", {
                defaultValue:
                  "No external network, shell command, or project-external path.",
              })}
            </li>
            <li>
              {t("research.workflow.runtimeRestrictionArtifacts", {
                defaultValue:
                  "Only verified workspace artifacts may be persisted.",
              })}
            </li>
          </ul>
        </div>

        {intent.errorSummary && (
          <div className="rounded-input border border-error/25 bg-error/5 px-3 py-2.5 text-xs text-muted">
            <p className="font-medium text-error">
              {intent.errorSummary.code}: {intent.errorSummary.userMessage}
            </p>
            {intent.errorSummary.stderrExcerpt && (
              <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px]">
                {intent.errorSummary.stderrExcerpt}
              </pre>
            )}
          </div>
        )}

        {(canApprove || canReject || canCancel) && (
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border-faint pt-3">
            {canCancel && (
              <button
                type="button"
                onClick={() => void onCancel()}
                disabled={mutating}
                className="flex items-center gap-1.5 rounded-input border border-border px-3 py-1.5 text-xs text-text hover:bg-surface-2 disabled:opacity-40"
              >
                {mutating ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <Square size={12} />
                )}
                {t("research.workflow.cancel", { defaultValue: "Cancel" })}
              </button>
            )}
            {canReject && (
              <button
                type="button"
                onClick={() => void onDecision("rejected")}
                disabled={mutating}
                className="flex items-center gap-1.5 rounded-input border border-error/30 px-3 py-1.5 text-xs text-error hover:bg-error/5 disabled:opacity-40"
              >
                {mutating ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <XCircle size={13} />
                )}
                {t("research.workflow.rejectExecution", {
                  defaultValue: "Reject",
                })}
              </button>
            )}
            {canApprove && (
              <button
                type="button"
                onClick={() => void onDecision("approved")}
                disabled={mutating}
                className="flex items-center gap-1.5 rounded-input bg-warn px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-40"
              >
                {mutating ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <ShieldAlert size={13} />
                )}
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

function AnalysisRunCard({ run }: { run: WorkflowAnalysisRun }) {
  const { t } = useTranslation("pages");
  const figures = run.artifacts.filter(
    (artifact) =>
      artifact.artifactType === "figure" ||
      artifact.mimeType.startsWith("image/"),
  );
  const tables = run.artifacts.filter(
    (artifact) => isTableArtifact(artifact),
  );
  const environments = run.artifacts.filter(
    (artifact) => artifact.artifactType === "environment",
  );

  return (
    <section className="overflow-hidden rounded-card border border-border bg-surface shadow-card">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <SquareTerminal size={15} className="text-accent" />
        <h3 className="text-sm font-medium text-text">
          {t("research.workflow.analysisRun", {
            defaultValue: "Analysis run",
          })}
        </h3>
        <RunStatus status={run.status} />
        <code className="ml-auto text-[10px] text-muted">{run.id}</code>
      </div>
      <div className="space-y-4 p-4">
        {run.error && (
          <div className="flex items-start gap-2 rounded-input border border-error/25 bg-error/5 px-3 py-2.5 text-xs text-error">
            <XCircle size={13} className="mt-0.5 shrink-0" />
            <span className="break-words">{run.error}</span>
          </div>
        )}

        <dl className="grid gap-2 text-[11px] sm:grid-cols-2">
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

        <div className="grid gap-3 xl:grid-cols-2">
          <ConsolePanel
            label={t("research.workflow.stdout", { defaultValue: "stdout" })}
            value={run.stdout}
          />
          <ConsolePanel
            label={t("research.workflow.stderr", { defaultValue: "stderr" })}
            value={run.stderr}
            error
          />
        </div>
        <ConsolePanel
          label={t("research.workflow.executionLog", {
            defaultValue: "execution log",
          })}
          value={run.log || run.logs}
        />

        {environments.map((artifact) => (
          <TextArtifactPreview
            key={artifact.id}
            artifact={artifact}
            heading={t("research.workflow.environmentManifest", {
              defaultValue: "Environment manifest",
            })}
          />
        ))}

        {tables.length > 0 && (
          <div className="space-y-3">
            <h4 className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted">
              <Table2 size={14} />
              {t("research.workflow.tableOutputs", {
                defaultValue: "Table outputs",
              })}
            </h4>
            {tables.map((artifact) => (
              <TableArtifactPreview key={artifact.id} artifact={artifact} />
            ))}
          </div>
        )}

        {figures.length > 0 && (
          <div className="space-y-3">
            <h4 className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted">
              <BarChart3 size={14} />
              {t("research.workflow.figureOutputs", {
                defaultValue: "Figure outputs",
              })}
            </h4>
            <div className="grid gap-3 xl:grid-cols-2">
              {figures.map((artifact) => (
                <FigureArtifactPreview key={artifact.id} artifact={artifact} />
              ))}
            </div>
          </div>
        )}

        <div>
          <h4 className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted">
            <FileOutput size={14} />
            {t("research.workflow.runArtifacts", {
              defaultValue: "Verified artifacts ({{count}})",
              count: run.artifacts.length,
            })}
          </h4>
          {run.artifacts.length === 0 ? (
            <p className="mt-2 text-xs text-muted">
              {t("research.workflow.noRunArtifacts", {
                defaultValue: "No accepted artifacts were recorded for this run.",
              })}
            </p>
          ) : (
            <ul className="mt-2 grid gap-2 xl:grid-cols-2">
              {run.artifacts.map((artifact) => (
                <ArtifactDownloadRow key={artifact.id} artifact={artifact} />
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}

function DatasetReviewGate({
  snapshot,
  mutating,
  onAccept,
}: {
  snapshot: DatasetAnalysisWorkflowSnapshot;
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
    <section className="overflow-hidden rounded-card border border-border bg-surface shadow-card">
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
              <p className="mt-1 break-all font-mono text-[9px] text-muted">
                {t("research.workflow.reviewInputHash", {
                  defaultValue: "Review input SHA-256: {{hash}}",
                  hash: review.inputSha256,
                })}
              </p>
            </div>
            {canAccept && (
              <button
                type="button"
                onClick={() => void onAccept()}
                disabled={mutating}
                className="flex shrink-0 items-center gap-1.5 rounded-input bg-warn px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-40"
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
  value: string;
  mono?: boolean;
  wide?: boolean;
  warn?: boolean;
}) {
  return (
    <div
      className={cn(
        "min-w-0 rounded-input border border-border-faint bg-bg px-3 py-2",
        wide && "lg:col-span-2",
      )}
    >
      <dt className="flex items-center gap-1 text-[9px] font-medium uppercase tracking-wider text-muted">
        {mono && <Hash size={10} />} {label}
      </dt>
      <dd
        className={cn(
          "mt-1 break-all text-[11px] text-text",
          mono && "font-mono text-[10px]",
          warn && "font-medium text-warn",
        )}
      >
        {value}
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
      <p className="text-[10px] font-medium uppercase tracking-wider text-muted">
        {label}
      </p>
      <pre
        className={cn(
          "mt-1.5 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-input bg-[#17161b] p-3 font-mono text-[10px] leading-4",
          error ? "text-[#ffb4ab]" : "text-[#d8d4cc]",
        )}
      >
        {value || `No ${label} recorded.`}
      </pre>
    </div>
  );
}

function RunStatus({ status }: { status: WorkflowAnalysisRun["status"] }) {
  const tone =
    status === "completed"
      ? "bg-ok/10 text-ok ring-ok/20"
      : status === "failed"
        ? "bg-error/10 text-error ring-error/20"
        : "bg-warn/10 text-warn ring-warn/20";
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-[10px] font-medium ring-1",
        tone,
      )}
    >
      {statusLabel(status)}
    </span>
  );
}

function ArtifactDownloadRow({
  artifact,
}: {
  artifact: WorkflowAnalysisArtifact;
}) {
  const { t } = useTranslation("pages");
  const [downloading, setDownloading] = useState(false);

  const download = async () => {
    if (downloading) return;
    setDownloading(true);
    try {
      const blob = await scienceCore.fetchArtifactBlob(artifact.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = fileName(artifact.path);
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
    <li className="flex min-w-0 items-center gap-2 rounded-input border border-border bg-bg p-2.5">
      <FileOutput size={13} className="shrink-0 text-muted" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium text-text">
          {fileName(artifact.path)}
        </p>
        <p className="mt-0.5 truncate text-[9px] text-muted">
          {artifact.artifactType} · {formatBytes(artifact.sizeBytes)} · {artifact.contentHash}
        </p>
      </div>
      <button
        type="button"
        onClick={() => void download()}
        disabled={downloading}
        aria-label={`Download ${fileName(artifact.path)}`}
        className="rounded p-1.5 text-muted hover:bg-surface-2 hover:text-text disabled:opacity-40"
      >
        {downloading ? (
          <Loader2 size={13} className="animate-spin" />
        ) : (
          <Download size={13} />
        )}
      </button>
    </li>
  );
}

function FigureArtifactPreview({
  artifact,
}: {
  artifact: WorkflowAnalysisArtifact;
}) {
  const resource = useArtifactResource(artifact, "url");
  return (
    <figure className="overflow-hidden rounded-input border border-border bg-bg">
      {resource.loading ? (
        <PreviewLoading />
      ) : resource.error ? (
        <PreviewError message={resource.error} />
      ) : resource.url ? (
        <img
          src={resource.url}
          alt={`Analysis figure ${fileName(artifact.path)}`}
          className="max-h-[28rem] w-full object-contain"
        />
      ) : null}
      <figcaption className="border-t border-border-faint px-3 py-2 text-[10px] text-muted">
        {fileName(artifact.path)} · {artifact.contentHash}
      </figcaption>
    </figure>
  );
}

function TableArtifactPreview({
  artifact,
}: {
  artifact: WorkflowAnalysisArtifact;
}) {
  const resource = useArtifactResource(artifact, "text");
  const table = useMemo(
    () =>
      resource.text == null
        ? null
        : parseTableFile(artifact.path, resource.text),
    [artifact.path, resource.text],
  );
  return (
    <div className="overflow-hidden rounded-input border border-border bg-bg">
      {resource.loading ? (
        <PreviewLoading />
      ) : resource.error ? (
        <PreviewError message={resource.error} />
      ) : table ? (
        <ParsedTablePreview table={table} />
      ) : null}
      <p className="border-t border-border-faint px-3 py-2 text-[10px] text-muted">
        {fileName(artifact.path)} · {artifact.contentHash}
      </p>
    </div>
  );
}

function TextArtifactPreview({
  artifact,
  heading,
}: {
  artifact: WorkflowAnalysisArtifact;
  heading: string;
}) {
  const { t } = useTranslation("pages");
  const resource = useArtifactResource(artifact, "text");
  return (
    <div>
      <p className="text-[10px] font-medium uppercase tracking-wider text-muted">
        {heading}
      </p>
      {resource.loading ? (
        <PreviewLoading />
      ) : resource.error ? (
        <PreviewError message={resource.error} />
      ) : (
        <pre className="mt-1.5 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-input border border-border bg-bg p-3 font-mono text-[10px] leading-4 text-text">
          {resource.text ||
            t("research.workflow.emptyEnvironmentManifest", {
              defaultValue: "Empty manifest",
            })}
        </pre>
      )}
    </div>
  );
}

function ParsedTablePreview({ table }: { table: ParsedTable }) {
  const { t } = useTranslation("pages");
  return (
    <div className="max-h-80 overflow-auto">
      <table className="min-w-full text-left text-[10px]">
        <thead className="sticky top-0 bg-surface-2 text-muted">
          <tr>
            {table.columns.map((column, index) => (
              <th key={`${column}:${index}`} className="px-3 py-2 font-medium">
                {column || `Column ${index + 1}`}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border-faint text-text">
          {table.rows.slice(0, 50).map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, cellIndex) => (
                <td
                  key={cellIndex}
                  className="max-w-64 whitespace-pre-wrap break-words px-3 py-2"
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {(table.truncated || table.rows.length > 50) && (
        <p className="border-t border-border-faint px-3 py-2 text-[9px] text-muted">
          {t("research.workflow.tablePreviewTruncated", {
            defaultValue:
              "Preview truncated; download the verified artifact for the complete table.",
          })}
        </p>
      )}
    </div>
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

function fileName(path: string): string {
  const segments = path.split("/").filter(Boolean);
  return segments[segments.length - 1] ?? path;
}

function isTableArtifact(artifact: WorkflowAnalysisArtifact): boolean {
  if (artifact.artifactType === "structured-data") return true;
  if (artifact.artifactType !== "dataset") return false;
  const path = artifact.path.toLowerCase();
  const mimeType = artifact.mimeType.toLowerCase();
  return (
    path.endsWith(".csv") ||
    path.endsWith(".tsv") ||
    mimeType === "text/csv" ||
    mimeType === "application/csv" ||
    mimeType === "text/tab-separated-values"
  );
}

function formatBytes(value: number): string {
  if (value < 1_024) return `${value} B`;
  if (value < 1_024 ** 2) return `${(value / 1_024).toFixed(1)} KiB`;
  return `${(value / 1_024 ** 2).toFixed(1)} MiB`;
}
