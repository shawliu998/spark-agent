import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  BarChart3,
  ChevronRight,
  FileText,
  Loader2,
  Sparkles,
  Upload,
} from "lucide-react";
import type {
  ResearchGenerationMode,
  ResearchSource,
  ResearchWorkflowType,
  ScienceCoreModelDestination,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";
import type { ResearchWorkflowCreateOptions } from "./useResearchWorkflow";

const LITERATURE_WORKFLOW: ResearchWorkflowType = "literature-synthesis";
const DATASET_WORKFLOW: ResearchWorkflowType = "dataset-analysis";

interface WorkflowGoalComposerProps {
  canStart: boolean;
  busy: boolean;
  sources: ResearchSource[];
  importingDataset: boolean;
  remoteDestination: ScienceCoreModelDestination | null;
  onCreate: (
    goal: string,
    options: ResearchWorkflowCreateOptions,
  ) => Promise<void>;
  onImportDataset: (event: React.ChangeEvent<HTMLInputElement>) => void;
}

export function WorkflowGoalComposer({
  canStart,
  busy,
  sources,
  importingDataset,
  remoteDestination,
  onCreate,
  onImportDataset,
}: WorkflowGoalComposerProps) {
  const { t } = useTranslation("pages");
  const datasetInput = useRef<HTMLInputElement>(null);
  const [goal, setGoal] = useState("");
  const [workflowType, setWorkflowType] =
    useState<ResearchWorkflowType>("literature-synthesis");
  const [datasetSourceId, setDatasetSourceId] = useState<string | null>(null);
  const [generationMode, setGenerationMode] =
    useState<ResearchGenerationMode>("local-deterministic");
  const [remoteDataApproved, setRemoteDataApproved] = useState(false);

  const readyPdfs = useMemo(
    () =>
      sources.filter(
        (source) =>
          source.sourceKind === "pdf" && source.ingestionStatus === "ready",
      ),
    [sources],
  );
  const readyDatasets = useMemo(
    () =>
      sources.filter(
        (source) =>
          source.sourceKind === "dataset" &&
          source.ingestionStatus === "ready",
      ),
    [sources],
  );
  const selectedDataset =
    readyDatasets.find((source) => source.id === datasetSourceId) ?? null;
  const remoteAssisted =
    workflowType === "literature-synthesis" &&
    generationMode === "remote-model-assisted";
  const remoteDestinationApprovalKey = remoteDestination
    ? `${remoteDestination.endpointIdentity}:${remoteDestination.model}`
    : null;

  useEffect(() => {
    setDatasetSourceId((current) =>
      readyDatasets.some((source) => source.id === current)
        ? current
        : (readyDatasets[0]?.id ?? null),
    );
  }, [readyDatasets]);

  useEffect(() => {
    setRemoteDataApproved(false);
    if (!remoteDestinationApprovalKey) setGenerationMode("local-deterministic");
  }, [remoteDestinationApprovalKey]);

  useEffect(() => {
    if (workflowType === "dataset-analysis") {
      setGenerationMode("local-deterministic");
      setRemoteDataApproved(false);
    }
  }, [workflowType]);

  const sourceReady =
    workflowType === "dataset-analysis"
      ? selectedDataset !== null
      : readyPdfs.length > 0;
  const canSubmit =
    goal.trim().length > 0 &&
    canStart &&
    sourceReady &&
    !busy &&
    (!remoteAssisted || (remoteDataApproved && remoteDestination != null));

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const next = goal.trim();
    if (!next || !canSubmit) return;
    await onCreate(next, {
      workflowType,
      datasetSourceId:
        workflowType === "dataset-analysis" ? datasetSourceId : null,
      generationMode:
        workflowType === "dataset-analysis"
          ? "local-deterministic"
          : generationMode,
      remoteDataApproved:
        workflowType === "literature-synthesis" &&
        remoteAssisted &&
        remoteDataApproved,
    });
  };

  return (
    <form
      onSubmit={(event) => void submit(event)}
      className="rounded-card border border-border bg-surface p-4 shadow-card"
    >
      <div className="flex items-center gap-2">
        <Sparkles size={15} className="text-accent" />
        <label
          htmlFor="research-workflow-goal"
          className="text-sm font-medium text-text"
        >
          {t("research.workflow.composerLabel", {
            defaultValue: "Give Spark Agent a research goal",
          })}
        </label>
      </div>

      <div
        className="mt-3"
        role="group"
        aria-label={t("research.workflow.typeLabel", {
          defaultValue: "Workflow type",
        })}
      >
        <p className="text-[10px] font-medium uppercase tracking-wider text-muted">
          {t("research.workflow.typeLabel", { defaultValue: "Workflow type" })}
        </p>
        <div className="mt-1.5 grid gap-2 sm:grid-cols-2">
          <WorkflowTypeButton
            selected={workflowType === "literature-synthesis"}
            icon={<FileText size={15} />}
            title={t("research.workflow.typeLiterature", {
              defaultValue: "Literature Synthesis",
            })}
            description={t("research.workflow.typeLiteratureHint", {
              defaultValue: "Build a claim-and-evidence synthesis from ready PDFs.",
            })}
            onClick={() => setWorkflowType(LITERATURE_WORKFLOW)}
          />
          <WorkflowTypeButton
            selected={workflowType === "dataset-analysis"}
            icon={<BarChart3 size={15} />}
            title={t("research.workflow.typeDataset", {
              defaultValue: "Dataset Analysis",
            })}
            description={t("research.workflow.typeDatasetHint", {
              defaultValue:
                "Profile an immutable CSV, approve exact Python, and review its artifacts.",
            })}
            onClick={() => setWorkflowType(DATASET_WORKFLOW)}
          />
        </div>
      </div>

      {workflowType === "dataset-analysis" && (
        <div className="mt-3 rounded-input border border-border bg-bg p-3">
          <div className="flex items-end gap-2">
            <label className="min-w-0 flex-1 text-[10px] font-medium uppercase tracking-wider text-muted">
              {t("research.workflow.datasetLabel", {
                defaultValue: "Ready dataset",
              })}
              <select
                value={datasetSourceId ?? ""}
                onChange={(event) =>
                  setDatasetSourceId(event.target.value || null)
                }
                aria-label={t("research.workflow.datasetSelectAria", {
                  defaultValue: "Dataset",
                })}
                className="mt-1.5 w-full rounded-input border border-border bg-surface px-2.5 py-2 text-xs normal-case tracking-normal text-text outline-none focus:border-accent"
              >
                <option value="">
                  {readyDatasets.length === 0
                    ? t("research.workflow.noReadyDatasets", {
                        defaultValue: "No ready CSV datasets",
                      })
                    : t("research.workflow.chooseDataset", {
                        defaultValue: "Choose a dataset",
                      })}
                </option>
                {readyDatasets.map((dataset) => (
                  <option key={dataset.id} value={dataset.id}>
                    {dataset.title}
                  </option>
                ))}
              </select>
            </label>
            <input
              ref={datasetInput}
              type="file"
              accept=".csv,text/csv"
              onChange={onImportDataset}
              className="hidden"
            />
            <button
              type="button"
              onClick={() => datasetInput.current?.click()}
              disabled={!canStart || importingDataset || busy}
              className="flex shrink-0 items-center gap-1.5 rounded-input border border-border bg-surface px-3 py-2 text-xs text-text hover:bg-surface-2 disabled:opacity-40"
            >
              {importingDataset ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Upload size={13} />
              )}
              {importingDataset
                ? t("research.workflow.importingDataset", {
                    defaultValue: "Importing…",
                  })
                : t("research.workflow.importDataset", {
                    defaultValue: "Import CSV",
                  })}
            </button>
          </div>
          {selectedDataset && (
            <dl className="mt-2 grid gap-2 border-t border-border-faint pt-2 text-[10px] sm:grid-cols-2">
              <div className="min-w-0">
                <dt className="text-muted">
                  {t("research.workflow.datasetSourceId", {
                    defaultValue: "Dataset source ID",
                  })}
                </dt>
                <dd className="break-all font-mono text-text">
                  {selectedDataset.id}
                </dd>
              </div>
              <div className="min-w-0">
                <dt className="text-muted">
                  {t("research.workflow.datasetHash", {
                    defaultValue: "Dataset SHA-256",
                  })}
                </dt>
                <dd className="break-all font-mono text-text">
                  {selectedDataset.contentHash}
                </dd>
              </div>
            </dl>
          )}
          {readyDatasets.length === 0 && (
            <p className="mt-2 text-[10px] leading-relaxed text-warn">
              {t("research.workflow.datasetRequired", {
                defaultValue:
                  "Import a CSV and wait until it is ready before creating a dataset workflow.",
              })}
            </p>
          )}
        </div>
      )}

      <textarea
        id="research-workflow-goal"
        value={goal}
        onChange={(event) => {
          setGoal(event.target.value);
          setRemoteDataApproved(false);
        }}
        rows={4}
        placeholder={
          workflowType === "dataset-analysis"
            ? t("research.workflow.datasetComposerPlaceholder", {
                defaultValue:
                  "Summarize the primary outcome by experimental group and report missingness…",
              })
            : t("research.workflow.composerPlaceholder", {
                defaultValue:
                  "Compare the findings, conflicting evidence, and limitations across these papers…",
              })
        }
        className="mt-3 w-full resize-y rounded-input border border-border bg-bg px-3 py-2.5 text-sm leading-relaxed text-text outline-none placeholder:text-muted focus:border-accent"
      />

      {workflowType === "literature-synthesis" && (
        <LiteratureGenerationMode
          generationMode={generationMode}
          remoteDataApproved={remoteDataApproved}
          remoteDestination={remoteDestination}
          onGenerationModeChange={setGenerationMode}
          onRemoteApprovalChange={setRemoteDataApproved}
        />
      )}

      <div className="mt-3 flex items-center gap-3 border-t border-border-faint pt-3">
        <p className="min-w-0 flex-1 text-[11px] leading-relaxed text-muted">
          {!canStart
            ? t("research.workflow.serviceRequired", {
                defaultValue: "Science core must be ready to create a workflow.",
              })
            : sourceReady
              ? workflowType === "dataset-analysis"
                ? t("research.workflow.datasetComposerHint", {
                    defaultValue:
                      "The selected dataset hash is frozen into the plan and every execution approval.",
                  })
                : t("research.workflow.localComposerHint", {
                    defaultValue:
                      "This workflow uses only indexed PDFs already in this project.",
                  })
              : workflowType === "dataset-analysis"
                ? t("research.workflow.needsDataset", {
                    defaultValue: "Choose or import a ready CSV dataset.",
                  })
                : t("research.workflow.needsPdf", {
                    defaultValue:
                      "Import at least one indexed PDF to start a research task.",
                  })}
        </p>
        <button
          type="submit"
          disabled={!canSubmit}
          className="flex shrink-0 items-center gap-1.5 rounded-input bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-40"
        >
          {busy ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <ChevronRight size={13} />
          )}
          {busy
            ? t("research.workflow.starting", { defaultValue: "Starting…" })
            : t("research.workflow.start", { defaultValue: "Create plan" })}
        </button>
      </div>
    </form>
  );
}

function WorkflowTypeButton({
  selected,
  icon,
  title,
  description,
  onClick,
}: {
  selected: boolean;
  icon: React.ReactNode;
  title: string;
  description: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={cn(
        "rounded-input border px-3 py-2 text-left",
        selected
          ? "border-accent/40 bg-accent/5"
          : "border-border bg-bg hover:bg-surface-2",
      )}
    >
      <span className="flex items-center gap-2 text-xs font-medium text-text">
        {icon}
        {title}
      </span>
      <span className="mt-1 block text-[10px] leading-relaxed text-muted">
        {description}
      </span>
    </button>
  );
}

function LiteratureGenerationMode({
  generationMode,
  remoteDataApproved,
  remoteDestination,
  onGenerationModeChange,
  onRemoteApprovalChange,
}: {
  generationMode: ResearchGenerationMode;
  remoteDataApproved: boolean;
  remoteDestination: ScienceCoreModelDestination | null;
  onGenerationModeChange: (mode: ResearchGenerationMode) => void;
  onRemoteApprovalChange: (approved: boolean) => void;
}) {
  const { t } = useTranslation("pages");
  const remoteAssisted = generationMode === "remote-model-assisted";
  return (
    <div className="mt-3">
      <div
        role="group"
        aria-label={t("research.workflow.generationMode.label", {
          defaultValue: "Generation mode",
        })}
      >
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
              onGenerationModeChange("local-deterministic");
              onRemoteApprovalChange(false);
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
            <span className="mt-0.5 block text-[10px] text-muted">
              {t("research.workflow.localDeterministicHint", {
                defaultValue: "Plan and synthesis stay on this Mac.",
              })}
            </span>
          </button>
          <button
            type="button"
            aria-pressed={remoteAssisted}
            disabled={!remoteDestination}
            onClick={() =>
              remoteDestination &&
              onGenerationModeChange("remote-model-assisted")
            }
            className={cn(
              "rounded-input border px-3 py-2 text-left",
              remoteAssisted
                ? "border-warn/40 bg-warn/5"
                : "border-border bg-bg hover:bg-surface-2 disabled:opacity-40",
            )}
          >
            <span className="block text-xs font-medium text-text">
              {t("research.workflow.remoteModelAssisted", {
                defaultValue: "Model-assisted remote",
              })}
            </span>
            <span className="mt-0.5 block text-[10px] text-muted">
              {t("research.workflow.remoteModelAssistedHint", {
                defaultValue:
                  "Use the configured remote model with explicit data approval.",
              })}
            </span>
          </button>
        </div>
      </div>

      {remoteAssisted && remoteDestination && (
        <div className="mt-3 rounded-input border border-warn/30 bg-warn/5 px-3 py-2.5">
          <dl className="mb-2 grid gap-1 border-b border-warn/20 pb-2 text-[10px] text-muted sm:grid-cols-2">
            <div>
              <dt className="font-medium text-text">
                {t("research.workflow.model", { defaultValue: "Model" })}
              </dt>
              <dd className="break-all font-mono">
                {remoteDestination.model}
              </dd>
            </div>
            <div>
              <dt className="font-medium text-text">
                {t("research.endpointHost", { defaultValue: "Endpoint host" })}
              </dt>
              <dd className="break-all font-mono">
                {remoteDestination.endpointHost}
              </dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="font-medium text-text">
                {t("research.endpointIdentity", {
                  defaultValue: "Endpoint identity",
                })}
              </dt>
              <dd className="break-all font-mono">
                {remoteDestination.endpointIdentity}
              </dd>
            </div>
          </dl>
          <p className="mb-2 text-[10px] leading-relaxed text-muted">
            {t("research.workflow.passageDisclosureBoundary", {
              defaultValue:
                "No PDF passage is sent at this step. Approving the generated plan later authorizes only verified passages represented by that approval's affected resources, for that plan version.",
            })}
          </p>
          <label className="flex cursor-pointer items-start gap-2 text-xs leading-relaxed text-text">
            <input
              type="checkbox"
              checked={remoteDataApproved}
              onChange={(event) =>
                onRemoteApprovalChange(event.target.checked)
              }
              className="mt-0.5 shrink-0 accent-accent"
            />
            <span>
              {t("research.workflow.goalDisclosureApproval", {
                defaultValue:
                  "I approve sending this research goal to {{model}} at {{host}} to create the plan.",
                model: remoteDestination.model,
                host: remoteDestination.endpointHost,
              })}
            </span>
          </label>
        </div>
      )}
    </div>
  );
}
