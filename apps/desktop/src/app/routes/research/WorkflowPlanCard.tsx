import { useTranslation } from "react-i18next";
import { FileSearch, Loader2, Play, ShieldCheck } from "lucide-react";
import type {
  DatasetAnalysisPlanStep,
  ResearchSource,
  ResearchWorkflowAllowedAction,
  ResearchWorkflowSnapshot,
  ResearchWorkflowStepSpec,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";
import { generationModeForSnapshot } from "./workflowModel";

function WorkflowPlanStepDetails({
  step,
  sources,
}: {
  step: ResearchWorkflowStepSpec | DatasetAnalysisPlanStep;
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
            t("research.workflow.unknownSource", {
              defaultValue: "Unknown source",
            }),
          contentHash: null,
          pageManifestHash: null,
          legacyAllowlist: true,
        })) ?? [])
      : [];
  const fields: Array<[string, string]> =
    "dependencies" in step
      ? [
          [
            t("research.workflow.dependencies", {
              defaultValue: "Dependencies",
            }),
            step.dependencies.length > 0
              ? step.dependencies.join(" · ")
              : t("research.workflow.noDependencies", {
                  defaultValue: "None",
                }),
          ],
          [
            t("research.workflow.riskLevelLabel", {
              defaultValue: "Risk level",
            }),
            step.riskLevel,
          ],
        ]
      : "sourceKind" in step.inputs
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
            [
              t("research.workflow.searchQuery", {
                defaultValue: "Search query",
              }),
              step.inputs.query,
            ],
            [
              t("research.workflow.maxPassages", {
                defaultValue: "Maximum passages",
              }),
              String(step.inputs.maxPassages),
            ],
            [
              t("research.workflow.maxPerSource", {
                defaultValue: "Maximum per source",
              }),
              String(step.inputs.maxPerSource),
            ],
          ]
          : [
            [
              t("research.workflow.maxClaims", {
                defaultValue: "Maximum claims",
              }),
              String(step.inputs.maxClaims),
            ],
          ];

  if ("dependencies" in step) {
    if ("datasetSourceId" in step.inputs) {
      fields.push(["Dataset source ID", step.inputs.datasetSourceId]);
      fields.push(["Dataset SHA-256", step.inputs.datasetContentHash]);
    }
    if ("samplingMethod" in step.inputs) {
      fields.push(["Sampling", step.inputs.samplingMethod]);
      fields.push(["Maximum sampled rows", String(step.inputs.maxSampleRows)]);
    }
    if ("profileStepKey" in step.inputs) {
      fields.push(["Profile dependency", step.inputs.profileStepKey]);
    }
    if ("preparationStepKey" in step.inputs) {
      fields.push(["Preparation dependency", step.inputs.preparationStepKey]);
    }
    if ("executionStepKey" in step.inputs) {
      fields.push(["Execution dependency", step.inputs.executionStepKey]);
    }
    if ("expectedOutputs" in step.inputs) {
      fields.push(["Bound runtime outputs", step.inputs.expectedOutputs.join(" · ")]);
    }
    if ("timeoutSeconds" in step.inputs) {
      fields.push(["Runtime timeout", `${step.inputs.timeoutSeconds} seconds`]);
    }
  }

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
                  <code className="text-[10px] text-muted">
                    ({source.sourceId})
                  </code>
                </p>
                {source.contentHash && (
                  <p className="mt-1 break-all font-mono text-[9px] text-muted">
                    {t("research.workflow.fileHash", {
                      defaultValue: "File SHA-256",
                    })}
                    : {source.contentHash}
                  </p>
                )}
                {source.pageManifestHash && (
                  <p className="mt-1 break-all font-mono text-[9px] text-muted">
                    {t("research.workflow.pageManifestHash", {
                      defaultValue: "Parsed-page manifest SHA-256",
                    })}
                    : {source.pageManifestHash}
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

interface WorkflowPlanCardProps {
  snapshot: ResearchWorkflowSnapshot;
  sources: ResearchSource[];
  mutating: boolean;
  onApprove: () => Promise<void>;
  onCancel: () => Promise<void>;
}

export function WorkflowPlanCard({
  snapshot,
  sources,
  mutating,
  onApprove,
  onCancel,
}: WorkflowPlanCardProps) {
  const { t } = useTranslation("pages");
  const { plan } = snapshot;
  const allowedActions: readonly ResearchWorkflowAllowedAction[] =
    snapshot.allowedActions;
  if (!plan) return null;
  const approval = snapshot.pendingApprovals.find(
    (item) => item.kind === "plan" && item.planId === plan.id,
  );
  const generationMode = generationModeForSnapshot(snapshot);
  const remoteAssisted = generationMode === "remote-model-assisted";
  const datasetPlan =
    snapshot.workflow.workflowType === "dataset-analysis" &&
    "workflowType" in plan.spec &&
    plan.spec.workflowType === "dataset-analysis"
      ? plan.spec
      : null;
  const datasetSource = datasetPlan
    ? sources.find((source) => source.id === datasetPlan.datasetSourceId) ?? null
    : null;

  return (
    <section className="overflow-hidden rounded-card border border-accent/30 bg-surface shadow-card">
      <div className="flex items-center gap-2 border-b border-accent/20 bg-accent/5 px-4 py-3">
        <FileSearch size={16} className="text-accent" />
        <h3 className="text-sm font-medium text-text">
          {t("research.workflow.planHeading", {
            defaultValue: datasetPlan
              ? "Review the dataset analysis plan"
              : "Review the research plan",
          })}
        </h3>
        <span className="ml-auto rounded-full bg-surface px-2 py-0.5 text-[10px] text-muted ring-1 ring-border">
          {t("research.workflow.planVersion", {
            defaultValue: "Plan v{{version}}",
            version: plan.version,
          })}
        </span>
      </div>
      <div className="p-4">
        {datasetPlan && (
          <div className="mb-4 space-y-2 rounded-input border border-accent/20 bg-accent/5 p-3 text-[11px]">
            <div className="grid gap-2 sm:grid-cols-2">
              <div>
                <p className="text-[9px] font-medium uppercase tracking-wider text-muted">
                  {t("research.workflow.datasetLabel", {
                    defaultValue: "Dataset",
                  })}
                </p>
                <p className="mt-0.5 text-text">
                  {datasetSource?.title ?? datasetPlan.datasetSourceId}
                </p>
                <p className="mt-1 break-all font-mono text-[9px] text-muted">
                  {datasetPlan.datasetSourceId}
                </p>
              </div>
              <div>
                <p className="text-[9px] font-medium uppercase tracking-wider text-muted">
                  {t("research.workflow.immutableDatasetHash", {
                    defaultValue: "Immutable dataset SHA-256",
                  })}
                </p>
                <p className="mt-0.5 break-all font-mono text-[9px] text-text">
                  {datasetPlan.datasetContentHash}
                </p>
              </div>
            </div>
            {(datasetPlan.assumptions.length > 0 ||
              datasetPlan.questionsForUser.length > 0) && (
              <div className="grid gap-2 border-t border-accent/15 pt-2 sm:grid-cols-2">
                <PlanNotes
                  label={t("research.workflow.planAssumptions", {
                    defaultValue: "Assumptions",
                  })}
                  items={datasetPlan.assumptions}
                />
                <PlanNotes
                  label={t("research.workflow.planQuestions", {
                    defaultValue: "Questions for user",
                  })}
                  items={datasetPlan.questionsForUser}
                />
              </div>
            )}
          </div>
        )}
        <ol className="space-y-3">
          {plan.spec.steps.map((step, index) => (
            <li key={step.key} className="flex items-start gap-3">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface-2 text-[10px] font-semibold text-muted ring-1 ring-border">
                {index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-medium text-text">
                    {step.objective}
                  </p>
                  <code className="rounded-full bg-surface-2 px-2 py-0.5 text-[9px] text-muted ring-1 ring-border">
                    {step.type}
                  </code>
                </div>
                <p className="mt-1 text-[11px] text-muted">
                  {t("research.workflow.expectedOutputs", {
                    defaultValue: "Outputs: {{outputs}}",
                    outputs: (
                      "expectedOutputs" in step
                        ? step.expectedOutputs
                        : step.expectedArtifacts
                    ).join(" · "),
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
              {t("research.workflow.planHash", {
                defaultValue: "Immutable plan hash",
              })}
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
                : t("research.workflow.localOnly", {
                    defaultValue: "Local only.",
                  })}
            </strong>{" "}
            {remoteAssisted
              ? t("research.workflow.remoteAssistedDetail", {
                  defaultValue:
                    "The research goal was sent under the approval given when this task was created. Approving this plan permits only verified passages represented by the affected resources below to be sent to the configured model provider, for this plan version. It does not authorize code execution, dependency installation, unrelated network access, or a future plan version.",
                })
              : datasetPlan
                ? t("research.workflow.localDatasetPlanDetail", {
                    defaultValue:
                      "This plan profiles the displayed immutable dataset locally. Plan approval starts only the registered workflow steps; Python execution still requires a separate exact-payload approval.",
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
              })}
              : {approval.payloadSha256}
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
                  <code className="break-all text-[10px] text-text">
                    {resource}
                  </code>
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
              {mutating ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Play size={13} />
              )}
              {t("research.workflow.approveRun", {
                defaultValue: datasetPlan ? "Approve plan" : "Approve & run",
              })}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

function PlanNotes({ label, items }: { label: string; items: string[] }) {
  const { t } = useTranslation("pages");
  return (
    <div>
      <p className="text-[9px] font-medium uppercase tracking-wider text-muted">
        {label}
      </p>
      {items.length > 0 ? (
        <ul className="mt-1 list-disc space-y-0.5 pl-4 text-muted">
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-muted">
          {t("research.workflow.noPlanNotes", { defaultValue: "None" })}
        </p>
      )}
    </div>
  );
}
