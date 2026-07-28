import { useTranslation } from "react-i18next";
import {
  Loader2,
  ShieldCheck,
} from "lucide-react";
import type {
  DatasetAnalysisPlanStep,
  PaperDiscoveryPlanStep,
  ResearchSource,
  ResearchWorkflowAllowedAction,
  ResearchWorkflowSnapshot,
  ResearchWorkflowStepSpec,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";
import { generationModeForSnapshot } from "./workflowModel";

function discoveryProviderName(provider: string): string {
  if (provider === "arxiv") return "arXiv";
  if (provider === "crossref") return "Crossref";
  if (provider === "openalex") return "OpenAlex";
  if (provider === "pubmed") return "PubMed";
  return provider;
}

function WorkflowPlanStepDetails({
  step,
  sources,
}: {
  step: ResearchWorkflowStepSpec | DatasetAnalysisPlanStep | PaperDiscoveryPlanStep;
  sources: ResearchSource[];
}) {
  const { t } = useTranslation("pages");
  if ("taskType" in step) {
    return <div className="mt-2 border-t border-border-faint pt-3"><dl className="workflow-plan-fields grid gap-x-4 gap-y-3 text-xs">{[[t("research.workflow.discoveryProvider"), step.inputs.provider], [t("research.workflow.discoveryQuery"), step.inputs.query], [t("research.workflow.discoveryYears"), `${step.inputs.yearFrom ?? t("research.workflow.discoveryAnyYear")} – ${step.inputs.yearTo ?? t("research.workflow.discoveryAnyYear")}`], [t("research.workflow.discoverySort"), step.inputs.sort], [t("research.workflow.discoveryResultBudget"), String(step.inputs.maxResultsPerProvider)], [t("research.workflow.discoveryStopPolicy"), t("research.workflow.discoveryStopPolicyValue", { attempts: step.inputs.stopPolicy.maxAttempts, target: step.inputs.stopPolicy.minUniqueCandidates, novelty: step.inputs.stopPolicy.maxConsecutiveNoNovelty })], [t("research.workflow.discoveryPdfDownload"), t("research.workflow.discoveryPdfDownloadOff")]].map(([label, value]) => <div key={label} className="min-w-0"><dt className="text-xs font-medium text-muted">{label}</dt><dd className="mt-1 break-words text-text">{value}</dd></div>)}</dl><div className="mt-3"><p className="text-xs font-medium text-muted">{t("research.workflow.acceptanceCriteria", { defaultValue: "Acceptance criteria" })}</p><ul className="mt-1 divide-y divide-border-faint border-y border-border-faint">{step.acceptanceCriteria.map((criterion) => <li key={criterion} className="flex items-start gap-2 py-2 text-xs text-text"><span aria-hidden="true" className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />{criterion}</li>)}</ul></div></div>;
  }
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
    <div className="mt-2 space-y-3 border-t border-border-faint pt-3">
      <dl className="workflow-plan-fields grid gap-x-4 gap-y-3 text-xs">
        {fields.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-xs font-medium text-muted">
              {label}
            </dt>
            <dd className="mt-1 break-words text-text">{value}</dd>
          </div>
        ))}
      </dl>
      {approvedSources.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted">
            {hasFrozenSources
              ? t("research.workflow.frozenSources", {
                  defaultValue: "Content-bound sources",
                })
              : t("research.workflow.legacySources", {
                  defaultValue: "Legacy allowlisted source IDs",
                })}
          </p>
          <ul className="mt-1 divide-y divide-border-faint">
            {approvedSources.map((source) => (
              <li
                key={source.sourceId}
                className="py-2 text-xs text-text"
              >
                <p className="break-words">
                  {source.title}{" "}
                  <code className="text-caption text-muted">
                    ({source.sourceId})
                  </code>
                </p>
                {source.contentHash && (
                  <p className="mt-1 break-all font-mono text-caption text-muted">
                    {t("research.workflow.fileHash", {
                      defaultValue: "File SHA-256",
                    })}
                    : {source.contentHash}
                  </p>
                )}
                {source.pageManifestHash && (
                  <p className="mt-1 break-all font-mono text-caption text-muted">
                    {t("research.workflow.pageManifestHash", {
                      defaultValue: "Parsed-page manifest SHA-256",
                    })}
                    : {source.pageManifestHash}
                  </p>
                )}
                {source.legacyAllowlist && (
                  <p className="mt-1 text-xs leading-relaxed text-warn">
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
        <p className="text-xs font-medium text-muted">
          {t("research.workflow.acceptanceCriteria", {
            defaultValue: "Acceptance criteria",
          })}
        </p>
        <ul className="mt-1 divide-y divide-border-faint border-y border-border-faint">
          {step.acceptanceCriteria.map((criterion) => (
            <li key={criterion} className="flex items-start gap-2 py-2 text-xs text-text">
              <span aria-hidden="true" className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
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
  const discoveryPlan = plan.spec.planType === "paper-discovery" ? plan.spec : null;
  const discoveryProviderLabel = discoveryPlan
    ? [...new Set(discoveryPlan.steps.map((step) => step.inputs.provider))]
        .map(discoveryProviderName)
        .join(" + ")
    : "";
  const datasetSource = datasetPlan
    ? sources.find((source) => source.id === datasetPlan.datasetSourceId) ?? null
    : null;
  const approvalAuditItems: Array<{
    label: string;
    value: string;
    detail: string;
    tone?: "warning";
  }> = [
    {
      label: t("research.workflow.executionBoundaryLabel", {
        defaultValue: "Execution",
      }),
      value: t("research.workflow.registeredPlanSteps", {
        defaultValue: "{{count}} registered plan step(s)",
        count: plan.spec.steps.length,
      }),
      detail: datasetPlan
        ? t("research.workflow.datasetExecutionBoundary", {
            defaultValue:
              "Plan approval starts the listed steps. The exact Python payload requires a separate approval.",
          })
        : t("research.workflow.researchExecutionBoundary", {
            defaultValue:
              "Approval applies only to this immutable plan version and its listed steps.",
          }),
    },
    {
      label: t("research.workflow.dataScopeLabel", {
        defaultValue: "Data scope",
      }),
      value: datasetPlan
        ? (datasetSource?.title ?? datasetPlan.datasetSourceId)
        : t("research.workflow.projectSourceCount", {
            defaultValue: "{{count}} project source(s)",
            count: sources.length,
          }),
      detail: datasetPlan
        ? t("research.workflow.contentBoundDataset", {
            defaultValue: "One content-bound CSV identified by SHA-256.",
          })
        : remoteAssisted
          ? t("research.workflow.verifiedPassageScope", {
              defaultValue:
                "Only verified passages named in the approval may leave the workspace.",
            })
          : t("research.workflow.indexedPdfScope", {
              defaultValue: "Indexed project PDFs remain inside the workspace.",
            }),
    },
    {
      label: t("research.workflow.permissionsLabel", {
        defaultValue: "Permissions",
      }),
      value: remoteAssisted
        ? t("research.workflow.verifiedPassageTransfer", {
            defaultValue: "Verified passage transfer",
          })
        : datasetPlan
          ? t("research.workflow.localDatasetRead", {
              defaultValue: "Local dataset read",
            })
          : t("research.workflow.localIndexRead", {
              defaultValue: "Local index read",
            }),
      detail: remoteAssisted
        ? t("research.workflow.remotePermissionExclusions", {
            defaultValue:
              "No code, installs, deletion, unrelated network access, or future plan versions.",
          })
        : datasetPlan
          ? t("research.workflow.datasetPermissionExclusions", {
              defaultValue:
                "No Python execution, installs, deletion, or remote connection is approved here.",
            })
          : t("research.workflow.localPermissionExclusions", {
              defaultValue:
                "No code execution, installs, deletion, or remote connection.",
            }),
    },
    {
      label: t("research.workflow.riskLabel", {
        defaultValue: "Risk",
      }),
      value: approval
        ? t(`research.workflow.risk${approval.riskLevel === "low" ? "Low" : approval.riskLevel === "medium" ? "Medium" : "High"}`, {
            defaultValue: `${approval.riskLevel} risk`,
          })
        : t("research.workflow.notReported", {
            defaultValue: "Not reported (legacy snapshot)",
          }),
      detail: approval
        ? t("research.workflow.affectedResourceCount", {
            defaultValue: "{{count}} affected resource(s) in the approval envelope.",
            count: approval.affectedResources.length,
          })
        : t("research.workflow.missingApprovalEnvelope", {
            defaultValue:
              "The approval envelope is unavailable, so this plan cannot be approved.",
          }),
      tone: remoteAssisted || !approval ? "warning" : undefined,
    },
  ];

  if (discoveryPlan) {
    approvalAuditItems.splice(0, approvalAuditItems.length,
      {
        label: t("research.workflow.executionBoundaryLabel"),
        value: t("research.workflow.discoveryOperationCount", {
          count: discoveryPlan.steps.length,
          providers: discoveryProviderLabel,
        }),
        detail: t("research.workflow.discoveryExecutionDetail"),
      },
      {
        label: t("research.workflow.discoveryPublicQueryScope"),
        value: [...new Set(discoveryPlan.steps.map((step) => step.inputs.query))].join(" · "),
        detail: t("research.workflow.discoveryQueryScopeDetail"),
      },
      {
        label: t("research.workflow.permissionsLabel"),
        value: t("research.workflow.discoveryPermission", {
          providers: discoveryProviderLabel,
        }),
        detail: t("research.workflow.discoveryPermissionDetail"),
        tone: "warning",
      },
      {
        label: t("research.workflow.riskLabel", { defaultValue: "Risk" }),
        value: approval ? t(`research.workflow.risk${approval.riskLevel === "low" ? "Low" : approval.riskLevel === "medium" ? "Medium" : "High"}`) : t("research.workflow.notReported", { defaultValue: "Not reported (legacy snapshot)" }),
        detail: approval ? t("research.workflow.affectedResourceCount", { count: approval.affectedResources.length }) : t("research.workflow.missingApprovalEnvelope", { defaultValue: "The approval envelope is unavailable, so this plan cannot be approved." }),
        tone: "warning",
      },
    );
  }

  if (datasetPlan) {
    approvalAuditItems.push({
      label: t("research.workflow.pythonPayloadLabel", {
        defaultValue: "Python payload",
      }),
      value: t("research.workflow.pythonPayloadSeparateApproval", {
        defaultValue: "Separate approval required",
      }),
      detail: t("research.workflow.pythonPayloadPlanBoundary", {
        defaultValue:
          "Plan approval may prepare code, but the exact payload remains blocked until a separate runtime approval.",
      }),
      tone: "warning",
    });
  }

  return (
    <section className="research-plan-container border-y border-border bg-surface">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border-faint px-4 py-3">
        <h3 className="text-base font-semibold leading-6 text-text">
          {discoveryPlan
            ? t("research.workflow.discoveryPlanHeading")
            : datasetPlan
            ? t("research.workflow.planHeadingDataset", {
                defaultValue: "Review the dataset analysis plan",
              })
            : t("research.workflow.planHeadingResearch", {
                defaultValue: "Review the research plan",
              })}
        </h3>
        <span className="ml-auto inline-flex items-center gap-1.5 text-caption font-medium text-warn">
          <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-warn" />
          {t("research.workflow.approvalRequired", {
            defaultValue: "Approval required",
          })}
        </span>
        <span className="border-l border-border pl-3 text-caption text-muted">
          {t("research.workflow.planVersion", {
            defaultValue: "Plan v{{version}}",
            version: plan.version,
          })}
        </span>
      </div>
      <div className="p-4 pb-0">
        <section aria-labelledby="approval-boundary-heading">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <h4 id="approval-boundary-heading" className="text-sm font-semibold text-text">
              {t("research.workflow.approvalBoundaryHeading", {
                defaultValue: "Approval boundary",
              })}
            </h4>
            <p className="text-xs text-muted">
              {t("research.workflow.approvalBoundaryPrompt", {
                defaultValue:
                  "Confirm what this plan may do. Dataset code remains separately approval-gated.",
              })}
            </p>
          </div>
          <dl className="mt-3 divide-y divide-border-faint border-y border-border-faint">
            {approvalAuditItems.map((item) => (
              <ApprovalScopeRow key={item.label} {...item} />
            ))}
          </dl>
        </section>

        {datasetPlan &&
          (datasetPlan.assumptions.length > 0 || datasetPlan.questionsForUser.length > 0) && (
            <div className="research-plan-summary-grid mt-4 grid gap-4 border-t border-border-faint pt-4 text-xs">
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

        <h4 className="mb-1 mt-5 text-sm font-semibold text-text">
          {t("research.workflow.plannedStepsHeading", {
            defaultValue: "Planned steps",
          })}
        </h4>
        <ol className="divide-y divide-border-faint">
          {plan.spec.steps.map((step, index) => (
            <li key={step.key} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center text-caption font-semibold tabular-nums text-muted">
                {index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="min-w-0 break-words text-sm font-medium text-text">
                    {"taskType" in step
                      ? t("research.workflow.discoveryStepObjective", {
                          provider: discoveryProviderName(step.inputs.provider),
                          query: step.inputs.query,
                        })
                      : step.objective}
                  </p>
                  <code className="break-all rounded-input bg-surface-2 px-2 py-0.5 text-caption text-muted">
                    {"taskType" in step
                      ? t("research.workflow.discoveryTaskLabel")
                      : step.type}
                  </code>
                </div>
                <p className="mt-1 break-words text-xs leading-relaxed text-muted">
                  {t("research.workflow.expectedOutputs", {
                    defaultValue: "Outputs: {{outputs}}",
                    outputs: (
                      "taskType" in step
                        ? [t("research.workflow.discoveryExpectedOutput")]
                        : "expectedOutputs" in step
                          ? step.expectedOutputs
                          : step.expectedArtifacts
                    ).join(" · "),
                  })}
                </p>
                <details className="mt-2">
                  <summary className="flex min-h-11 cursor-pointer items-center rounded-input text-xs font-medium text-link hover:underline">
                    {t("research.workflow.stepDetails", {
                      defaultValue: "Step details",
                    })}
                  </summary>
                  <WorkflowPlanStepDetails step={step} sources={sources} />
                </details>
              </div>
            </li>
          ))}
        </ol>

        <details className="mt-5 border-t border-border-faint">
          <summary className="flex min-h-11 cursor-pointer items-center rounded-input text-xs font-medium text-link hover:underline">
            {t("research.workflow.auditDetails", { defaultValue: "Audit details" })}
          </summary>
          <div className="space-y-4 pb-4">
            <dl className="research-plan-metadata-grid grid gap-x-5 gap-y-4 text-xs">
          <div className="min-w-0">
            <dt className="text-xs font-medium text-muted">
              {t("research.workflow.planGenerator", {
                defaultValue: "Plan generator",
              })}
            </dt>
            <dd className="mt-1 break-words text-text">
              {plan.generator ??
                t("research.workflow.notReported", {
                  defaultValue: "Not reported (legacy snapshot)",
                })}
            </dd>
          </div>
          <div className="min-w-0">
            <dt className="text-xs font-medium text-muted">
              {t("research.workflow.promptVersion", {
                defaultValue: "Prompt version",
              })}
            </dt>
            <dd className="mt-1 break-words text-text">
              {plan.promptVersion ??
                t("research.workflow.notReported", {
                  defaultValue: "Not reported (legacy snapshot)",
                })}
            </dd>
          </div>
          <div className="min-w-0">
            <dt className="text-xs font-medium text-muted">
              {t("research.workflow.model", { defaultValue: "Model" })}
            </dt>
            <dd className="mt-1 break-words text-text">
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
            <dt className="text-xs font-medium text-muted">
              {t("research.workflow.planHash", {
                defaultValue: "Immutable plan hash",
              })}
            </dt>
            <dd className="mt-1 break-all font-mono text-caption text-text">
              {plan.planSha256}
            </dd>
          </div>
            </dl>

            {approval && (
              <section className="border-t border-border-faint pt-4" aria-label={t("research.workflow.canonicalApprovalEnvelope")}>
                <p className="text-xs font-medium text-muted">
                  {t("research.workflow.canonicalApprovalEnvelope", {
                    defaultValue: "Canonical approval envelope",
                  })}
                </p>
                <dl className="research-plan-metadata-grid mt-3 grid gap-x-5 gap-y-4 text-xs">
                  <MetadataItem label={t("research.workflow.reasonLabel")} value={approval.reason} />
                  {"approvalSchemaVersion" in approval && (
                    <MetadataItem label={t("research.workflow.approvalSchemaLabel")} value={approval.approvalSchemaVersion} mono />
                  )}
                  {"expectedWorkflowRevision" in approval && (
                    <MetadataItem label={t("research.workflow.workflowRevisionLabel")} value={String(approval.expectedWorkflowRevision)} />
                  )}
                  {"planVersion" in approval && (
                    <MetadataItem label={t("research.workflow.planRevisionLabel")} value={`v${approval.planVersion}`} />
                  )}
                  {(!remoteAssisted || discoveryPlan) && (
                    <div className="min-w-0">
                      <dt className="text-xs font-medium text-muted">{t("research.workflow.affectedResourcesLabel")}</dt>
                      <dd className="mt-1 space-y-1">
                        {approval.affectedResources.map((resource) => (
                          <code key={resource} className="block break-all font-mono text-caption text-text">
                            {resource}
                          </code>
                        ))}
                      </dd>
                    </div>
                  )}
                </dl>
              </section>
            )}

            <div
          className={cn(
            "flex items-start gap-2 border-t border-border-faint pt-4 text-xs leading-relaxed text-muted",
            remoteAssisted || discoveryPlan
              ? "text-warn"
              : "text-muted",
          )}
        >
          <ShieldCheck
            size={15}
            className={cn(
              "mt-0.5 shrink-0",
              remoteAssisted || discoveryPlan ? "text-warn" : "text-ok",
            )}
          />
          <span>
            <strong className="font-medium text-text">
              {discoveryPlan
                ? t("research.workflow.discoveryPublicSearch", {
                    providers: discoveryProviderLabel,
                  })
                : remoteAssisted
                ? t("research.workflow.remoteAssisted", {
                    defaultValue: "Remote model-assisted.",
                  })
                : t("research.workflow.localOnly", {
                    defaultValue: "Local only.",
                  })}
            </strong>{" "}
            {discoveryPlan
              ? t("research.workflow.discoveryPublicSearchDetail")
              : remoteAssisted
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
              <div className="space-y-1 text-xs leading-relaxed text-muted">
                <p>{approval.reason}</p>
                <p className="break-all font-mono text-caption">
                  {t("research.workflow.approvalPayloadHash", {
                    defaultValue: "Approval payload SHA-256",
                  })}
                  : {approval.payloadSha256}
                </p>
              </div>
            )}

            {(remoteAssisted || discoveryPlan) && approval && (
              <div className="border-t border-border-faint pt-4">
                <p className="text-xs font-medium text-muted">
                  {t("research.workflow.remoteApprovalScope", {
                    defaultValue: "Remote approval scope",
                  })}
                </p>
                <ul className="mt-2 space-y-1">
                  {approval.affectedResources.map((resource) => (
                    <li key={resource}>
                      <code className="break-all text-caption text-text">
                        {resource}
                      </code>
                    </li>
                  ))}
                </ul>
                <p className="mt-2 text-caption leading-relaxed text-muted">
                  {discoveryPlan
                    ? t("research.workflow.discoveryResourceBoundary", {
                        providers: discoveryProviderLabel,
                      })
                    : t("research.workflow.remoteResourceBoundary", {
                        defaultValue:
                          "Only entries marked verified-passages:remote authorize data transfer. The project entry identifies the local workflow scope.",
                      })}
                </p>
              </div>
            )}

          </div>
        </details>

        <div className="-mx-4 mt-5 flex min-h-[68px] flex-wrap items-center gap-3 border-t border-border bg-surface px-4 py-3">
          <p id="plan-approval-consequence" className="mr-auto max-w-xl text-xs leading-relaxed text-muted">
              {discoveryPlan
                ? t("research.workflow.discoveryPlanApprovalConsequence", {
                    providers: discoveryProviderLabel,
                  })
                : datasetPlan
              ? t("research.workflow.planApprovalDoesNotExecutePython", {
                  defaultValue: "Approving this plan does not approve or execute Python.",
                })
              : t("research.workflow.planApprovalImmutableScope", {
                  defaultValue: "Approval applies only to this immutable plan version.",
                })}
          </p>
          {allowedActions.includes("cancel") && (
            <button
              type="button"
              onClick={() => void onCancel()}
              disabled={mutating}
              aria-describedby="plan-approval-consequence"
              className="min-h-11 rounded-input border border-border px-4 py-2 text-xs text-text hover:bg-surface-2 disabled:opacity-40"
            >
              {t("research.workflow.cancelPlan", { defaultValue: "Cancel task" })}
            </button>
          )}
          {allowedActions.includes("approve-plan") && approval && (
            <button
              type="button"
              onClick={() => void onApprove()}
              disabled={mutating}
              aria-describedby="plan-approval-consequence"
              className="flex min-h-11 items-center gap-1.5 rounded-input bg-accent px-4 py-2 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-40"
            >
              {mutating && <Loader2 size={13} className="animate-spin" />}
              {t("research.workflow.approveRun", {
                defaultValue: "Approve plan",
              })}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

function MetadataItem({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className={cn("mt-1 break-words text-text", mono && "break-all font-mono text-caption")}>
        {value}
      </dd>
    </div>
  );
}

function PlanNotes({ label, items }: { label: string; items: string[] }) {
  const { t } = useTranslation("pages");
  return (
    <div>
      <p className="text-xs font-medium text-muted">
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

function ApprovalScopeRow({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  tone?: "warning";
}) {
  return (
    <div className="approval-scope-row grid min-w-0 gap-x-4 gap-y-1 py-3">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd
        className={cn(
          "min-w-0 break-words text-xs font-medium text-text",
          tone === "warning" && "text-warn",
        )}
      >
        {value}
      </dd>
      <dd className="min-w-0 text-xs leading-relaxed text-muted">
        {detail}
      </dd>
    </div>
  );
}
