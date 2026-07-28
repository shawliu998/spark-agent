import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  BarChart3,
  ChevronRight,
  FileText,
  Loader2,
  Route,
  SlidersHorizontal,
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
type ComposerMode = "autonomous" | "advanced";
const AUTONOMOUS_MODE: ComposerMode = "autonomous";
const ADVANCED_MODE: ComposerMode = "advanced";
const LOCAL_GENERATION_MODE: ResearchGenerationMode = "local-deterministic";

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
  const [composerMode, setComposerMode] =
    useState<ComposerMode>("autonomous");
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
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
  const readySources = useMemo(
    () => [...readyPdfs, ...readyDatasets],
    [readyDatasets, readyPdfs],
  );
  const selectedDataset =
    readyDatasets.find((source) => source.id === datasetSourceId) ?? null;
  const selectedSources = useMemo(
    () =>
      readySources.filter((source) => selectedSourceIds.includes(source.id)),
    [readySources, selectedSourceIds],
  );
  const autoRemoteAssisted =
    composerMode === "autonomous" &&
    generationMode === "remote-model-assisted";
  const literatureRemoteAssisted =
    composerMode === "advanced" &&
    workflowType === "literature-synthesis" &&
    generationMode === "remote-model-assisted";
  const remoteAssisted = autoRemoteAssisted || literatureRemoteAssisted;
  const remoteDestinationApprovalKey = remoteDestination
    ? `${remoteDestination.endpointIdentity}:${remoteDestination.model}`
    : null;
  const autoRemoteSourceApprovalKey = autoRemoteAssisted
    ? [...selectedSourceIds].sort().join("\n")
    : null;

  useEffect(() => {
    setDatasetSourceId((current) =>
      readyDatasets.some((source) => source.id === current)
        ? current
        : (readyDatasets[0]?.id ?? null),
    );
  }, [readyDatasets]);

  useEffect(() => {
    setSelectedSourceIds((current) => {
      const readyIds = new Set(readySources.map((source) => source.id));
      return current.filter((sourceId) => readyIds.has(sourceId));
    });
  }, [readySources]);

  useEffect(() => {
    setRemoteDataApproved(false);
    if (!remoteDestinationApprovalKey) setGenerationMode("local-deterministic");
  }, [remoteDestinationApprovalKey]);

  useEffect(() => {
    if (autoRemoteAssisted) setRemoteDataApproved(false);
  }, [autoRemoteAssisted, autoRemoteSourceApprovalKey]);

  useEffect(() => {
    if (workflowType === "dataset-analysis") {
      setGenerationMode("local-deterministic");
      setRemoteDataApproved(false);
    }
  }, [workflowType]);

  const sourceReady =
    composerMode === "autonomous"
      ? selectedSourceIds.length > 0
      : workflowType === "dataset-analysis"
      ? selectedDataset !== null
      : readyPdfs.length > 0;
  const canSubmit =
    goal.trim().length > 0 &&
    canStart &&
    sourceReady &&
    !busy &&
    (!remoteAssisted || (remoteDataApproved && remoteDestination != null));
  const configuredSourceCount =
    composerMode === "autonomous"
      ? selectedSourceIds.length
      : workflowType === "dataset-analysis"
        ? selectedDataset
          ? 1
          : 0
        : readyPdfs.length;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const next = goal.trim();
    if (!next || !canSubmit) return;
    if (composerMode === "autonomous") {
      await onCreate(next, {
        mode: "autonomous",
        sourceIds: selectedSourceIds,
        remoteDataApproved: autoRemoteAssisted && remoteDataApproved,
      });
      return;
    }
    await onCreate(next, {
      mode: "advanced",
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
      className="rounded-card border border-border bg-surface p-4"
    >
      <div>
        <label
          htmlFor="research-workflow-goal"
          className="text-base font-semibold leading-6 text-text"
        >
          {t("research.workflow.composerLabel", {
            defaultValue: "Research question",
          })}
        </label>
        <p className="mt-1 text-xs leading-relaxed text-muted">
          {t("research.workflow.composerIntro", {
            defaultValue:
              "Define the question Spark should investigate using the sources in this project.",
          })}
        </p>
      </div>

      <textarea
        id="research-workflow-goal"
        value={goal}
        onChange={(event) => {
          setGoal(event.target.value);
          setRemoteDataApproved(false);
        }}
        rows={3}
        placeholder={
          composerMode === "autonomous"
            ? t("research.workflow.autoComposerPlaceholder", {
                defaultValue:
                  "What does the available evidence show, where does it conflict, and what remains uncertain?",
              })
            : workflowType === "dataset-analysis"
              ? t("research.workflow.datasetComposerPlaceholder", {
                  defaultValue:
                    "Summarize the primary outcome by experimental group and report missingness…",
                })
              : t("research.workflow.composerPlaceholder", {
                  defaultValue:
                    "Compare the findings, conflicting evidence, and limitations across these papers…",
                })
        }
        className="mt-3 w-full resize-y rounded-input border border-border bg-surface px-3 py-3 text-sm leading-relaxed text-text placeholder:text-muted focus:border-accent"
      />

      <details className="group mt-3 border-y border-border">
        <summary className="flex cursor-pointer list-none items-center gap-2 py-2.5 text-xs font-medium text-text marker:content-none">
          <SlidersHorizontal size={13} className="text-muted" />
          {t("research.workflow.settings", {
            defaultValue: "Research settings",
          })}
          <span className="ml-auto text-xs font-normal text-muted">
            {t("research.workflow.settingsSummary", {
              defaultValue: "{{count}} sources · {{processing}}",
              count: configuredSourceCount,
              processing: remoteAssisted
                ? t("research.workflow.processingRemote", {
                    defaultValue: "model-assisted",
                  })
                : t("research.workflow.processingLocal", {
                    defaultValue: "local",
                  }),
            })}
          </span>
          <ChevronRight
            size={13}
            className="text-muted transition-transform group-open:rotate-90"
          />
        </summary>
        <div className="border-t border-border-faint pb-3">
      <div
        className="mt-3"
        role="group"
        aria-label={t("research.workflow.modeLabel", {
          defaultValue: "Research mode",
        })}
      >
        <div className="grid gap-2 sm:grid-cols-2">
          <WorkflowTypeButton
            selected={composerMode === "autonomous"}
            icon={<Route size={15} />}
            title={t("research.workflow.modeAuto", {
              defaultValue: "Recommended",
            })}
            description={t("research.workflow.modeAutoHint", {
              defaultValue:
                "Let the research question and selected sources determine the workflow.",
            })}
            onClick={() => {
              setComposerMode(AUTONOMOUS_MODE);
              setGenerationMode(LOCAL_GENERATION_MODE);
              setRemoteDataApproved(false);
            }}
          />
          <WorkflowTypeButton
            selected={composerMode === "advanced"}
            icon={<SlidersHorizontal size={15} />}
            title={t("research.workflow.modeAdvanced", {
              defaultValue: "Choose workflow",
            })}
            description={t("research.workflow.modeAdvancedHint", {
              defaultValue:
                "Select literature synthesis or dataset analysis explicitly.",
            })}
            onClick={() => {
              setComposerMode(ADVANCED_MODE);
              setGenerationMode(LOCAL_GENERATION_MODE);
              setRemoteDataApproved(false);
            }}
          />
        </div>
      </div>

      {composerMode === "advanced" && (
      <div
        className="mt-3"
        role="group"
        aria-label={t("research.workflow.typeLabel", {
          defaultValue: "Workflow type",
        })}
      >
        <p className="text-xs font-medium text-muted">
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
      )}

      {composerMode === "autonomous" && (
        <div className="mt-3 border-y border-border bg-surface-2/60 px-3 py-3">
          <div className="flex items-end gap-2">
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium text-muted">
                {t("research.workflow.autoSources", {
                  defaultValue: "Sources for this research run",
                })}
              </p>
              <p className="mt-1 text-xs leading-relaxed text-muted">
                {t("research.workflow.autoSourcesHint", {
                  defaultValue:
                    "Select the local PDFs and datasets that may be used for this review.",
                })}
              </p>
            </div>
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

          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            {readySources.map((source) => {
              const selected = selectedSourceIds.includes(source.id);
              return (
                <label
                  key={source.id}
                  className={cn(
                    "flex cursor-pointer items-start gap-2 rounded-input border px-2.5 py-2 text-xs",
                    selected
                      ? "border-accent/40 bg-accent/5"
                      : "border-border bg-surface",
                  )}
                >
                  <input
                    type="checkbox"
                    checked={selected}
                    onChange={() =>
                      setSelectedSourceIds((current) =>
                        current.includes(source.id)
                          ? current.filter((sourceId) => sourceId !== source.id)
                          : [...current, source.id],
                      )
                    }
                    className="mt-0.5 accent-[var(--accent)]"
                    aria-label={t("research.workflow.toggleSource", {
                      defaultValue: "Use {{title}}",
                      title: source.title,
                    })}
                  />
                  <span className="min-w-0">
                    <span className="block truncate font-medium text-text">
                      {source.title}
                    </span>
                    <span className="mt-0.5 block text-xs text-muted">
                      {source.sourceKind === "dataset"
                        ? t("research.workflow.sourceDataset", {
                            defaultValue: "CSV dataset",
                          })
                        : t("research.workflow.sourcePdf", {
                            defaultValue: "PDF",
                          })}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
          {readySources.length === 0 && (
            <p className="mt-2 text-caption leading-relaxed text-muted">
              {t("research.workflow.autoSourceRequired", {
                defaultValue:
                  "Choose at least one ready PDF or CSV source before creating a plan.",
              })}
            </p>
          )}
        </div>
      )}

      {composerMode === "autonomous" && (
        <AutoRoutingMode
          generationMode={generationMode}
          remoteDataApproved={remoteDataApproved}
          remoteDestination={remoteDestination}
          selectedSources={selectedSources}
          onGenerationModeChange={setGenerationMode}
          onRemoteApprovalChange={setRemoteDataApproved}
        />
      )}

      {composerMode === "advanced" && workflowType === "dataset-analysis" && (
        <div className="mt-3 rounded-input border border-border bg-bg p-3">
          <div className="flex items-end gap-2">
            <label className="min-w-0 flex-1 text-caption font-medium text-muted">
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
            <dl className="mt-2 grid gap-2 border-t border-border-faint pt-2 text-caption sm:grid-cols-2">
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
            <p className="mt-2 text-caption leading-relaxed text-warn">
              {t("research.workflow.datasetRequired", {
                defaultValue:
                  "Import a CSV and wait until it is ready before creating a dataset workflow.",
              })}
            </p>
          )}
        </div>
      )}

      {composerMode === "advanced" &&
        workflowType === "literature-synthesis" && (
        <LiteratureGenerationMode
          generationMode={generationMode}
          remoteDataApproved={remoteDataApproved}
          remoteDestination={remoteDestination}
          onGenerationModeChange={setGenerationMode}
          onRemoteApprovalChange={setRemoteDataApproved}
        />
      )}
        </div>
      </details>

      <div className="mt-3 flex items-center gap-3 border-t border-border-faint pt-3">
        <p className="min-w-0 flex-1 text-xs leading-relaxed text-muted">
          {!canStart
            ? t("research.workflow.serviceRequired", {
                defaultValue: "Connect the local research service to create a plan.",
              })
            : sourceReady
              ? composerMode === "autonomous"
                ? t("research.workflow.autoComposerHint", {
                    defaultValue:
                      "Auto routes from the goal and explicitly selected sources. Ambiguity creates a durable clarification request.",
                  })
                : workflowType === "dataset-analysis"
                ? t("research.workflow.datasetComposerHint", {
                    defaultValue:
                      "The selected dataset hash is frozen into the plan and every execution approval.",
                  })
                : t("research.workflow.localComposerHint", {
                    defaultValue:
                      "This workflow uses only indexed PDFs already in this project.",
                  })
              : composerMode === "autonomous"
                ? t("research.workflow.needsAutoSource", {
                    defaultValue:
                      "Select at least one ready PDF or CSV source before starting.",
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
          className="flex min-h-9 shrink-0 items-center gap-1.5 rounded-input bg-accent px-4 py-2 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-40"
        >
          {busy ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <ChevronRight size={13} />
          )}
          {busy
            ? t("research.workflow.starting", { defaultValue: "Starting…" })
            : composerMode === "autonomous"
              ? t("research.workflow.startAuto", { defaultValue: "Create review plan" })
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
        "rounded-input border px-3 py-2.5 text-left",
        selected
          ? "border-accent/40 bg-accent/5"
          : "border-border bg-bg hover:bg-surface-2",
      )}
    >
      <span className="flex items-center gap-2 text-xs font-medium text-text">
        {icon}
        {title}
      </span>
      <span className="mt-1 block text-xs leading-relaxed text-muted">
        {description}
      </span>
    </button>
  );
}

function AutoRoutingMode({
  generationMode,
  remoteDataApproved,
  remoteDestination,
  selectedSources,
  onGenerationModeChange,
  onRemoteApprovalChange,
}: {
  generationMode: ResearchGenerationMode;
  remoteDataApproved: boolean;
  remoteDestination: ScienceCoreModelDestination | null;
  selectedSources: ResearchSource[];
  onGenerationModeChange: (mode: ResearchGenerationMode) => void;
  onRemoteApprovalChange: (approved: boolean) => void;
}) {
  const { t } = useTranslation("pages");
  const remoteAssisted = generationMode === "remote-model-assisted";
  const hasSelectedDataset = selectedSources.some(
    (source) => source.sourceKind === "dataset",
  );
  return (
    <div className="mt-3">
      <p className="text-xs font-medium text-muted">
        {t("research.workflow.routerMode.label", {
          defaultValue: "Processing",
        })}
      </p>
      <div
        className="mt-1.5 grid gap-2 sm:grid-cols-2"
        role="group"
        aria-label={t("research.workflow.routerMode.label", {
          defaultValue: "Processing",
        })}
      >
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
            {t("research.workflow.routerMode.local", {
              defaultValue: "Local workflow",
            })}
          </span>
          <span className="mt-0.5 block text-caption text-muted">
            {t("research.workflow.routerMode.localHint", {
              defaultValue:
                "Plan and process the selected sources without contacting a model provider.",
            })}
          </span>
        </button>
        <button
          type="button"
          aria-pressed={remoteAssisted}
          disabled={!remoteDestination}
          onClick={() => {
            if (remoteDestination) {
              onGenerationModeChange("remote-model-assisted");
              onRemoteApprovalChange(false);
            }
          }}
          className={cn(
            "rounded-input border px-3 py-2 text-left",
            remoteAssisted
              ? "border-warn/40 bg-warn/5"
              : "border-border bg-bg hover:bg-surface-2 disabled:opacity-40",
          )}
        >
          <span className="block text-xs font-medium text-text">
            {t("research.workflow.routerMode.remote", {
              defaultValue: "Model-assisted workflow",
            })}
          </span>
          <span className="mt-0.5 block text-caption text-muted">
            {t(
              hasSelectedDataset
                ? "research.workflow.routerMode.remoteHintDataset"
                : "research.workflow.routerMode.remoteHint",
              {
                defaultValue: hasSelectedDataset
                  ? "The configured model helps route and select an analysis method only after explicit metadata and bounded Dataset Profile approval."
                  : "The configured model helps route only after explicit metadata approval.",
              },
            )}
          </span>
        </button>
      </div>

      {remoteAssisted && remoteDestination && (
        <div className="mt-3 rounded-input border border-warn/30 bg-warn/5 px-3 py-2.5">
          <dl className="grid gap-2 border-b border-warn/20 pb-2 text-caption text-muted sm:grid-cols-3">
            <div>
              <dt className="font-medium text-text">
                {t("research.provider", { defaultValue: "Provider" })}
              </dt>
              <dd className="break-all font-mono">
                {remoteDestination.provider}
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
            <div>
              <dt className="font-medium text-text">
                {t("research.workflow.model", { defaultValue: "Model" })}
              </dt>
              <dd className="break-all font-mono">{remoteDestination.model}</dd>
            </div>
          </dl>
          <p className="mt-2 text-xs font-medium text-text">
            {t("research.workflow.routerMode.sentHeading", {
              defaultValue: "Sent to the model",
            })}
          </p>
          <p className="mt-1 max-w-[70ch] text-ui leading-relaxed text-muted">
            {t(
              hasSelectedDataset
                ? "research.workflow.routerMode.sentBoundaryDataset"
                : "research.workflow.routerMode.sentBoundary",
              {
                defaultValue: hasSelectedDataset
                  ? "The research goal; each selected source's ID, type, and ingestion status; and, after routing, a locally generated bounded Dataset Profile for each selected CSV. Profiles include column names, inferred types, missing and unique counts, and bounded low-cardinality summaries for method selection."
                  : "The research goal and each selected source's ID, type, and ingestion status.",
              },
            )}
          </p>
          {selectedSources.length > 0 && (
            <ul className="mt-2 space-y-1">
              {selectedSources.map((source) => (
                <li
                  key={source.id}
                  className="break-all font-mono text-caption text-muted"
                >
                  {source.id} · {source.sourceKind} · {source.ingestionStatus}
                </li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-caption font-medium text-text">
            {t("research.workflow.routerMode.notSentHeading", {
              defaultValue: "Not sent",
            })}
          </p>
          <p className="mt-1 text-caption leading-relaxed text-muted">
            {t("research.workflow.routerMode.notSentBoundary", {
              defaultValue:
                "PDF text or passages, CSV rows, full cell-level content, and full document contents are not sent.",
            })}
          </p>
          <label className="mt-3 flex cursor-pointer items-start gap-2 border-t border-warn/20 pt-2 text-xs leading-relaxed text-text">
            <input
              type="checkbox"
              checked={remoteDataApproved}
              onChange={(event) =>
                onRemoteApprovalChange(event.target.checked)
              }
              className="mt-0.5 shrink-0 accent-accent"
            />
            <span>
              {t(
                hasSelectedDataset
                  ? "research.workflow.routerMode.approvalDataset"
                  : "research.workflow.routerMode.approval",
                {
                  defaultValue: hasSelectedDataset
                    ? "I approve sending this goal, the listed source metadata, and the bounded Dataset Profile fields described above to {{model}} at {{host}} for routing and method selection."
                    : "I approve sending this goal and the listed source metadata to {{model}} at {{host}} for this routing request.",
                  model: remoteDestination.model,
                  host: remoteDestination.endpointHost,
                },
              )}
            </span>
          </label>
        </div>
      )}
    </div>
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
        <p className="text-caption font-medium text-muted">
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
            <span className="mt-0.5 block text-caption text-muted">
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
            <span className="mt-0.5 block text-caption text-muted">
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
          <dl className="mb-2 grid gap-1 border-b border-warn/20 pb-2 text-caption text-muted sm:grid-cols-2">
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
          <p className="mb-2 text-caption leading-relaxed text-muted">
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
