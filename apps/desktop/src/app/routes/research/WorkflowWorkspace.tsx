import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Circle,
  FileSearch,
  Loader2,
  Play,
  Quote,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Square,
  XCircle,
} from "lucide-react";
import type {
  ResearchGenerationMode,
  ResearchSource,
  ResearchWorkflowMaterializedStep,
  ResearchWorkflowResult,
  ResearchWorkflowReview,
  ResearchWorkflowSnapshot,
  ResearchWorkflowStepSpec,
  ScienceCoreModelDestination,
  WorkflowClaim,
  WorkflowEvidenceRelationship,
} from "@spark/research-domain";
import { MarkdownViewer } from "@/components/markdown-viewer/MarkdownViewer";
import { cn } from "@/lib/cn";
import type { WorkflowConnectionState } from "./useResearchWorkflow";

export interface WorkflowWorkspaceProps {
  snapshot: ResearchWorkflowSnapshot | null;
  sources: ResearchSource[];
  loading: boolean;
  mutating: boolean;
  connection: WorkflowConnectionState;
  error: string | null;
  canStart: boolean;
  remoteDestination: ScienceCoreModelDestination | null;
  legacyContent?: ReactNode;
  onCreate: (
    goal: string,
    generationMode: ResearchGenerationMode,
    remoteDataApproved: boolean,
  ) => Promise<void>;
  onApprovePlan: () => Promise<void>;
  onCancel: () => Promise<void>;
  onRetry: () => Promise<void>;
  onResume: () => Promise<void>;
  onRefresh: () => Promise<void>;
  onNew: () => void;
  onSelectEvidence: (evidence: WorkflowEvidenceRelationship) => void;
  onOpenReview: () => void;
  onOpenActivity: () => void;
}

function statusLabel(status: string): string {
  return status.split("-").join(" ");
}

function generationModeForSnapshot(
  snapshot: ResearchWorkflowSnapshot,
): ResearchGenerationMode {
  return snapshot.workflow.generationMode ?? "local-deterministic";
}

export function WorkflowWorkspace({
  snapshot,
  sources,
  loading,
  mutating,
  connection,
  error,
  canStart,
  remoteDestination,
  legacyContent,
  onCreate,
  onApprovePlan,
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
          remoteDestination={remoteDestination}
          onCreate={onCreate}
        />
        {legacyContent}
      </div>
    );
  }

  const { workflow, plan, allowedActions } = snapshot;
  const cancelling = workflow.cancelRequestedAt != null;
  const showAttention =
    workflow.status === "blocked" ||
    workflow.status === "failed" ||
    workflow.status === "cancelled";

  return (
    <div className="space-y-4">
      <section className="rounded-card border border-border bg-surface px-4 py-3 shadow-card">
        <div className="flex items-start gap-3">
          <Sparkles size={16} className="mt-0.5 shrink-0 text-accent" />
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-medium uppercase tracking-wider text-muted">
              {t("research.workflow.goal", { defaultValue: "Research goal" })}
            </p>
            <h3 className="mt-1 text-sm font-medium leading-relaxed text-text">
              {workflow.goal}
            </h3>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] text-muted">
              <WorkflowStatusBadge snapshot={snapshot} />
              <GenerationModeBadge mode={generationModeForSnapshot(snapshot)} />
              <ConnectionBadge connection={connection} />
              <button type="button" onClick={onOpenActivity} className="text-link hover:underline">
                {t("research.workflow.viewActivity", { defaultValue: "View activity" })}
              </button>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {allowedActions.includes("cancel") && (
              <button
                type="button"
                onClick={() => void onCancel()}
                disabled={mutating || cancelling}
                className="flex items-center gap-1.5 rounded-input border border-border px-2.5 py-1.5 text-xs text-text hover:bg-surface-2 disabled:opacity-40"
              >
                {cancelling ? <Loader2 size={12} className="animate-spin" /> : <Square size={11} />}
                {cancelling
                  ? t("research.workflow.cancelling", { defaultValue: "Cancelling…" })
                  : t("research.workflow.cancel", { defaultValue: "Cancel" })}
              </button>
            )}
            {(workflow.status === "completed" || workflow.status === "cancelled") && (
              <button
                type="button"
                onClick={onNew}
                className="rounded-input border border-border px-2.5 py-1.5 text-xs text-text hover:bg-surface-2"
              >
                {t("research.workflow.newTask", { defaultValue: "New task" })}
              </button>
            )}
          </div>
        </div>
      </section>

      {error && <WorkflowError message={error} onRefresh={onRefresh} />}

      {loading && !plan && (
        <WorkflowWaiting
          label={t("research.workflow.loading", { defaultValue: "Loading workflow state…" })}
        />
      )}

      {workflow.status === "planning" && !plan && (
        <WorkflowWaiting
          label={t("research.workflow.planning", {
            defaultValue: "Inspecting the project and preparing a three-step plan…",
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

      {snapshot.result && (
        <WorkflowResultView
          snapshot={snapshot}
          sources={sources}
          onSelectEvidence={onSelectEvidence}
          onOpenReview={onOpenReview}
        />
      )}

      {workflow.status === "completed" && !snapshot.result && (
        <div className="flex items-start gap-2 rounded-card border border-warn/30 bg-warn/5 p-4 text-xs text-muted">
          <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warn" />
          {t("research.workflow.resultMissing", {
            defaultValue: "The workflow completed, but no research result was returned.",
          })}
        </div>
      )}
    </div>
  );
}

function WorkflowGoalComposer({
  canStart,
  busy,
  remoteDestination,
  onCreate,
}: {
  canStart: boolean;
  busy: boolean;
  remoteDestination: ScienceCoreModelDestination | null;
  onCreate: (
    goal: string,
    generationMode: ResearchGenerationMode,
    remoteDataApproved: boolean,
  ) => Promise<void>;
}) {
  const { t } = useTranslation("pages");
  const [goal, setGoal] = useState("");
  const [generationMode, setGenerationMode] =
    useState<ResearchGenerationMode>("local-deterministic");
  const [remoteDataApproved, setRemoteDataApproved] = useState(false);
  const remoteAssisted = generationMode === "remote-model-assisted";
  const remoteDestinationApprovalKey = remoteDestination
    ? `${remoteDestination.endpointIdentity}:${remoteDestination.model}`
    : null;
  useEffect(() => {
    setRemoteDataApproved(false);
    if (!remoteDestinationApprovalKey) setGenerationMode("local-deterministic");
  }, [remoteDestinationApprovalKey]);
  const canSubmit =
    goal.trim().length > 0 &&
    canStart &&
    !busy &&
    (!remoteAssisted || (remoteDataApproved && remoteDestination != null));
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const next = goal.trim();
    if (!next || !canSubmit) return;
    await onCreate(
      next,
      generationMode,
      remoteAssisted && remoteDataApproved,
    );
  };
  return (
    <form onSubmit={(event) => void submit(event)} className="rounded-card border border-border bg-surface p-4 shadow-card">
      <div className="flex items-center gap-2">
        <Sparkles size={15} className="text-accent" />
        <label htmlFor="research-workflow-goal" className="text-sm font-medium text-text">
          {t("research.workflow.composerLabel", { defaultValue: "Give Spark Agent a research goal" })}
        </label>
      </div>
      <textarea
        id="research-workflow-goal"
        value={goal}
        onChange={(event) => {
          setGoal(event.target.value);
          setRemoteDataApproved(false);
        }}
        rows={4}
        placeholder={t("research.workflow.composerPlaceholder", {
          defaultValue: "Compare the findings, conflicting evidence, and limitations across these papers…",
        })}
        className="mt-3 w-full resize-y rounded-input border border-border bg-bg px-3 py-2.5 text-sm leading-relaxed text-text outline-none placeholder:text-muted focus:border-accent"
      />
      <div className="mt-3" role="group" aria-label={t("research.workflow.generationMode.label", { defaultValue: "Generation mode" })}>
        <p className="text-[10px] font-medium uppercase tracking-wider text-muted">
          {t("research.workflow.generationMode.label", {
            defaultValue: "Generation mode",
          })}
        </p>
        <div className="mt-1.5 grid gap-2 sm:grid-cols-2">
          <button
            type="button"
            aria-pressed={!remoteAssisted}
            onClick={() => {
              setGenerationMode("local-deterministic");
              setRemoteDataApproved(false);
            }}
            className={cn(
              "rounded-input border px-3 py-2 text-left",
              !remoteAssisted
                ? "border-accent/40 bg-accent/5"
                : "border-border bg-bg hover:bg-surface-2",
            )}
          >
            <span className="block text-xs font-medium text-text">
              {t("research.workflow.localDeterministic", {
                defaultValue: "Local deterministic",
              })}
            </span>
            <span className="mt-0.5 block text-[10px] leading-relaxed text-muted">
              {t("research.workflow.localDeterministicHint", {
                defaultValue: "Plan and synthesis stay on this Mac.",
              })}
            </span>
          </button>
          <button
            type="button"
            aria-pressed={remoteAssisted}
            disabled={!remoteDestination}
            onClick={() => {
              if (remoteDestination) setGenerationMode("remote-model-assisted");
            }}
            className={cn(
              "rounded-input border px-3 py-2 text-left",
              remoteAssisted
                ? "border-warn/40 bg-warn/5"
                : "border-border bg-bg hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40",
            )}
          >
            <span className="block text-xs font-medium text-text">
              {t("research.workflow.remoteModelAssisted", {
                defaultValue: "Model-assisted remote",
              })}
            </span>
            <span className="mt-0.5 block text-[10px] leading-relaxed text-muted">
              {t("research.workflow.remoteModelAssistedHint", {
                defaultValue: "Use the configured remote model with explicit data approval.",
              })}
            </span>
          </button>
        </div>
      </div>

      {remoteAssisted && (
        <div className="mt-3 rounded-input border border-warn/30 bg-warn/5 px-3 py-2.5">
          {remoteDestination && (
            <dl className="mb-2 grid gap-1 border-b border-warn/20 pb-2 text-[10px] text-muted sm:grid-cols-2">
              <div>
                <dt className="font-medium text-text">
                  {t("research.workflow.model", { defaultValue: "Model" })}
                </dt>
                <dd className="break-all font-mono">{remoteDestination.model}</dd>
              </div>
              <div>
                <dt className="font-medium text-text">
                  {t("research.endpointHost", { defaultValue: "Endpoint host" })}
                </dt>
                <dd className="break-all font-mono">{remoteDestination.endpointHost}</dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="font-medium text-text">
                  {t("research.endpointIdentity", { defaultValue: "Endpoint identity" })}
                </dt>
                <dd className="break-all font-mono">{remoteDestination.endpointIdentity}</dd>
              </div>
            </dl>
          )}
          <label className="flex cursor-pointer items-start gap-2 text-xs leading-relaxed text-text">
            <input
              type="checkbox"
              checked={remoteDataApproved}
              onChange={(event) => setRemoteDataApproved(event.target.checked)}
              className="mt-0.5 shrink-0 accent-accent"
            />
            <span>
              {t("research.workflow.goalDisclosureApproval", {
                defaultValue:
                  "I approve sending this research goal to {{model}} at {{host}} to create the plan.",
                model: remoteDestination?.model,
                host: remoteDestination?.endpointHost,
              })}
            </span>
          </label>
          <p className="mt-1.5 pl-5 text-[10px] leading-relaxed text-muted">
            {t("research.workflow.passageDisclosureBoundary", {
              defaultValue:
                "No PDF passage is sent at this step. Approving the generated plan later authorizes only verified passages represented by that approval's affected resources, for that plan version.",
            })}
          </p>
        </div>
      )}
      <div className="mt-3 flex items-center gap-3 border-t border-border-faint pt-3">
        <p className="min-w-0 flex-1 text-[11px] leading-relaxed text-muted">
          {canStart
            ? remoteAssisted
              ? t("research.workflow.remoteComposerHint", {
                  defaultValue:
                    "The goal is sent only after explicit approval; source passages require a second approval on the generated plan.",
                })
              : t("research.workflow.localComposerHint", {
                  defaultValue:
                    "This workflow uses only indexed PDFs already in this project.",
                })
            : t("research.workflow.needsPdf", {
                defaultValue: "Import at least one indexed PDF to start a research task.",
              })}
        </p>
        <button
          type="submit"
          disabled={!canSubmit}
          className="flex shrink-0 items-center gap-1.5 rounded-input bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-40"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <ChevronRight size={13} />}
          {busy
            ? t("research.workflow.starting", { defaultValue: "Starting…" })
            : t("research.workflow.start", { defaultValue: "Create plan" })}
        </button>
      </div>
    </form>
  );
}

function WorkflowPlanStepDetails({
  step,
  sources,
}: {
  step: ResearchWorkflowStepSpec;
  sources: ResearchSource[];
}) {
  const { t } = useTranslation("pages");
  const hasFrozenSources =
    "sourceKind" in step.inputs && step.inputs.frozenSources != null;
  const approvedSources =
    "sourceKind" in step.inputs
      ? (step.inputs.frozenSources?.map((source) => ({
          ...source,
          legacyAllowlist: false,
        })) ??
        step.inputs.sourceIds?.map((sourceId) => ({
          sourceId,
          title:
            sources.find((item) => item.id === sourceId)?.title ??
            t("research.workflow.unknownSource", { defaultValue: "Unknown source" }),
          contentHash: null,
          pageManifestHash: null,
          legacyAllowlist: true,
        })) ?? [])
      : [];
  const fields =
    "sourceKind" in step.inputs
      ? [
          [
            t("research.workflow.sourceKind", { defaultValue: "Source kind" }),
            step.inputs.sourceKind.toUpperCase(),
          ],
          [
            t("research.workflow.sourceScope", { defaultValue: "Source scope" }),
            !hasFrozenSources && step.inputs.sourceIds === null
              ? t("research.workflow.readySourcesAtRun", {
                  defaultValue: "Ready project PDFs resolved when the plan runs",
                })
              : hasFrozenSources
                ? t("research.workflow.frozenSourceCount", {
                    defaultValue: "{{count}} content-bound source(s)",
                    count: approvedSources.length,
                  })
                : t("research.workflow.legacySourceCount", {
                    defaultValue: "{{count}} legacy allowlisted source ID(s)",
                    count: approvedSources.length,
                  }),
          ],
        ]
      : "query" in step.inputs
        ? [
            [t("research.workflow.searchQuery", { defaultValue: "Search query" }), step.inputs.query],
            [
              t("research.workflow.maxPassages", { defaultValue: "Maximum passages" }),
              String(step.inputs.maxPassages),
            ],
            [
              t("research.workflow.maxPerSource", { defaultValue: "Maximum per source" }),
              String(step.inputs.maxPerSource),
            ],
          ]
        : [
            [
              t("research.workflow.maxClaims", { defaultValue: "Maximum claims" }),
              String(step.inputs.maxClaims),
            ],
          ];
  return (
    <div className="mt-2 space-y-2 rounded-input border border-border-faint bg-bg px-3 py-2.5">
      <dl className="grid gap-2 text-[11px] sm:grid-cols-2">
        {fields.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-[9px] font-medium uppercase tracking-wider text-muted">
              {label}
            </dt>
            <dd className="mt-0.5 break-words text-text">{value}</dd>
          </div>
        ))}
      </dl>
      {approvedSources.length > 0 && (
        <div>
          <p className="text-[9px] font-medium uppercase tracking-wider text-muted">
            {hasFrozenSources
              ? t("research.workflow.frozenSources", {
                  defaultValue: "Content-bound sources",
                })
              : t("research.workflow.legacySources", {
                  defaultValue: "Legacy allowlisted source IDs",
                })}
          </p>
          <ul className="mt-1 space-y-1">
            {approvedSources.map((source) => (
              <li
                key={source.sourceId}
                className="rounded-input border border-border-faint bg-surface px-2 py-1.5 text-[11px] text-text"
              >
                <p className="break-words">
                  {source.title}{" "}
                  <code className="text-[10px] text-muted">({source.sourceId})</code>
                </p>
                {source.contentHash && (
                  <p className="mt-1 break-all font-mono text-[9px] text-muted">
                    {t("research.workflow.fileHash", { defaultValue: "File SHA-256" })}: {source.contentHash}
                  </p>
                )}
                {source.pageManifestHash && (
                  <p className="mt-1 break-all font-mono text-[9px] text-muted">
                    {t("research.workflow.pageManifestHash", {
                      defaultValue: "Parsed-page manifest SHA-256",
                    })}: {source.pageManifestHash}
                  </p>
                )}
                {source.legacyAllowlist && (
                  <p className="mt-1 text-[9px] leading-relaxed text-warn">
                    {t("research.workflow.legacySourceBoundary", {
                      defaultValue:
                        "The title is current metadata; this historical plan bound only the source ID, not file content.",
                    })}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div>
        <p className="text-[9px] font-medium uppercase tracking-wider text-muted">
          {t("research.workflow.acceptanceCriteria", {
            defaultValue: "Acceptance criteria",
          })}
        </p>
        <ul className="mt-1 flex flex-wrap gap-1.5">
          {step.acceptanceCriteria.map((criterion) => (
            <li
              key={criterion}
              className="rounded-full bg-surface-2 px-2 py-0.5 text-[10px] text-text ring-1 ring-border"
            >
              {criterion}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function WorkflowPlanCard({
  snapshot,
  sources,
  mutating,
  onApprove,
  onCancel,
}: {
  snapshot: ResearchWorkflowSnapshot;
  sources: ResearchSource[];
  mutating: boolean;
  onApprove: () => Promise<void>;
  onCancel: () => Promise<void>;
}) {
  const { t } = useTranslation("pages");
  const { plan, allowedActions } = snapshot;
  if (!plan) return null;
  const approval = snapshot.pendingApprovals.find(
    (item) => item.kind === "plan" && item.planId === plan.id,
  );
  const generationMode = generationModeForSnapshot(snapshot);
  const remoteAssisted = generationMode === "remote-model-assisted";
  return (
    <section className="overflow-hidden rounded-card border border-accent/30 bg-surface shadow-card">
      <div className="flex items-center gap-2 border-b border-accent/20 bg-accent/5 px-4 py-3">
        <FileSearch size={16} className="text-accent" />
        <h3 className="text-sm font-medium text-text">
          {t("research.workflow.planHeading", { defaultValue: "Review the research plan" })}
        </h3>
        <span className="ml-auto rounded-full bg-surface px-2 py-0.5 text-[10px] text-muted ring-1 ring-border">
          {t("research.workflow.planVersion", {
            defaultValue: "Plan v{{version}}",
            version: plan.version,
          })}
        </span>
      </div>
      <div className="p-4">
        <ol className="space-y-3">
          {plan.spec.steps.map((step, index) => (
            <li key={step.key} className="flex items-start gap-3">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface-2 text-[10px] font-semibold text-muted ring-1 ring-border">
                {index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-medium text-text">{step.objective}</p>
                  <code className="rounded-full bg-surface-2 px-2 py-0.5 text-[9px] text-muted ring-1 ring-border">
                    {step.type}
                  </code>
                </div>
                <p className="mt-1 text-[11px] text-muted">
                  {t("research.workflow.expectedOutputs", {
                    defaultValue: "Outputs: {{outputs}}",
                    outputs: step.expectedOutputs.join(" · "),
                  })}
                </p>
                <WorkflowPlanStepDetails step={step} sources={sources} />
              </div>
            </li>
          ))}
        </ol>

        <dl className="mt-4 grid gap-2 rounded-input border border-border bg-bg px-3 py-2.5 text-[11px] sm:grid-cols-2 lg:grid-cols-4">
          <div className="min-w-0">
            <dt className="text-[9px] font-medium uppercase tracking-wider text-muted">
              {t("research.workflow.planGenerator", {
                defaultValue: "Plan generator",
              })}
            </dt>
            <dd className="mt-0.5 break-words text-text">
              {plan.generator ??
                t("research.workflow.notReported", {
                  defaultValue: "Not reported (legacy snapshot)",
                })}
            </dd>
          </div>
          <div className="min-w-0">
            <dt className="text-[9px] font-medium uppercase tracking-wider text-muted">
              {t("research.workflow.promptVersion", {
                defaultValue: "Prompt version",
              })}
            </dt>
            <dd className="mt-0.5 break-words text-text">
              {plan.promptVersion ??
                t("research.workflow.notReported", {
                  defaultValue: "Not reported (legacy snapshot)",
                })}
            </dd>
          </div>
          <div className="min-w-0">
            <dt className="text-[9px] font-medium uppercase tracking-wider text-muted">
              {t("research.workflow.model", { defaultValue: "Model" })}
            </dt>
            <dd className="mt-0.5 break-words text-text">
              {plan.model ??
                (remoteAssisted
                  ? t("research.workflow.notReported", {
                      defaultValue: "Not reported (legacy snapshot)",
                    })
                  : t("research.workflow.noRemoteModel", {
                      defaultValue: "None — local deterministic",
                    }))}
            </dd>
          </div>
          <div className="min-w-0">
            <dt className="text-[9px] font-medium uppercase tracking-wider text-muted">
              {t("research.workflow.planHash", { defaultValue: "Immutable plan hash" })}
            </dt>
            <dd className="mt-0.5 break-all font-mono text-[10px] text-text">
              {plan.planSha256}
            </dd>
          </div>
        </dl>

        <div
          className={cn(
            "mt-4 flex items-start gap-2 rounded-input border px-3 py-2.5 text-xs leading-relaxed text-muted",
            remoteAssisted
              ? "border-warn/30 bg-warn/5"
              : "border-ok/25 bg-ok/5",
          )}
        >
          <ShieldCheck
            size={15}
            className={cn(
              "mt-0.5 shrink-0",
              remoteAssisted ? "text-warn" : "text-ok",
            )}
          />
          <span>
            <strong className="font-medium text-text">
              {remoteAssisted
                ? t("research.workflow.remoteAssisted", {
                    defaultValue: "Remote model-assisted.",
                  })
                : t("research.workflow.localOnly", { defaultValue: "Local only." })}
            </strong>{" "}
            {remoteAssisted
              ? t("research.workflow.remoteAssistedDetail", {
                  defaultValue:
                    "The research goal was sent under the approval given when this task was created. Approving this plan permits only verified passages represented by the affected resources below to be sent to the configured model provider, for this plan version. It does not authorize code execution, dependency installation, unrelated network access, or a future plan version.",
                })
              : t("research.workflow.localOnlyDetail", {
                  defaultValue:
                    "This plan reads indexed project PDFs and does not send data to an external service. Plan approval does not authorize future network access or code execution.",
                })}
          </span>
        </div>

        {approval?.reason && (
          <div className="mt-3 space-y-1 text-[11px] leading-relaxed text-muted">
            <p>{approval.reason}</p>
            <p className="break-all font-mono text-[9px]">
              {t("research.workflow.approvalPayloadHash", {
                defaultValue: "Approval payload SHA-256",
              })}: {approval.payloadSha256}
            </p>
          </div>
        )}

        {remoteAssisted && approval && (
          <div className="mt-3 rounded-input border border-border bg-bg px-3 py-2.5">
            <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted">
              <span>
                {t("research.workflow.remoteApprovalScope", {
                  defaultValue: "Remote approval scope",
                })}
              </span>
              <span className="ml-auto rounded-full bg-warn/10 px-2 py-0.5 normal-case tracking-normal text-warn ring-1 ring-warn/20">
                {t("research.workflow.riskLevel", {
                  defaultValue: "{{level}} risk",
                  level: approval.riskLevel,
                })}
              </span>
            </div>
            <ul className="mt-2 space-y-1">
              {approval.affectedResources.map((resource) => (
                <li key={resource}>
                  <code className="break-all text-[10px] text-text">{resource}</code>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[10px] leading-relaxed text-muted">
              {t("research.workflow.remoteResourceBoundary", {
                defaultValue:
                  "Only entries marked verified-passages:remote authorize data transfer. The project entry identifies the local workflow scope.",
              })}
            </p>
          </div>
        )}

        <div className="mt-4 flex items-center justify-end gap-2 border-t border-border-faint pt-3">
          {allowedActions.includes("cancel") && (
            <button
              type="button"
              onClick={() => void onCancel()}
              disabled={mutating}
              className="rounded-input border border-border px-3 py-1.5 text-xs text-text hover:bg-surface-2 disabled:opacity-40"
            >
              {t("research.workflow.cancelPlan", { defaultValue: "Cancel" })}
            </button>
          )}
          {allowedActions.includes("approve-plan") && (
            <button
              type="button"
              onClick={() => void onApprove()}
              disabled={mutating || !approval}
              className="flex items-center gap-1.5 rounded-input bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-40"
            >
              {mutating ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
              {t("research.workflow.approveRun", { defaultValue: "Approve & run" })}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

function WorkflowProgress({ snapshot }: { snapshot: ResearchWorkflowSnapshot }) {
  const { t } = useTranslation("pages");
  const { workflow, plan } = snapshot;
  if (!plan) return null;
  const heading = workflow.cancelRequestedAt
    ? t("research.workflow.cancelling", { defaultValue: "Cancelling…" })
    : workflow.status === "reviewing"
      ? t("research.workflow.reviewing", { defaultValue: "Reviewing claims and evidence" })
      : t("research.workflow.progressHeading", { defaultValue: "Research progress" });
  return (
    <section className="rounded-card border border-border bg-surface p-4">
      <div className="flex items-center gap-2">
        {workflow.status === "reviewing" ? (
          <ShieldCheck size={15} className="text-accent" />
        ) : (
          <Loader2 size={15} className="animate-spin text-accent" />
        )}
        <h3 className="text-sm font-medium text-text">{heading}</h3>
      </div>
      <ol className="mt-4 space-y-2">
        {plan.steps.map((step) => (
          <WorkflowStepRow key={step.id} step={step} current={workflow.currentStepId === step.id} />
        ))}
      </ol>
    </section>
  );
}

function WorkflowStepRow({
  step,
  current,
}: {
  step: ResearchWorkflowMaterializedStep;
  current: boolean;
}) {
  const { t } = useTranslation("pages");
  const icon =
    step.status === "completed" ? (
      <CheckCircle2 size={15} className="text-ok" />
    ) : step.status === "failed" || step.status === "blocked" ? (
      <XCircle size={15} className="text-error" />
    ) : step.status === "running" ? (
      <Loader2 size={15} className="animate-spin text-accent" />
    ) : (
      <Circle size={14} className="text-muted" />
    );
  return (
    <li className={cn("rounded-input border px-3 py-2.5", current ? "border-accent/35 bg-accent/5" : "border-border bg-bg")}>
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 shrink-0">{icon}</span>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium text-text">{step.objective}</p>
          <p className="mt-1 text-[10px] text-muted">
            {t(`research.workflow.stepStatus.${step.status}`, {
              defaultValue: statusLabel(step.status),
            })}
            {step.retryCount > 0 &&
              t("research.workflow.retryCount", {
                defaultValue: " · retry {{count}}",
                count: step.retryCount,
              })}
          </p>
          {step.outputSummary && (
            <p className="mt-1.5 text-[11px] leading-relaxed text-muted">{step.outputSummary}</p>
          )}
        </div>
      </div>
    </li>
  );
}

function WorkflowNeedsAttention({
  snapshot,
  mutating,
  onRetry,
  onResume,
  onNew,
}: {
  snapshot: ResearchWorkflowSnapshot;
  mutating: boolean;
  onRetry: () => Promise<void>;
  onResume: () => Promise<void>;
  onNew: () => void;
}) {
  const { t } = useTranslation("pages");
  const { workflow, allowedActions } = snapshot;
  return (
    <section className="rounded-card border border-warn/35 bg-warn/5 p-4">
      <div className="flex items-start gap-3">
        <AlertTriangle size={16} className="mt-0.5 shrink-0 text-warn" />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-medium text-text">
            {workflow.status === "cancelled"
              ? t("research.workflow.cancelledHeading", { defaultValue: "Research task cancelled" })
              : t("research.workflow.attentionHeading", { defaultValue: "This task needs attention" })}
          </h3>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            {workflow.blockingReason?.userMessage ??
              t("research.workflow.attentionDefault", {
                defaultValue: "Review the completed steps and choose an available recovery action.",
              })}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {allowedActions.includes("resume") && (
              <button
                type="button"
                onClick={() => void onResume()}
                disabled={mutating}
                className="flex items-center gap-1.5 rounded-input bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg disabled:opacity-40"
              >
                {mutating ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                {t("research.workflow.resume", { defaultValue: "Resume" })}
              </button>
            )}
            {allowedActions.includes("retry") && (
              <button
                type="button"
                onClick={() => void onRetry()}
                disabled={mutating}
                className="flex items-center gap-1.5 rounded-input border border-border bg-surface px-3 py-1.5 text-xs text-text disabled:opacity-40"
              >
                {mutating ? <Loader2 size={13} className="animate-spin" /> : <RotateCcw size={13} />}
                {t("research.workflow.retry", { defaultValue: "Retry" })}
              </button>
            )}
            {allowedActions.length === 0 && (
              <button
                type="button"
                onClick={onNew}
                className="rounded-input border border-border bg-surface px-3 py-1.5 text-xs text-text"
              >
                {t("research.workflow.newTask", { defaultValue: "New task" })}
              </button>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function WorkflowResultView({
  snapshot,
  sources,
  onSelectEvidence,
  onOpenReview,
}: {
  snapshot: ResearchWorkflowSnapshot;
  sources: ResearchSource[];
  onSelectEvidence: (evidence: WorkflowEvidenceRelationship) => void;
  onOpenReview: () => void;
}) {
  const { t } = useTranslation("pages");
  const result = snapshot.result;
  if (!result) return null;
  const frozenIntegrity = result.integrityStatus === "verified-frozen-v2";
  const reviewPassed =
    snapshot.workflow.status === "completed" &&
    snapshot.latestReview?.verdict === "passed" &&
    frozenIntegrity;
  const legacyReviewPassed =
    snapshot.workflow.status === "completed" &&
    snapshot.latestReview?.verdict === "passed" &&
    snapshot.latestReview.result.schemaVersion === "1";
  const reviewNeedsRevision = snapshot.latestReview?.verdict === "revision-required";
  const generationMode = generationModeForSnapshot(snapshot);
  return (
    <article className="space-y-4">
      <section className="rounded-card border border-border bg-surface p-4 shadow-card">
        <div className="mb-3 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-muted">
          {reviewPassed ? (
            <CheckCircle2 size={14} className="text-ok" />
          ) : (
            <AlertTriangle
              size={14}
              className={reviewNeedsRevision ? "text-warn" : "text-muted"}
            />
          )}
          {reviewPassed
            ? t("research.workflow.resultHeading", {
                defaultValue: "Evidence-integrity review passed",
              })
            : legacyReviewPassed
              ? t("research.workflow.legacyResultHeading", {
                  defaultValue: "Legacy review passed — result was not immutably frozen",
                })
            : reviewNeedsRevision
              ? t("research.workflow.resultNeedsRevision", {
                  defaultValue: "Research result needs revision",
                })
              : t("research.workflow.resultPendingReview", {
                  defaultValue: "Provisional evidence map — review pending",
                })}
          {snapshot.latestReview && (
            <button type="button" onClick={onOpenReview} className="ml-auto text-link hover:underline">
              {t("research.workflow.reviewDetails", { defaultValue: "Review details" })}
            </button>
          )}
        </div>
        <GenerationBoundary mode={generationMode} result={result} />
        <MarkdownViewer>{result.summary}</MarkdownViewer>
      </section>

      <div className="flex items-center gap-2 pt-1">
        <h3 className="text-xs font-medium uppercase tracking-wider text-muted">
          {t("research.claimsHeading", {
            defaultValue: "Claims ({{count}})",
            count: result.claims.length,
          })}
        </h3>
        <div className="h-px flex-1 bg-border" />
      </div>

      {result.claims.map((claim, index) => (
        <WorkflowClaimCard
          key={claim.id}
          claim={claim}
          index={index}
          sources={sources}
          onSelectEvidence={onSelectEvidence}
        />
      ))}

      {result.unresolvedQuestions.length > 0 && (
        <section className="rounded-card border border-warn/30 bg-warn/5 p-4">
          <h3 className="flex items-center gap-2 text-xs font-medium text-text">
            <AlertTriangle size={14} className="text-warn" />
            {t("research.unresolvedHeading", { defaultValue: "Unresolved questions" })}
          </h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed text-muted">
            {result.unresolvedQuestions.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </section>
      )}
    </article>
  );
}

function WorkflowClaimCard({
  claim,
  index,
  sources,
  onSelectEvidence,
}: {
  claim: WorkflowClaim;
  index: number;
  sources: ResearchSource[];
  onSelectEvidence: (evidence: WorkflowEvidenceRelationship) => void;
}) {
  const { t } = useTranslation("pages");
  const tone =
    claim.supportStatus === "supported"
      ? "bg-ok/10 text-ok ring-ok/20"
      : claim.supportStatus === "pending-review"
        ? "bg-surface-2 text-muted ring-border"
      : claim.supportStatus === "contradicted" || claim.supportStatus === "insufficient-evidence"
        ? "bg-error/10 text-error ring-error/20"
        : "bg-warn/10 text-warn ring-warn/20";
  return (
    <section className="rounded-card border border-border bg-surface p-4">
      <div className="flex items-start gap-3">
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface-2 text-[10px] font-semibold text-muted ring-1 ring-border">
          {index + 1}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium leading-relaxed text-text">{claim.statement}</p>
          <span className={cn("mt-2 inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium ring-1", tone)}>
            {t(`research.claimSupport.${claim.supportStatus}`, {
              defaultValue: statusLabel(claim.supportStatus),
            })}
          </span>
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
          const source = sources.find((item) => item.id === evidence.sourceId);
          const sourceTitle =
            evidence.sourceTitle ??
            source?.title ??
            t("research.unknownSource", { defaultValue: "Unknown source" });
          const frozenCitation =
            evidence.sourceTitle !== null &&
            evidence.sourceContentHash !== null &&
            evidence.sourcePageManifestHash !== null;
          return (
            <button
              key={evidence.evidenceId}
              type="button"
              onClick={() => onSelectEvidence(evidence)}
              className="group w-full rounded-input border border-border bg-bg px-3 py-2.5 text-left hover:border-accent/30 hover:bg-surface-2"
            >
              <span className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-wide text-muted">
                <Quote size={12} className={evidence.relationship === "contradicting" ? "text-warn" : "text-accent"} />
                {t("research.evidencePage", {
                  defaultValue: "{{source}}, page {{page}}",
                  source: sourceTitle,
                  page: evidence.pageLabel ?? evidence.pageIndex + 1,
                })}
                <span className="ml-auto normal-case tracking-normal">
                  {t(`research.evidenceRelationship.${evidence.relationship}`, {
                    defaultValue: evidence.relationship,
                  })}
                </span>
                <ChevronRight size={12} className="transition-transform group-hover:translate-x-0.5" />
              </span>
              <span className="mt-1.5 block line-clamp-4 font-serif text-[13px] leading-relaxed text-text/90">
                {evidence.text}
              </span>
              <span className="mt-2 block space-y-0.5 border-t border-border-faint pt-2 font-mono text-[9px] leading-relaxed text-muted">
                <span className="block break-all">
                  {t("research.workflow.evidenceId", { defaultValue: "Evidence ID" })}: {evidence.evidenceId}
                </span>
                <span className="block break-all">
                  {t("research.workflow.sourceId", { defaultValue: "Source ID" })}: {evidence.sourceId}
                </span>
                <span className="block break-all">
                  {t("research.workflow.quoteHash", { defaultValue: "Quote SHA-256" })}: {evidence.quoteHash}
                </span>
                {evidence.sourceContentHash && (
                  <span className="block break-all">
                    {t("research.workflow.fileHash", { defaultValue: "File SHA-256" })}: {evidence.sourceContentHash}
                  </span>
                )}
                {evidence.sourcePageManifestHash && (
                  <span className="block break-all">
                    {t("research.workflow.pageManifestHash", {
                      defaultValue: "Parsed-page manifest SHA-256",
                    })}: {evidence.sourcePageManifestHash}
                  </span>
                )}
                {!frozenCitation && (
                  <span className="block font-sans text-warn">
                    {t("research.workflow.legacyCitationBoundary", {
                      defaultValue:
                        "Legacy citation: the displayed source title is current metadata and was not frozen with this result.",
                    })}
                  </span>
                )}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function WorkflowStatusBadge({ snapshot }: { snapshot: ResearchWorkflowSnapshot }) {
  const { t } = useTranslation("pages");
  const { workflow } = snapshot;
  const status = workflow.cancelRequestedAt ? "cancelling" : workflow.status;
  const tone =
    workflow.status === "completed"
      ? "bg-ok/10 text-ok ring-ok/20"
      : workflow.status === "blocked" || workflow.status === "failed"
        ? "bg-error/10 text-error ring-error/20"
        : "bg-accent/10 text-accent ring-accent/20";
  return (
    <span className={cn("rounded-full px-2 py-0.5 font-medium ring-1", tone)}>
      {t(`research.workflowStatus.${status}`, { defaultValue: statusLabel(status) })}
    </span>
  );
}

function GenerationModeBadge({ mode }: { mode: ResearchGenerationMode }) {
  const { t } = useTranslation("pages");
  const remoteAssisted = mode === "remote-model-assisted";
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 font-medium ring-1",
        remoteAssisted
          ? "bg-warn/10 text-warn ring-warn/20"
          : "bg-surface-2 text-muted ring-border",
      )}
    >
      {remoteAssisted
        ? t("research.workflow.generationMode.remote", {
            defaultValue: "remote model-assisted",
          })
        : t("research.workflow.generationMode.local", {
            defaultValue: "local deterministic",
          })}
    </span>
  );
}

function GenerationBoundary({
  mode,
  result,
}: {
  mode: ResearchGenerationMode;
  result: ResearchWorkflowResult;
}) {
  const { t } = useTranslation("pages");
  const remoteOutput = result.generator.startsWith("remote-model-assisted-");
  return (
    <div className="mb-3 rounded-input border border-border-faint bg-bg px-3 py-2 text-[11px] leading-relaxed text-muted">
      <strong className="font-medium text-text">
        {remoteOutput
          ? t("research.workflow.modelAssistedResult", {
              defaultValue: "Model-assisted synthesis.",
            })
          : t("research.workflow.localResult", {
              defaultValue: "Locally generated synthesis.",
            })}
      </strong>{" "}
      {t("research.workflow.generationBoundary", {
        defaultValue:
          "Generation mode is provenance, not evidence strength. Claim support comes from the cited source relationships and the separate deterministic evidence-integrity review.",
      })}
      <p
        className={cn(
          "mt-2 rounded-input px-2 py-1 text-[10px] ring-1",
          result.integrityStatus === "verified-frozen-v2"
            ? "bg-ok/5 text-ok ring-ok/20"
            : "bg-warn/5 text-warn ring-warn/20",
        )}
      >
        {result.integrityStatus === "verified-frozen-v2"
          ? t("research.workflow.frozenResultIntegrity", {
              defaultValue:
                "Frozen result verified: answer, ordered claims, citations, source files, and parsed-page manifests match the completed review.",
            })
          : t("research.workflow.unfrozenResultIntegrity", {
              defaultValue:
                "Unfrozen result: review is pending, requires revision, or predates immutable result snapshots.",
            })}
      </p>
      <dl className="mt-2 grid gap-2 border-t border-border-faint pt-2 sm:grid-cols-3">
        <div className="min-w-0">
          <dt className="text-[9px] font-medium uppercase tracking-wider text-muted">
            {t("research.workflow.resultGenerator", { defaultValue: "Generator" })}
          </dt>
          <dd className="mt-0.5 break-words font-mono text-[10px] text-text">
            {result.generator}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-[9px] font-medium uppercase tracking-wider text-muted">
            {t("research.workflow.model", { defaultValue: "Model" })}
          </dt>
          <dd className="mt-0.5 break-words font-mono text-[10px] text-text">
            {result.model ??
              (mode === "local-deterministic"
                ? t("research.workflow.noRemoteModel", {
                    defaultValue: "None — local deterministic",
                  })
                : t("research.workflow.notReported", {
                    defaultValue: "Not reported (legacy snapshot)",
                  }))}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-[9px] font-medium uppercase tracking-wider text-muted">
            {t("research.workflow.promptVersion", {
              defaultValue: "Prompt version",
            })}
          </dt>
          <dd className="mt-0.5 break-words font-mono text-[10px] text-text">
            {result.promptVersion ??
              t("research.workflow.notReported", {
                defaultValue: "Not reported (legacy snapshot)",
              })}
          </dd>
        </div>
      </dl>
    </div>
  );
}

function ConnectionBadge({ connection }: { connection: WorkflowConnectionState }) {
  const { t } = useTranslation("pages");
  if (connection === "idle" || connection === "live") return null;
  return (
    <span className="inline-flex items-center gap-1 text-warn">
      <Loader2 size={10} className="animate-spin" />
      {t(`research.workflow.connection.${connection}`, {
        defaultValue: connection === "reconnecting" ? "reconnecting" : "refreshing",
      })}
    </span>
  );
}

function WorkflowWaiting({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center gap-2 rounded-card border border-border bg-surface py-14 text-sm text-muted">
      <Loader2 size={15} className="animate-spin" />
      {label}
    </div>
  );
}

function WorkflowError({ message, onRefresh }: { message: string; onRefresh: () => Promise<void> }) {
  const { t } = useTranslation("pages");
  return (
    <div className="flex items-start gap-3 rounded-card border border-error/30 bg-error/5 p-4 text-sm">
      <AlertTriangle size={16} className="mt-0.5 shrink-0 text-error" />
      <p className="min-w-0 flex-1 break-words text-xs text-muted">{message}</p>
      <button type="button" onClick={() => void onRefresh()} className="flex items-center gap-1 text-xs text-link hover:underline">
        <RefreshCw size={12} />
        {t("research.retry", { defaultValue: "Retry" })}
      </button>
    </div>
  );
}

export function WorkflowReviewSummary({ review }: { review: ResearchWorkflowReview | null }) {
  const { t } = useTranslation("pages");
  if (!review) {
    return (
      <p className="px-4 py-6 text-center text-xs text-muted">
        {t("research.workflow.noReview", { defaultValue: "No review has been recorded yet." })}
      </p>
    );
  }
  const frozenReview = review.result.schemaVersion === "2";
  return (
    <div className="space-y-3 p-4">
      <div className="flex items-center gap-2">
        <ShieldCheck size={15} className={review.verdict === "passed" ? "text-ok" : "text-warn"} />
        <div>
          <p className="text-[10px] uppercase tracking-wider text-muted">
            {review.reviewType.startsWith("deterministic-claims-")
              ? frozenReview
                ? t("research.workflow.deterministicReview", {
                    defaultValue: "Deterministic frozen-result integrity review",
                  })
                : t("research.workflow.legacyDeterministicReview", {
                    defaultValue: "Legacy deterministic review — result not frozen",
                  })
              : review.reviewType}
          </p>
          <p className="text-sm font-medium text-text">
            {t(`research.reviewVerdict.${review.verdict}`, {
              defaultValue: statusLabel(review.verdict),
            })}
          </p>
        </div>
      </div>
      <p className="text-[11px] leading-relaxed text-muted">
        {t("research.workflow.reviewBoundary", {
          defaultValue:
            "This deterministic review validates citation linkage and evidence integrity. It does not establish the scientific correctness, methodological quality, or generalizability of a conclusion, and it does not treat a model confidence score as evidence strength.",
        })}
      </p>
      {frozenReview && review.result.resultSnapshotSha256 ? (
        <p className="break-all rounded-input border border-ok/20 bg-ok/5 px-3 py-2 font-mono text-[9px] text-ok">
          {t("research.workflow.resultSnapshotHash", {
            defaultValue: "Frozen result SHA-256",
          })}: {review.result.resultSnapshotSha256}
        </p>
      ) : (
        <p className="rounded-input border border-warn/25 bg-warn/5 px-3 py-2 text-[10px] leading-relaxed text-warn">
          {t("research.workflow.legacyReviewBoundary", {
            defaultValue:
              "This historical review has no immutable result snapshot. Its live result remains available for compatibility but does not carry the v2 frozen-result guarantee.",
          })}
        </p>
      )}
      <ul className="space-y-2">
        {review.result.checks.map((check, index) => (
          <li key={`${check.code}:${check.claimId ?? "workflow"}:${check.evidenceId ?? index}`} className="rounded-input border border-border bg-bg px-3 py-2.5">
            <div className="flex items-start gap-2">
              {check.status === "passed" ? (
                <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-ok" />
              ) : (
                <XCircle size={13} className="mt-0.5 shrink-0 text-error" />
              )}
              <p className="text-xs leading-relaxed text-muted">{check.message}</p>
            </div>
          </li>
        ))}
      </ul>
      {review.result.requiredRevisions.length > 0 && (
        <div className="rounded-input border border-warn/25 bg-warn/5 px-3 py-2.5">
          <p className="text-[10px] font-medium uppercase tracking-wider text-muted">
            {t("research.workflow.requiredRevisions", { defaultValue: "Required revisions" })}
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-relaxed text-muted">
            {review.result.requiredRevisions.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}
