import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  ChevronRight,
  Code2,
  Database,
  FileOutput,
  FileSpreadsheet,
  Hash,
  Loader2,
  LockKeyhole,
  NotebookPen,
  Play,
  Plus,
  RefreshCw,
  ScrollText,
  ShieldAlert,
  Upload,
  WifiOff,
  XCircle,
} from "lucide-react";
import type {
  AnalysisArtifact,
  AnalysisRun,
  AnalysisRunStatus,
  AgentResearchWorkflowSnapshot,
  DatasetAnalysisWorkflowSnapshot,
  ResearchWorkflowSnapshot,
  ResearchProject,
  ResearchSource,
  ScienceCoreHealth,
  StructuredAnalysisResult,
  WorkflowStructuredAnalysisResult,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";
import { parseTableFile } from "@/lib/csv";
import { scienceCore } from "@/lib/scienceCore";
import { toast } from "@/lib/toast";

const CSV_ACCEPT = ".csv,text/csv";

function isDatasetWorkflow(
  snapshot: ResearchWorkflowSnapshot,
): snapshot is DatasetAnalysisWorkflowSnapshot | AgentResearchWorkflowSnapshot {
  return snapshot.workflow.workflowType === "dataset-analysis";
}

function idempotencyKey(): string {
  return crypto.randomUUID();
}

function hasWorkflowAction(snapshot: ResearchWorkflowSnapshot, action: string): boolean {
  return (snapshot.allowedActions as readonly string[]).includes(action);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function fileName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

function formatTime(value: string | null): string {
  if (!value) return "";
  const date = new Date(withUtcOffset(value));
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

function withUtcOffset(value: string): string {
  return /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
}

/** Safe-by-default CSV analysis workflow backed by science-core approvals. */
export function AnalysisPage() {
  const { t } = useTranslation("pages");
  const [searchParams] = useSearchParams();
  const requestedProjectId = searchParams.get("project");
  const fileInput = useRef<HTMLInputElement>(null);
  const proposalForm = useRef<HTMLFormElement>(null);
  const createWorkflowKey = useRef<string | null>(null);
  const mutationKey = useRef<string | null>(null);
  const [health, setHealth] = useState<ScienceCoreHealth | null>(null);
  const [projects, setProjects] = useState<ResearchProject[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [sources, setSources] = useState<ResearchSource[]>([]);
  const [datasetId, setDatasetId] = useState<string | null>(null);
  const [runs, setRuns] = useState<AnalysisRun[]>([]);
  const [workflow, setWorkflow] = useState<ResearchWorkflowSnapshot | null>(null);
  const [persistedStructuredResult, setPersistedStructuredResult] =
    useState<WorkflowStructuredAnalysisResult | null>(null);
  const [objective, setObjective] = useState("");
  const [projectTitle, setProjectTitle] = useState("");
  const [showProjectForm, setShowProjectForm] = useState(false);
  const [booting, setBooting] = useState(true);
  const [loadingProject, setLoadingProject] = useState(false);
  const [creatingProject, setCreatingProject] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [workflowAction, setWorkflowAction] = useState<string | null>(null);
  const [refreshingRuns, setRefreshingRuns] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);

  const loadWorkspace = useCallback(async () => {
    setBooting(true);
    setPageError(null);
    try {
      const [nextHealth, nextProjects] = await Promise.all([
        scienceCore.health(),
        scienceCore.listProjects(),
      ]);
      setHealth(nextHealth);
      setProjects(nextProjects);
      setProjectId((current) =>
        requestedProjectId && nextProjects.some((project) => project.id === requestedProjectId)
            ? requestedProjectId
          : nextProjects.some((project) => project.id === current)
            ? current
          : nextProjects[0]?.id ?? null,
      );
    } catch (error) {
      setHealth(null);
      setPageError(errorMessage(error));
    } finally {
      setBooting(false);
    }
  }, [requestedProjectId]);

  useEffect(() => {
    void loadWorkspace();
  }, [loadWorkspace]);

  useEffect(() => {
    let cancelled = false;
    setWorkflow(null);
    setPersistedStructuredResult(null);
    createWorkflowKey.current = null;
    mutationKey.current = null;
    setDatasetId(null);
    if (!projectId) {
      setSources([]);
      setRuns([]);
      return;
    }

    setLoadingProject(true);
    void Promise.all([
      scienceCore.listSources(projectId),
      scienceCore.listAnalysisRuns(projectId),
      scienceCore.listWorkflows(projectId, { activeOnly: false, limit: 20 }),
      scienceCore.listAgentRuns(projectId, { activeOnly: false, limit: 20 }),
    ])
      .then(([nextSources, nextRuns, nextWorkflows, nextAgentRuns]) => {
        if (cancelled) return;
        setSources(nextSources);
        setRuns(nextRuns);
        const datasets = nextSources.filter((source) => source.sourceKind === "dataset");
        const datasetIds = new Set(datasets.map((dataset) => dataset.id));
        const latestDatasetWorkflow =
          [...nextWorkflows, ...nextAgentRuns]
            .filter(
              (candidate) =>
                isDatasetWorkflow(candidate) ||
                (candidate.workflow.mode === "autonomous" &&
                  (candidate.workflow.sourceIds ?? []).some((sourceId) =>
                    datasetIds.has(sourceId),
                  )),
            )
            .sort(
              (left, right) =>
                Date.parse(right.workflow.updatedAt) - Date.parse(left.workflow.updatedAt),
            )[0] ?? null;
        setWorkflow(latestDatasetWorkflow);
        setDatasetId(
          (latestDatasetWorkflow && "datasetSourceId" in latestDatasetWorkflow.workflow
            ? latestDatasetWorkflow.workflow.datasetSourceId
            : latestDatasetWorkflow?.workflow.sourceIds?.find((sourceId) =>
                datasetIds.has(sourceId),
              )) ??
            datasets[0]?.id ??
            null,
        );
        if (latestDatasetWorkflow) setObjective(latestDatasetWorkflow.workflow.goal);
      })
      .catch((error) => {
        if (!cancelled) {
          setSources([]);
          setRuns([]);
          toast.error(
            t("analysis.toast.loadProjectFailed", {
              defaultValue: "Could not load analysis project: {{error}}",
              error: errorMessage(error),
            }),
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingProject(false);
      });

    return () => {
      cancelled = true;
    };
  }, [projectId, t]);

  const datasets = useMemo(
    () => sources.filter((source) => source.sourceKind === "dataset"),
    [sources],
  );
  const selectedProject = projects.find((project) => project.id === projectId) ?? null;
  const selectedDataset = datasets.find((dataset) => dataset.id === datasetId) ?? null;
  const serviceReady = health?.database === "ok" && health.runtime === "ready";
  const hasActiveRun = runs.some((run) => run.status === "pending" || run.status === "running");
  const proposalLocked = workflow !== null;
  const datasetWorkflow = workflow && isDatasetWorkflow(workflow) ? workflow : null;
  useEffect(() => {
    if (datasetWorkflow?.structuredResult) {
      setPersistedStructuredResult(datasetWorkflow.structuredResult);
    }
  }, [datasetWorkflow?.structuredResult]);
  const displayedRuns = useMemo(() => {
    const workflowRun = datasetWorkflow?.analysisRun ?? null;
    if (!workflowRun) return runs;
    return [workflowRun, ...runs.filter((run) => run.id !== workflowRun.id)];
  }, [datasetWorkflow, runs]);

  const refreshRuns = useCallback(
    async (quiet = false) => {
      if (!projectId) return;
      if (!quiet) setRefreshingRuns(true);
      try {
        setRuns(await scienceCore.listAnalysisRuns(projectId));
      } catch (error) {
        if (!quiet) {
          toast.error(
            t("analysis.toast.loadRunsFailed", {
              defaultValue: "Could not refresh runs: {{error}}",
              error: errorMessage(error),
            }),
          );
        }
      } finally {
        if (!quiet) setRefreshingRuns(false);
      }
    },
    [projectId, t],
  );

  useEffect(() => {
    if (!projectId || !hasActiveRun) return;
    const timer = window.setInterval(() => void refreshRuns(true), 2_000);
    return () => window.clearInterval(timer);
  }, [hasActiveRun, projectId, refreshRuns]);

  useEffect(() => {
    if (
      !workflow ||
      !["routing", "planning", "running", "reviewing"].includes(workflow.workflow.status)
    ) {
      return;
    }
    let cancelled = false;
    const refresh = async () => {
      try {
        const next =
          workflow.workflow.mode === "autonomous"
            ? await scienceCore.getAgentRun(workflow.workflow.id)
            : await scienceCore.getWorkflow(workflow.workflow.id);
        if (!cancelled) setWorkflow(next);
      } catch (error) {
        if (!cancelled) {
          toast.error(
            t("analysis.toast.loadWorkflowFailed", {
              defaultValue: "Could not refresh the analysis workflow: {{error}}",
              error: errorMessage(error),
            }),
          );
        }
      }
    };
    const timer = window.setInterval(() => void refresh(), 1_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [t, workflow]);

  const createProject = async (event: React.FormEvent) => {
    event.preventDefault();
    const title = projectTitle.trim();
    if (!title || !serviceReady) return;
    setCreatingProject(true);
    try {
      const project = await scienceCore.createProject({ title });
      setProjects((current) => [project, ...current.filter((item) => item.id !== project.id)]);
      setProjectId(project.id);
      setProjectTitle("");
      setShowProjectForm(false);
      toast.success(
        t("analysis.toast.projectCreated", { defaultValue: "Analysis project created." }),
      );
    } catch (error) {
      toast.error(
        t("analysis.toast.createProjectFailed", {
          defaultValue: "Could not create project: {{error}}",
          error: errorMessage(error),
        }),
      );
    } finally {
      setCreatingProject(false);
    }
  };

  const uploadDataset = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !projectId || !serviceReady) return;
    if (!file.name.toLowerCase().endsWith(".csv")) {
      toast.error(t("analysis.toast.csvOnly", { defaultValue: "Choose a CSV file." }));
      return;
    }
    setUploading(true);
    try {
      const dataset = await scienceCore.importDataset(projectId, file);
      setSources((current) => [dataset, ...current.filter((item) => item.id !== dataset.id)]);
      setDatasetId(dataset.id);
      setWorkflow(null);
      createWorkflowKey.current = null;
      toast.success(
        t("analysis.toast.datasetUploaded", { defaultValue: "CSV uploaded to the project." }),
      );
    } catch (error) {
      toast.error(
        t("analysis.toast.uploadFailed", {
          defaultValue: "Could not upload CSV: {{error}}",
          error: errorMessage(error),
        }),
      );
    } finally {
      setUploading(false);
    }
  };

  const prepareWorkflow = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!projectId || !datasetId || !objective.trim() || workflow) return;
    setPreparing(true);
    const key = createWorkflowKey.current ?? idempotencyKey();
    createWorkflowKey.current = key;
    try {
      const prepared = await scienceCore.createAgentRun(
        projectId,
        {
          goal: objective.trim(),
          sourceIds: [datasetId],
          mode: "autonomous",
          remoteDataApproved: false,
        },
        { idempotencyKey: key },
      );
      createWorkflowKey.current = null;
      setWorkflow(prepared);
      toast.success(
        t("analysis.toast.intentPrepared", {
          defaultValue: "Spark is profiling the dataset and preparing a bounded analysis plan.",
        }),
      );
    } catch (error) {
      toast.error(
        t("analysis.toast.prepareFailed", {
          defaultValue: "Could not prepare the analysis workflow: {{error}}",
          error: errorMessage(error),
        }),
      );
    } finally {
      setPreparing(false);
    }
  };

  const approvePlan = async () => {
    if (!workflow?.plan || !hasWorkflowAction(workflow, "approve-plan")) return;
    const approval = workflow.pendingApprovals.find((item) => item.kind === "plan");
    if (!approval) return;
    setWorkflowAction("approve-plan");
    const key = mutationKey.current ?? idempotencyKey();
    mutationKey.current = key;
    try {
      const updated = await scienceCore.approveWorkflowPlan(
        workflow.workflow.id,
        {
          approvalId: approval.id,
          planId: workflow.plan.id,
          planVersion: workflow.plan.version,
          planSha256: workflow.plan.planSha256,
          expectedWorkflowRevision: workflow.workflow.revision,
        },
        { idempotencyKey: key },
      );
      if (!isDatasetWorkflow(updated)) throw new Error("Workflow type changed unexpectedly");
      mutationKey.current = null;
      setWorkflow(updated);
    } catch (error) {
      toast.error(
        t("analysis.toast.decisionFailed", {
          defaultValue: "Could not approve the analysis plan: {{error}}",
          error: errorMessage(error),
        }),
      );
    } finally {
      setWorkflowAction(null);
    }
  };

  const decideWorkflowAnalysis = async (decision: "approved" | "rejected") => {
    if (
      !workflow?.analysisIntent ||
      workflow.analysisIntent.status !== "waiting-approval" ||
      !hasWorkflowAction(
        workflow,
        decision === "approved" ? "approve-analysis" : "reject-analysis",
      )
    ) {
      return;
    }
    const approval = workflow.pendingApprovals.find(
      (item) =>
        item.kind === "analysis-execution" &&
        item.analysisIntentId === workflow.analysisIntent?.id,
    );
    if (!approval) return;
    setWorkflowAction(decision);
    const key = mutationKey.current ?? idempotencyKey();
    mutationKey.current = key;
    try {
      const updated = await scienceCore.decideWorkflowAnalysisIntent(
        workflow.workflow.id,
        {
          intentId: workflow.analysisIntent.id,
          approvalId: approval.id,
          decision,
          payloadSha256: workflow.analysisIntent.payloadSha256,
          expectedWorkflowRevision: workflow.workflow.revision,
        },
        { idempotencyKey: key },
      );
      if (!isDatasetWorkflow(updated)) throw new Error("Workflow type changed unexpectedly");
      mutationKey.current = null;
      setWorkflow(updated);
    } catch (error) {
      toast.error(
        t("analysis.toast.decisionFailed", {
          defaultValue: "Could not record the execution decision: {{error}}",
          error: errorMessage(error),
        }),
      );
    } finally {
      setWorkflowAction(null);
    }
  };

  const acceptReviewWarnings = async () => {
    if (
      !workflow ||
      !hasWorkflowAction(workflow, "accept-review-warnings") ||
      workflow.latestReview?.reviewType !== "deterministic-analysis-v1" ||
      workflow.latestReview.verdict !== "passed-with-warnings"
    ) {
      return;
    }
    setWorkflowAction("accept-review-warnings");
    try {
      const updated = await scienceCore.acceptWorkflowReviewWarnings(
        workflow.workflow.id,
        {
          reviewId: workflow.latestReview.id,
          reviewInputSha256: workflow.latestReview.inputSha256,
          expectedWorkflowRevision: workflow.workflow.revision,
          decision: "accepted",
        },
        {},
      );
      if (!isDatasetWorkflow(updated)) throw new Error("Workflow type changed unexpectedly");
      setWorkflow(updated);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setWorkflowAction(null);
    }
  };

  const startNewPlan = useCallback(
    (nextObjective = "") => {
      void refreshRuns(true);
      setWorkflow(null);
      setObjective(nextObjective);
      createWorkflowKey.current = null;
      mutationKey.current = null;
      window.setTimeout(() => {
        proposalForm.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      }, 0);
    },
    [refreshRuns],
  );

  return (
    <div className="h-full overflow-y-auto bg-bg">
      <div className="mx-auto max-w-[1320px] px-6 py-7 xl:px-10">
        <header className="flex items-start gap-4">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-input bg-accent/10 text-accent">
            <BarChart3 size={19} />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="font-serif text-xl text-text">
              {t("analysis.title", { defaultValue: "Data analysis" })}
            </h1>
            <p className="mt-1 text-sm text-muted">
              {t("analysis.subtitle", {
                defaultValue: "Turn a local dataset into reproducible tables, figures, and notebooks.",
              })}
            </p>
          </div>
          <HealthBadge health={health} loading={booting} />
        </header>

        {pageError && (
          <div className="mt-5 flex items-start gap-3 rounded-card border border-error/30 bg-error/5 p-4 text-sm">
            <AlertTriangle size={17} className="mt-0.5 shrink-0 text-error" />
            <div className="min-w-0 flex-1">
              <p className="font-medium text-text">
                {t("analysis.offlineTitle", { defaultValue: "Science core is offline" })}
              </p>
              <p className="mt-1 break-words text-xs text-muted">{pageError}</p>
            </div>
            <button type="button" onClick={() => void loadWorkspace()} className="text-xs text-link hover:underline">
              {t("analysis.retry", { defaultValue: "Retry" })}
            </button>
          </div>
        )}

        <section className="mt-5 rounded-card border border-border bg-surface">
          <div className="grid gap-4 p-4 md:grid-cols-2">
            <div className="min-w-0">
              <div className="flex items-center justify-between gap-2">
                <label className="flex items-center gap-2 text-xs font-medium text-muted">
                  <Database size={14} />
                  {t("analysis.projectHeading", { defaultValue: "Research project" })}
                </label>
                <button
                  type="button"
                  onClick={() => setShowProjectForm((open) => !open)}
                  disabled={!serviceReady}
                  className="rounded p-1 text-muted hover:bg-surface-2 hover:text-text disabled:opacity-40"
                  aria-label={t("analysis.newProjectAria", { defaultValue: "Create research project" })}
                >
                  <Plus size={14} />
                </button>
              </div>
              {projects.length > 0 ? (
                <select
                  value={projectId ?? ""}
                  onChange={(event) => setProjectId(event.target.value || null)}
                  className="mt-2 w-full rounded-input border border-border bg-bg px-3 py-2 text-sm text-text outline-none focus:border-accent"
                  aria-label={t("analysis.projectSelectAria", { defaultValue: "Research project" })}
                >
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>{project.title}</option>
                  ))}
                </select>
              ) : (
                !booting && <p className="mt-2 text-xs text-muted">{t("analysis.noProjects", { defaultValue: "Create a project to begin." })}</p>
              )}
              {(showProjectForm || (!booting && projects.length === 0)) && (
                <form onSubmit={(event) => void createProject(event)} className="mt-2 flex gap-2">
                  <input
                    value={projectTitle}
                    onChange={(event) => setProjectTitle(event.target.value)}
                    placeholder={t("analysis.projectNamePlaceholder", { defaultValue: "Project name" })}
                    className="min-w-0 flex-1 rounded-input border border-border bg-bg px-3 py-2 text-sm text-text outline-none placeholder:text-muted focus:border-accent"
                  />
                  <button
                    type="submit"
                    disabled={!projectTitle.trim() || creatingProject || !serviceReady}
                    className="flex items-center gap-1.5 rounded-input bg-accent px-3 py-2 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-40"
                  >
                    {creatingProject && <Loader2 size={12} className="animate-spin" />}
                    {t("analysis.createProject", { defaultValue: "Create project" })}
                  </button>
                </form>
              )}
              {selectedProject && (
                <p className="mt-1.5 truncate text-[10px] text-muted" title={selectedProject.projectPath}>{selectedProject.projectPath}</p>
              )}
            </div>

            <div className="min-w-0">
              <label className="flex items-center gap-2 text-xs font-medium text-muted">
                <FileSpreadsheet size={14} />
                {t("analysis.datasetHeading", { defaultValue: "Input dataset" })}
              </label>
              {loadingProject ? (
                <div className="mt-2 flex items-center gap-2 py-2 text-xs text-muted">
                  <Loader2 size={13} className="animate-spin" />
                  {t("analysis.loadingDatasets", { defaultValue: "Loading datasets…" })}
                </div>
              ) : datasets.length > 0 ? (
                <div className="mt-2 flex gap-2">
                  <select
                    value={datasetId ?? ""}
                    onChange={(event) => {
                      setDatasetId(event.target.value || null);
                      setWorkflow(null);
                      createWorkflowKey.current = null;
                    }}
                    disabled={proposalLocked}
                    className="min-w-0 flex-1 rounded-input border border-border bg-bg px-3 py-2 text-sm text-text outline-none focus:border-accent disabled:opacity-60"
                    aria-label={t("analysis.datasetSelectAria", { defaultValue: "Dataset" })}
                  >
                    {datasets.map((dataset) => (
                      <option key={dataset.id} value={dataset.id}>{dataset.title}</option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={() => fileInput.current?.click()}
                    disabled={!projectId || !serviceReady || uploading || proposalLocked}
                    className="flex shrink-0 items-center gap-1.5 rounded-input border border-border bg-bg px-3 py-2 text-xs text-text hover:bg-surface-2 disabled:opacity-40"
                  >
                    {uploading ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
                    {uploading ? t("analysis.uploading", { defaultValue: "Uploading…" }) : t("analysis.replaceCsv", { defaultValue: "Replace" })}
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => fileInput.current?.click()}
                  disabled={!projectId || !serviceReady || uploading || proposalLocked}
                  className="mt-2 flex w-full items-center justify-center gap-2 rounded-input border border-dashed border-border bg-bg px-3 py-2 text-sm text-muted hover:border-accent/50 hover:text-text disabled:opacity-40"
                >
                  {uploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
                  {projectId
                    ? t("analysis.uploadCsv", { defaultValue: "Upload CSV" })
                    : t("analysis.selectProjectFirst", { defaultValue: "Select a project first." })}
                </button>
              )}
              {selectedDataset && (
                <p className="mt-1.5 truncate text-[10px] text-muted" title={selectedDataset.contentHash}>
                  {t("analysis.datasetReady", { defaultValue: "Ready for analysis" })} · {selectedDataset.contentHash.slice(0, 12)}
                </p>
              )}
              <input ref={fileInput} type="file" accept={CSV_ACCEPT} onChange={(event) => void uploadDataset(event)} className="hidden" />
            </div>
          </div>
        </section>

        <main className="mt-5 space-y-5">
          {displayedRuns.length > 0 && (
            <AnalysisResultsWorkspace
              runs={displayedRuns}
              loading={loadingProject}
              refreshing={refreshingRuns}
              onRefresh={() => void refreshRuns()}
              structuredResult={
                datasetWorkflow?.structuredResult ?? persistedStructuredResult
              }
              onFollowUp={startNewPlan}
            />
          )}

          <form
            ref={proposalForm}
            onSubmit={(event) => void prepareWorkflow(event)}
            className="overflow-hidden rounded-card border border-border bg-surface shadow-card"
          >
            <div className="p-5">
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-medium text-text">
                  {t("analysis.questionHeading", { defaultValue: "What should Spark analyze?" })}
                </h2>
                {proposalLocked && (
                  <span className="ml-auto flex items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 text-[10px] text-muted ring-1 ring-border">
                    <LockKeyhole size={10} />
                    {t("analysis.locked", { defaultValue: "Locked" })}
                  </span>
                )}
              </div>
              <textarea
                id="analysis-objective"
                aria-label={t("analysis.objectiveLabel", { defaultValue: "Objective" })}
                value={objective}
                onChange={(event) => setObjective(event.target.value)}
                disabled={proposalLocked}
                rows={4}
                placeholder={t("analysis.objectivePlaceholder", { defaultValue: "Describe the question this analysis should answer." })}
                className="mt-3 w-full resize-y rounded-input border border-border bg-bg px-3.5 py-3 text-[15px] leading-relaxed text-text outline-none placeholder:text-muted focus:border-accent disabled:opacity-60"
              />

              <div className="mt-3 grid gap-2 rounded-input border border-border-faint bg-bg p-3 text-xs text-muted md:grid-cols-3">
                <p className="flex items-start gap-2">
                  <Database size={13} className="mt-0.5 shrink-0" />
                  {t("analysis.agentProfilesDataset", {
                    defaultValue: "Profiles columns, types, missing values, and sample bounds.",
                  })}
                </p>
                <p className="flex items-start gap-2">
                  <Code2 size={13} className="mt-0.5 shrink-0" />
                  {t("analysis.agentSelectsMethod", {
                    defaultValue: "Selects a bounded method from the research question.",
                  })}
                </p>
                <p className="flex items-start gap-2">
                  <ShieldAlert size={13} className="mt-0.5 shrink-0" />
                  {t("analysis.agentCompilesCode", {
                    defaultValue: "Compiles policy-verified Python after plan approval.",
                  })}
                </p>
              </div>

              <details className="mt-2 rounded-input border border-transparent">
                <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs text-muted hover:text-text">
                  <ShieldAlert size={13} />
                  {t("analysis.executionDetails", { defaultValue: "Execution boundary" })}
                </summary>
                <ul className="grid gap-2 border-t border-border-faint px-3 py-3 text-xs leading-relaxed text-muted md:grid-cols-3">
                  <li className="flex items-start gap-2"><WifiOff size={13} className="mt-0.5 shrink-0" />{t("analysis.safetyNoNetwork", { defaultValue: "External network access is denied." })}</li>
                  <li className="flex items-start gap-2"><NotebookPen size={13} className="mt-0.5 shrink-0" />{t("analysis.safetyJupyter", { defaultValue: "Code runs through the isolated Jupyter runtime." })}</li>
                  <li className="flex items-start gap-2"><LockKeyhole size={13} className="mt-0.5 shrink-0" />{t("analysis.safetyApproval", { defaultValue: "Approval applies only to the displayed payload hash." })}</li>
                </ul>
              </details>

              <div className="mt-4 flex items-center gap-3 border-t border-border-faint pt-4">
                <p className="min-w-0 flex-1 text-[11px] leading-relaxed text-muted">
                  {proposalLocked
                    ? t("analysis.proposalLockedHint", { defaultValue: "This proposal is locked to its server-generated hash. Start a new proposal to edit it." })
                    : t("analysis.prepareHint", { defaultValue: "Review the exact method before Spark runs it." })}
                </p>
                {proposalLocked ? (
                  <button
                    type="button"
                    onClick={() => startNewPlan()}
                    disabled={workflowAction !== null}
                    className="shrink-0 rounded-input border border-border px-3 py-2 text-xs text-text hover:bg-surface-2 disabled:opacity-40"
                  >
                    {t("analysis.newProposal", { defaultValue: "New proposal" })}
                  </button>
                ) : (
                  <button
                    type="submit"
                    disabled={!serviceReady || !datasetId || !objective.trim() || preparing}
                    className="flex shrink-0 items-center gap-1.5 rounded-input bg-accent px-4 py-2 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-40"
                  >
                    {preparing ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                    {preparing
                      ? t("analysis.preparing", { defaultValue: "Preparing…" })
                      : t("analysis.createAgentPlan", {
                          defaultValue: "Create analysis plan",
                        })}
                  </button>
                )}
              </div>
            </div>
          </form>

          {datasetWorkflow && (
            <WorkflowPlanCard
              workflow={datasetWorkflow}
              action={workflowAction}
              onApprovePlan={() => void approvePlan()}
              onDecision={(decision) => void decideWorkflowAnalysis(decision)}
              onAcceptWarnings={() => void acceptReviewWarnings()}
            />
          )}
          {workflow && !datasetWorkflow && <AgentRoutingCard workflow={workflow} />}

          {displayedRuns.length === 0 && (
            <RunHistory runs={displayedRuns} loading={loadingProject} refreshing={refreshingRuns} onRefresh={() => void refreshRuns()} />
          )}
        </main>
      </div>
    </div>
  );
}

function HealthBadge({ health, loading }: { health: ScienceCoreHealth | null; loading: boolean }) {
  const { t } = useTranslation("pages");
  const ready = health?.database === "ok" && health.runtime === "ready";
  return (
    <div className="flex shrink-0 items-center gap-1.5 rounded-full border border-border bg-surface px-2.5 py-1 text-[11px] text-muted">
      {loading ? (
        <Loader2 size={11} className="animate-spin" />
      ) : (
        <span className={cn("h-1.5 w-1.5 rounded-full", ready ? "bg-ok" : "bg-error")} />
      )}
      {loading
        ? t("analysis.health.connecting", { defaultValue: "Connecting…" })
        : ready
          ? t("analysis.health.ready", { defaultValue: "Science core ready" })
          : t("analysis.health.offline", { defaultValue: "Science core offline" })}
    </div>
  );
}

function WorkflowPlanCard({
  workflow,
  action,
  onApprovePlan,
  onDecision,
  onAcceptWarnings,
}: {
  workflow: DatasetAnalysisWorkflowSnapshot | AgentResearchWorkflowSnapshot;
  action: string | null;
  onApprovePlan: () => void;
  onDecision: (decision: "approved" | "rejected") => void;
  onAcceptWarnings: () => void;
}) {
  const { t } = useTranslation("pages");
  const { analysisIntent, analysisSpec, datasetProfile } = workflow;
  const status = workflow.workflow.status;
  const planWaiting = hasWorkflowAction(workflow, "approve-plan");
  const executionWaiting =
    analysisIntent?.status === "waiting-approval" &&
    hasWorkflowAction(workflow, "approve-analysis");
  const processing = ["routing", "planning", "running", "reviewing"].includes(status);
  const terminal = ["completed", "failed", "blocked", "cancelled", "unsupported"].includes(
    status,
  );

  if (terminal && status === "completed") {
    return (
      <details className="group rounded-card border border-border bg-surface">
        <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-xs text-muted hover:bg-surface-2/50 hover:text-text">
          <CheckCircle2 size={14} className="text-ok" />
          <span className="font-medium">
            {t("analysis.agentWorkflowRecord", { defaultValue: "Agent analysis record" })}
          </span>
          <span className="ml-auto text-ok">
            {t("analysis.runStatus.completed", { defaultValue: "Completed" })}
          </span>
          <ChevronRight size={13} className="ml-1 transition-transform group-open:rotate-90" />
        </summary>
        <div className="grid gap-4 border-t border-border-faint px-4 py-4 text-xs md:grid-cols-3">
          <div>
            <p className="text-[10px] font-medium uppercase tracking-wider text-muted">
              {t("analysis.planMethod", { defaultValue: "Method" })}
            </p>
            <p className="mt-1 text-text">
              {analysisSpec ? analysisMethodLabel(analysisSpec.spec, t) : "—"}
            </p>
          </div>
          <div>
            <p className="text-[10px] font-medium uppercase tracking-wider text-muted">
              {t("analysis.planDatasetProfile", { defaultValue: "Dataset profile" })}
            </p>
            <p className="mt-1 text-text">
              {datasetProfile
                ? t("analysis.planDatasetDimensions", {
                    defaultValue: "{{rows}} rows · {{columns}} columns",
                    rows: datasetProfile.rowCount,
                    columns: datasetProfile.columnCount,
                  })
                : "—"}
            </p>
          </div>
          <div>
            <p className="text-[10px] font-medium uppercase tracking-wider text-muted">
              {t("analysis.payloadHash", { defaultValue: "Payload SHA-256" })}
            </p>
            <p className="mt-1 break-all font-mono text-[10px] text-text">
              {analysisIntent?.payloadSha256 ?? "—"}
            </p>
          </div>
        </div>
      </details>
    );
  }

  if (terminal || status === "waiting-clarification") {
    return (
      <section className="rounded-card border border-error/25 bg-surface p-4">
        <div className="flex items-start gap-3">
          <AlertTriangle size={16} className="mt-0.5 shrink-0 text-error" />
          <div>
            <h2 className="text-sm font-medium text-text">
              {status === "waiting-clarification"
                ? t("analysis.clarificationNeeded", {
                    defaultValue: "The analysis question needs clarification",
                  })
                : t("analysis.workflowStopped", {
                    defaultValue: "The analysis workflow stopped",
                  })}
            </h2>
            <p className="mt-1 text-xs leading-relaxed text-muted">
              {workflow.workflow.blockingReason?.userMessage ??
                t("analysis.reviseObjectiveHint", {
                  defaultValue:
                    "Revise the question so it clearly requests descriptive statistics, a two-group comparison, or a correlation.",
                })}
            </p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="overflow-hidden rounded-card border border-border bg-surface shadow-card">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        {processing ? (
          <Loader2 size={16} className="animate-spin text-accent" />
        ) : (
          <Code2 size={16} className="text-accent" />
        )}
        <h2 className="text-sm font-medium text-text">
          {executionWaiting
            ? t("analysis.approvalHeading", {
                defaultValue: "High-risk execution approval",
              })
            : t("analysis.agentPlanHeading", { defaultValue: "Spark analysis plan" })}
        </h2>
        <WorkflowStatusBadge status={status} />
      </div>
      <div className="p-4">
        {analysisSpec ? (
          <>
            <p className="text-sm font-medium text-text">{analysisSpec.spec.objective}</p>
            <dl className="mt-4 grid gap-3 md:grid-cols-3">
              <PolicyCell
                icon={<Database size={14} />}
                label={t("analysis.planDatasetProfile", {
                  defaultValue: "Dataset profile",
                })}
                value={
                  datasetProfile
                    ? t("analysis.planDatasetDimensions", {
                        defaultValue: "{{rows}} rows · {{columns}} columns",
                        rows: datasetProfile.rowCount,
                        columns: datasetProfile.columnCount,
                      })
                    : t("analysis.planProfilePending", {
                        defaultValue: "Profile captured after plan approval",
                      })
                }
              />
              <PolicyCell
                icon={<Code2 size={14} />}
                label={t("analysis.planMethod", { defaultValue: "Method" })}
                value={analysisMethodLabel(analysisSpec.spec, t)}
              />
              <PolicyCell
                icon={<BarChart3 size={14} />}
                label={t("analysis.planOutput", { defaultValue: "Output" })}
                value={analysisOutputLabel(analysisSpec.spec, t)}
              />
            </dl>
            <div className="mt-3 rounded-input border border-border bg-bg px-3 py-2.5">
              <p className="text-[10px] font-medium uppercase tracking-wider text-muted">
                {t("analysis.planColumns", { defaultValue: "Bound columns" })}
              </p>
              <p className="mt-1.5 text-xs text-text">
                {analysisColumns(analysisSpec.spec).join(", ")}
              </p>
              <p className="mt-2 text-[11px] leading-relaxed text-muted">
                {analysisSelectionReason(analysisSpec.spec, t)}
              </p>
            </div>
          </>
        ) : (
          <div className="flex items-center gap-2 py-4 text-xs text-muted">
            <Loader2 size={13} className="animate-spin text-accent" />
            {t("analysis.agentPlanning", {
              defaultValue: "Profiling the dataset and selecting a bounded method…",
            })}
          </div>
        )}

        {executionWaiting && analysisIntent && (
          <div className="mt-4 border-t border-border-faint pt-4">
            <p className="text-xs leading-relaxed text-muted">
              {t("analysis.approvalBody", {
                defaultValue:
                  "Review the exact immutable payload below. Approval does not cover later edits or a different dataset.",
              })}
            </p>
            <dl className="mt-3 grid gap-3 sm:grid-cols-2">
              <PolicyCell
                icon={<Hash size={14} />}
                label={t("analysis.payloadHash", { defaultValue: "Payload SHA-256" })}
                value={analysisIntent.payloadSha256}
                mono
                wide
              />
              <PolicyCell
                icon={<ShieldAlert size={14} />}
                label={t("analysis.risk", { defaultValue: "Risk" })}
                value={t("analysis.riskHigh", { defaultValue: "High" })}
                warn
              />
              <PolicyCell
                icon={<NotebookPen size={14} />}
                label={t("analysis.runtime", { defaultValue: "Runtime" })}
                value={t("analysis.jupyterRuntime", { defaultValue: "Isolated Jupyter" })}
              />
            </dl>
            <details className="mt-3 rounded-input border border-border-faint bg-bg">
              <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5 text-xs text-muted hover:text-text">
                <Code2 size={13} />
                {t("analysis.compiledMethod", {
                  defaultValue: "Policy-compiled Python",
                })}
              </summary>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words border-t border-border-faint bg-[#17161b] p-3 font-mono text-[10px] leading-4 text-[#d8d4cc]">
                {analysisIntent.code}
              </pre>
            </details>
          </div>
        )}

        {workflow.latestReview?.reviewType === "deterministic-analysis-v1" &&
          workflow.latestReview.verdict === "passed-with-warnings" && (
          <div className="mt-4 rounded-input border border-warn/30 bg-warn/5 p-3">
            <p className="text-xs font-medium text-text">
              {t("analysis.reviewWarnings", {
                defaultValue: "Review completed with warnings",
              })}
            </p>
            <ul className="mt-2 list-disc space-y-1 pl-4 text-[11px] text-muted">
              {workflow.latestReview.result.methodWarnings.map((warning) => (
                <li key={warning.code}>{warning.message}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center justify-end gap-2 border-t border-border-faint pt-3">
          {planWaiting && (
            <button
              type="button"
              onClick={onApprovePlan}
              disabled={action !== null}
              className="flex items-center gap-1.5 rounded-input bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-40"
            >
              {action === "approve-plan" ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <CheckCircle2 size={13} />
              )}
              {t("analysis.approvePlan", { defaultValue: "Approve analysis plan" })}
            </button>
          )}
          {executionWaiting && (
            <>
              <button
                type="button"
                onClick={() => onDecision("rejected")}
                disabled={action !== null}
                className="flex items-center gap-1.5 rounded-input border border-border px-3 py-1.5 text-xs text-text hover:bg-surface-2 disabled:opacity-40"
              >
                {action === "rejected" ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <XCircle size={13} />
                )}
                {t("analysis.reject", { defaultValue: "Reject" })}
              </button>
              <button
                type="button"
                onClick={() => onDecision("approved")}
                disabled={action !== null}
                className="flex items-center gap-1.5 rounded-input bg-warn px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-40"
              >
                {action === "approved" ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <ShieldAlert size={13} />
                )}
                {t("analysis.approve", { defaultValue: "Approve exact payload" })}
              </button>
            </>
          )}
          {/* Internal action identifier, never shown to the user. */}
          {/* eslint-disable-next-line i18next/no-literal-string */}
          {hasWorkflowAction(workflow, "accept-review-warnings") && (
            <button
              type="button"
              onClick={onAcceptWarnings}
              disabled={action !== null}
              className="rounded-input bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg disabled:opacity-40"
            >
              {t("analysis.acceptWarnings", {
                defaultValue: "Accept review warnings",
              })}
            </button>
          )}
          {processing && !executionWaiting && (
            <p className="text-xs text-muted">
              {t("analysis.agentRunning", {
                defaultValue: "Spark is continuing the approved workflow.",
              })}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

function AgentRoutingCard({ workflow }: { workflow: ResearchWorkflowSnapshot }) {
  const { t } = useTranslation("pages");
  return (
    <section className="rounded-card border border-border bg-surface p-4 shadow-card">
      <div className="flex items-center gap-2">
        <Loader2 size={15} className="animate-spin text-accent" />
        <h2 className="text-sm font-medium text-text">
          {t("analysis.agentPlanHeading", { defaultValue: "Spark analysis plan" })}
        </h2>
        <WorkflowStatusBadge status={workflow.workflow.status} />
      </div>
      <p className="mt-3 text-xs leading-relaxed text-muted">
        {t("analysis.agentRouting", {
          defaultValue:
            "Spark is interpreting the objective, identifying the dataset workflow, and selecting a supported analysis method.",
        })}
      </p>
    </section>
  );
}

function analysisMethodLabel(
  spec: NonNullable<DatasetAnalysisWorkflowSnapshot["analysisSpec"]>["spec"],
  t: TFunction<"pages">,
): string {
  const operation = spec.operation;
  if (operation.type === "descriptive") {
    return t("analysis.method.descriptive", { defaultValue: "Descriptive statistics" });
  }
  if (operation.type === "correlation") {
    return operation.method === "auto"
      ? t("analysis.method.correlationAuto", { defaultValue: "Correlation · automatic" })
      : t("analysis.method.correlation", {
          defaultValue: "Correlation · {{method}}",
          method: operation.method,
        });
  }
  return operation.method === "auto"
    ? t("analysis.method.comparisonAuto", {
        defaultValue: "Two-group comparison · automatic",
      })
    : t("analysis.method.comparison", {
        defaultValue: "Two-group comparison · {{method}}",
        method: operation.method,
      });
}

function analysisColumns(
  spec: NonNullable<DatasetAnalysisWorkflowSnapshot["analysisSpec"]>["spec"],
): string[] {
  const operation = spec.operation;
  if (operation.type === "descriptive") return operation.columns;
  if (operation.type === "correlation") return [operation.xColumn, operation.yColumn];
  return [operation.groupColumn, operation.outcomeColumn];
}

function analysisOutputLabel(
  spec: NonNullable<DatasetAnalysisWorkflowSnapshot["analysisSpec"]>["spec"],
  t: TFunction<"pages">,
): string {
  const plot = spec.operation.plot;
  return plot === "none"
    ? t("analysis.output.table", { defaultValue: "Structured result + table" })
    : t("analysis.output.plot", {
        defaultValue: "Structured result + {{plot}}",
        plot: t(`analysis.plot.${plot}`, { defaultValue: plot }),
      });
}

function analysisSelectionReason(
  spec: NonNullable<DatasetAnalysisWorkflowSnapshot["analysisSpec"]>["spec"],
  t: TFunction<"pages">,
): string {
  if (spec.operation.type === "correlation") {
    return t("analysis.selectionReason.correlation", {
      defaultValue:
        "Spark identified two numeric variables and selected a correlation test. Correlation does not establish causation.",
    });
  }
  if (spec.operation.type === "two-group-comparison") {
    return t("analysis.selectionReason.comparison", {
      defaultValue:
        "Spark identified a grouping variable and a numeric outcome for a bounded two-group comparison.",
    });
  }
  return t("analysis.selectionReason.descriptive", {
    defaultValue:
      "Spark selected descriptive statistics to summarize the requested variables without making inferential claims.",
  });
}

function WorkflowStatusBadge({
  status,
}: {
  status: DatasetAnalysisWorkflowSnapshot["workflow"]["status"];
}) {
  const { t } = useTranslation("pages");
  const tone =
    status === "completed"
      ? "bg-ok/10 text-ok ring-ok/20"
      : status === "failed" || status === "blocked" || status === "cancelled"
        ? "bg-error/10 text-error ring-error/20"
        : "bg-accent/10 text-accent ring-accent/20";
  return (
    <span className={cn("ml-auto rounded-full px-2 py-0.5 text-[10px] font-medium ring-1", tone)}>
      {t(`analysis.workflowStatus.${status}`, { defaultValue: status })}
    </span>
  );
}

function PolicyCell({
  icon,
  label,
  value,
  mono = false,
  wide = false,
  warn = false,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  mono?: boolean;
  wide?: boolean;
  warn?: boolean;
}) {
  return (
    <div className={cn("rounded-input border border-border bg-bg px-3 py-2.5", wide && "sm:col-span-2")}>
      <dt className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-muted">
        {icon} {label}
      </dt>
      <dd className={cn("mt-1 break-all text-xs text-text", mono && "font-mono", warn && "font-medium text-warn")}>
        {value}
      </dd>
    </div>
  );
}

function RunHistory({
  runs,
  loading,
  refreshing,
  onRefresh,
}: {
  runs: AnalysisRun[];
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  const { t } = useTranslation("pages");
  return (
    <section className="rounded-card border border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <ScrollText size={15} className="text-muted" />
        <h2 className="text-sm font-medium text-text">
          {t("analysis.runsHeading", {
            defaultValue: "Analysis runs ({{count}})",
            count: runs.length,
          })}
        </h2>
        <button
          type="button"
          onClick={onRefresh}
          disabled={loading || refreshing}
          className="ml-auto rounded p-1 text-muted hover:bg-surface-2 hover:text-text disabled:opacity-40"
          aria-label={t("analysis.refreshRunsAria", { defaultValue: "Refresh analysis runs" })}
        >
          <RefreshCw size={13} className={cn((loading || refreshing) && "animate-spin")} />
        </button>
      </div>
      <div className="p-4">
        {loading && runs.length === 0 && (
          <div className="flex items-center gap-2 py-4 text-xs text-muted">
            <Loader2 size={13} className="animate-spin" />
            {t("analysis.loadingRuns", { defaultValue: "Loading runs…" })}
          </div>
        )}
        {!loading && runs.length === 0 && (
          <div className="py-5 text-center">
            <FileOutput size={20} className="mx-auto text-muted" />
            <p className="mt-2 text-sm font-medium text-text">
              {t("analysis.noRunsTitle", { defaultValue: "No analysis runs yet" })}
            </p>
            <p className="mt-1 text-xs text-muted">
              {t("analysis.noRunsBody", { defaultValue: "Prepare, approve, and execute an intent to create a run." })}
            </p>
          </div>
        )}
      </div>
    </section>
  );
}

type InspectorTab = "artifact" | "method" | "run";

interface ArtifactPreview {
  artifactId: string;
  text: string | null;
  url: string | null;
}

function artifactPriority(artifact: AnalysisArtifact): number {
  if (artifact.mimeType.startsWith("image/")) return 0;
  if (
    artifact.mimeType === "text/csv" ||
    artifact.mimeType === "text/tab-separated-values"
  ) {
    return 1;
  }
  if (artifact.artifactType.startsWith("notebook")) return 2;
  if (artifact.mimeType.startsWith("text/") || artifact.mimeType === "application/json") {
    return 3;
  }
  return 4;
}

function isTextPreview(artifact: AnalysisArtifact): boolean {
  return (
    artifact.mimeType.startsWith("text/") ||
    artifact.mimeType === "application/json" ||
    artifact.artifactType.startsWith("notebook")
  );
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

function AnalysisResultsWorkspace({
  runs,
  loading,
  refreshing,
  onRefresh,
  structuredResult,
  onFollowUp,
}: {
  runs: AnalysisRun[];
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  structuredResult: WorkflowStructuredAnalysisResult | null;
  onFollowUp: (objective?: string) => void;
}) {
  const { t } = useTranslation("pages");
  const [selectedRunId, setSelectedRunId] = useState(runs[0]?.id ?? "");
  const selectedRun = runs.find((run) => run.id === selectedRunId) ?? runs[0];
  const orderedArtifacts = useMemo(
    () => [...(selectedRun?.artifacts ?? [])].sort((a, b) => artifactPriority(a) - artifactPriority(b)),
    [selectedRun],
  );
  const [selectedArtifactId, setSelectedArtifactId] = useState(orderedArtifacts[0]?.id ?? "");
  const selectedArtifact =
    orderedArtifacts.find((artifact) => artifact.id === selectedArtifactId) ?? orderedArtifacts[0] ?? null;
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("artifact");
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [followUp, setFollowUp] = useState("");

  useEffect(() => {
    if (!runs.some((run) => run.id === selectedRunId)) setSelectedRunId(runs[0]?.id ?? "");
  }, [runs, selectedRunId]);

  useEffect(() => {
    if (!orderedArtifacts.some((artifact) => artifact.id === selectedArtifactId)) {
      setSelectedArtifactId(orderedArtifacts[0]?.id ?? "");
    }
  }, [orderedArtifacts, selectedArtifactId]);

  useEffect(() => {
    if (!selectedArtifact) {
      setPreview(null);
      setPreviewError(null);
      return;
    }
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setPreviewing(true);
    setPreviewError(null);
    setPreview(null);
    void scienceCore
      .fetchArtifactBlob(selectedArtifact.id)
      .then(async (blob) => {
        if (controller.signal.aborted) return;
        if (selectedArtifact.mimeType.startsWith("image/")) {
          objectUrl = URL.createObjectURL(blob);
          setPreview({ artifactId: selectedArtifact.id, text: null, url: objectUrl });
        } else if (isTextPreview(selectedArtifact)) {
          const text = await blob.text();
          if (controller.signal.aborted) return;
          setPreview({
            artifactId: selectedArtifact.id,
            text,
            url: null,
          });
        } else {
          objectUrl = URL.createObjectURL(blob);
          setPreview({ artifactId: selectedArtifact.id, text: null, url: objectUrl });
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) setPreviewError(errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setPreviewing(false);
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [selectedArtifact]);

  if (!selectedRun) return null;
  const active = selectedRun.status === "pending" || selectedRun.status === "running";

  return (
    <section className="overflow-hidden rounded-card border border-border bg-surface shadow-card">
      <header className="flex min-h-14 items-center gap-3 border-b border-border px-5">
        <div className="flex h-8 w-8 items-center justify-center rounded-input bg-accent/10 text-accent">
          <BarChart3 size={16} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-muted">
            {t("analysis.resultHeading", { defaultValue: "Latest analysis result" })}
          </p>
          <h2 className="mt-0.5 truncate text-sm font-medium text-text">
            {selectedRun.objective ||
              t("analysis.runFallbackTitle", { defaultValue: "Dataset analysis" })}
          </h2>
        </div>
        <RunStatus status={selectedRun.status} />
        <button
          type="button"
          onClick={onRefresh}
          disabled={loading || refreshing}
          className="rounded p-1.5 text-muted hover:bg-surface-2 hover:text-text disabled:opacity-40"
          aria-label={t("analysis.refreshRunsAria", { defaultValue: "Refresh analysis runs" })}
        >
          <RefreshCw size={14} className={cn((loading || refreshing) && "animate-spin")} />
        </button>
      </header>

      <div className="grid min-w-0 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="min-w-0 border-b border-border lg:border-b-0 lg:border-r">
          {selectedRun.error && (
            <div className="flex items-start gap-2 border-b border-error/20 bg-error/5 px-5 py-3 text-xs text-error">
              <XCircle size={13} className="mt-0.5 shrink-0" />
              <span className="break-words">{selectedRun.error}</span>
            </div>
          )}
          {structuredResult?.runId === selectedRun.id && (
            <StructuredFindings
              result={structuredResult.result}
              artifacts={orderedArtifacts}
              onSelectArtifact={(artifactId) => {
                setSelectedArtifactId(artifactId);
                // Internal tab identifier, never shown to the user.
                // eslint-disable-next-line i18next/no-literal-string
                setInspectorTab("artifact");
              }}
            />
          )}
          {active ? (
            <div className="flex min-h-72 items-center justify-center p-8">
              <div className="max-w-sm text-center">
                <Loader2 size={13} className="shrink-0 animate-spin text-warn" />
                <p className="mt-3 text-sm font-medium text-text">
                  {t("analysis.runInProgressTitle", { defaultValue: "Analysis in progress" })}
                </p>
                <p className="mt-1 text-xs leading-relaxed text-muted">
                  {t("analysis.runInProgress", {
                    defaultValue: "Spark is running the approved analysis. Results will appear here.",
                  })}
                </p>
              </div>
            </div>
          ) : selectedArtifact ? (
            <>
              <div className="flex items-center gap-2 border-b border-border-faint px-5 py-3">
                <span className="text-muted">{artifactIcon(selectedArtifact)}</span>
                <p className="min-w-0 flex-1 truncate text-xs font-medium text-text">
                  {fileName(selectedArtifact.path)}
                </p>
                <span className="text-[10px] text-muted">{formatBytes(selectedArtifact.sizeBytes)}</span>
              </div>
              <ArtifactResultPreview
                artifact={selectedArtifact}
                preview={preview}
                loading={previewing}
                error={previewError}
              />
            </>
          ) : (
            <div className="flex min-h-72 items-center justify-center p-8 text-center">
              <div>
                <FileOutput size={22} className="mx-auto text-muted" />
                <p className="mt-2 text-sm font-medium text-text">
                  {selectedRun.status === "failed"
                    ? t("analysis.noArtifactsFailed", {
                        defaultValue: "No artifacts were accepted from the failed run.",
                      })
                    : t("analysis.noArtifacts", { defaultValue: "No artifacts reported yet." })}
                </p>
              </div>
            </div>
          )}

          {orderedArtifacts.length > 0 && (
            <div className="border-t border-border px-5 py-4">
              <p className="mb-2 text-[10px] font-medium uppercase tracking-[0.14em] text-muted">
                {t("analysis.artifacts", {
                  defaultValue: "Artifacts ({{count}})",
                  count: orderedArtifacts.length,
                })}
              </p>
              <div className="flex gap-2 overflow-x-auto pb-1">
                {orderedArtifacts.map((artifact) => (
                  <button
                    key={artifact.id}
                    type="button"
                    onClick={() => {
                      setSelectedArtifactId(artifact.id);
                      setInspectorTab("artifact");
                    }}
                    aria-pressed={artifact.id === selectedArtifact?.id}
                    className={cn(
                      "flex min-w-40 max-w-56 items-center gap-2 rounded-input border px-3 py-2 text-left transition-colors",
                      artifact.id === selectedArtifact?.id
                        ? "border-accent/40 bg-accent/5"
                        : "border-border-faint bg-bg hover:border-border hover:bg-surface-2",
                    )}
                  >
                    {artifactIcon(artifact)}
                    <span className="min-w-0">
                      <span className="block truncate text-xs font-medium text-text">
                        {fileName(artifact.path)}
                      </span>
                      <span className="block truncate text-[10px] text-muted">
                        {artifact.artifactType}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {selectedRun.status === "completed" && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                const next = followUp.trim();
                if (!next) return;
                onFollowUp(next);
                setFollowUp("");
              }}
              className="border-t border-border px-5 py-4"
            >
              <label htmlFor="analysis-follow-up" className="text-xs font-medium text-text">
                {t("analysis.followUpHeading", {
                  defaultValue: "Continue from this result",
                })}
              </label>
              <div className="mt-2 flex gap-2">
                <input
                  id="analysis-follow-up"
                  value={followUp}
                  onChange={(event) => setFollowUp(event.target.value)}
                  placeholder={t("analysis.followUpPlaceholder", {
                    defaultValue: "For example: compare the trend before and after 1980",
                  })}
                  className="min-w-0 flex-1 rounded-input border border-border bg-bg px-3 py-2 text-sm text-text outline-none placeholder:text-muted focus:border-accent"
                />
                <button
                  type="submit"
                  disabled={!followUp.trim()}
                  className="flex shrink-0 items-center gap-1.5 rounded-input bg-accent px-3 py-2 text-xs font-medium text-accent-fg disabled:opacity-40"
                >
                  <Play size={12} />
                  {t("analysis.createFollowUpPlan", {
                    defaultValue: "Create follow-up plan",
                  })}
                </button>
              </div>
              <p className="mt-1.5 text-[10px] text-muted">
                {t("analysis.followUpHint", {
                  defaultValue:
                    "Spark will create a new immutable plan; this completed run remains unchanged.",
                })}
              </p>
            </form>
          )}
        </div>

        <aside className="min-w-0 bg-bg/40">
          <div className="flex border-b border-border px-3 pt-2" role="tablist">
            {/* eslint-disable-next-line i18next/no-literal-string -- internal tab identifiers */}
            {(["artifact", "method", "run"] as const).map((tab) => (
              <button
                key={tab}
                type="button"
                role="tab"
                aria-selected={inspectorTab === tab}
                onClick={() => setInspectorTab(tab)}
                className={cn(
                  "border-b-2 px-3 py-2 text-xs font-medium transition-colors",
                  inspectorTab === tab
                    ? "border-accent text-text"
                    : "border-transparent text-muted hover:text-text",
                )}
              >
                {t(`analysis.inspector.${tab}`, {
                  defaultValue: tab === "artifact" ? "Artifact" : tab === "method" ? "Method" : "Run",
                })}
              </button>
            ))}
          </div>
          <div className="max-h-[38rem] overflow-auto p-4">
            {inspectorTab === "artifact" && (
              <ArtifactInspector artifact={selectedArtifact} />
            )}
            {inspectorTab === "method" && (
              <div>
                <InspectorLabel>
                  {t("analysis.inspector.python", { defaultValue: "Python method" })}
                </InspectorLabel>
                <pre className="mt-2 max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-input bg-[#17161b] p-3 font-mono text-[10px] leading-4 text-[#d8d4cc]">
                  {selectedRun.code}
                </pre>
              </div>
            )}
            {inspectorTab === "run" && <RunInspector run={selectedRun} />}
          </div>
        </aside>
      </div>

      {runs.length > 1 && (
        <details className="group border-t border-border">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-3 text-xs font-medium text-muted hover:bg-surface-2/50 hover:text-text">
            <ChevronRight size={13} className="transition-transform group-open:rotate-90" />
            {t("analysis.previousRuns", {
              defaultValue: "Previous runs ({{count}})",
              count: runs.length - 1,
            })}
          </summary>
          <div className="divide-y divide-border-faint border-t border-border-faint">
            {runs.filter((run) => run.id !== selectedRun.id).map((run) => (
              <button
                key={run.id}
                type="button"
                onClick={() => {
                  setSelectedRunId(run.id);
                  setInspectorTab("artifact");
                }}
                className="flex w-full items-center gap-3 px-5 py-3 text-left hover:bg-surface-2/50"
              >
                <RunStatus status={run.status} />
                <span className="min-w-0 flex-1 truncate text-xs font-medium text-text">
                  {run.objective ||
                    t("analysis.runFallbackTitle", { defaultValue: "Dataset analysis" })}
                </span>
                <span className="text-[10px] text-muted">
                  {formatTime(run.finishedAt ?? run.createdAt)}
                </span>
              </button>
            ))}
          </div>
        </details>
      )}
    </section>
  );
}

function formatResultNumber(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  if (Math.abs(value) >= 1_000 || (Math.abs(value) > 0 && Math.abs(value) < 0.001)) {
    return value.toExponential(3);
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function StructuredFindings({
  result,
  artifacts,
  onSelectArtifact,
}: {
  result: StructuredAnalysisResult;
  artifacts: AnalysisArtifact[];
  onSelectArtifact: (artifactId: string) => void;
}) {
  const { t } = useTranslation("pages");
  const resultArtifact =
    artifacts.find((artifact) => fileName(artifact.path) === "results.json") ??
    artifacts.find((artifact) => fileName(artifact.path) === "summary.csv") ??
    null;
  const tableArtifact =
    artifacts.find((artifact) => fileName(artifact.path) === "summary.csv") ??
    resultArtifact;

  const findings: Array<{ key: string; text: string; artifact: AnalysisArtifact | null }> = [];
  if (result.result.type === "descriptive") {
    for (const column of result.result.columns.slice(0, 4)) {
      const values = ["mean", "median", "min", "max"]
        .flatMap((name) => {
          const value = column.statistics[name];
          return typeof value === "number"
            ? [
                t("analysis.finding.statistic", {
                  defaultValue: "{{name}} {{value}}",
                  name,
                  value: formatResultNumber(value),
                }),
              ]
            : [];
        })
        .join(" · ");
      findings.push({
        key: column.column,
        text: t("analysis.finding.descriptive", {
          defaultValue:
            "{{column}}: {{sample}} observations, {{missing}} missing{{statistics}}.",
          column: column.column,
          sample: column.sampleSize,
          missing: column.missingCount,
          statistics: values ? ` · ${values}` : "",
        }),
        artifact: tableArtifact,
      });
    }
  } else if (result.result.type === "correlation") {
    findings.push({
      key: "correlation",
      text: t("analysis.finding.correlation", {
        defaultValue:
          "{{x}} and {{y}}: {{method}} correlation {{correlation}}, p={{p}}, n={{sample}}. Correlation does not establish causation.",
        x: result.result.xColumn,
        y: result.result.yColumn,
        method: result.resolvedMethod,
        correlation: formatResultNumber(result.result.correlation),
        p: formatResultNumber(result.result.pValue),
        sample: result.result.sampleSize,
      }),
      artifact: resultArtifact,
    });
  } else {
    const [first, second] = result.result.groups;
    findings.push({
      key: "comparison",
      text: t("analysis.finding.comparison", {
        defaultValue:
          "{{first}} vs {{second}} on {{outcome}}: p={{p}}, {{effectName}}={{effect}}, 95% CI [{{low}}, {{high}}].",
        first,
        second,
        outcome: result.result.outcomeColumn,
        p: formatResultNumber(result.result.pValue),
        effectName: result.result.effectSizeName,
        effect: formatResultNumber(result.result.effectSize),
        low: formatResultNumber(result.result.confidenceInterval[0]),
        high: formatResultNumber(result.result.confidenceInterval[1]),
      }),
      artifact: resultArtifact,
    });
  }

  return (
    <section className="border-b border-border bg-accent/[0.035] px-5 py-4">
      <div className="flex items-center gap-2">
        <CheckCircle2 size={14} className="text-ok" />
        <h3 className="text-xs font-semibold text-text">
          {t("analysis.evidenceFindings", {
            defaultValue: "Findings grounded in this run",
          })}
        </h3>
        <span className="ml-auto text-[10px] text-muted">
          {t("analysis.reviewedStructuredResult", {
            defaultValue: "Structured result · deterministic review",
          })}
        </span>
      </div>
      <ul className="mt-3 space-y-2">
        {findings.map((finding) => (
          <li key={finding.key} className="flex items-start gap-3 text-xs leading-relaxed">
            <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
            <span className="min-w-0 flex-1 text-text">{finding.text}</span>
            {finding.artifact && (
              <button
                type="button"
                onClick={() => onSelectArtifact(finding.artifact!.id)}
                className="shrink-0 rounded-full border border-border bg-surface px-2 py-0.5 text-[10px] text-muted hover:border-accent/30 hover:text-text"
              >
                {fileName(finding.artifact.path)}
              </button>
            )}
          </li>
        ))}
      </ul>
      {result.limitations.length > 0 && (
        <p className="mt-3 text-[10px] leading-relaxed text-muted">
          {t("analysis.limitationsPrefix", { defaultValue: "Limitation:" })}{" "}
          {result.result.type === "correlation"
            ? t("analysis.correlationLimitation", {
                defaultValue: "Correlation does not establish causation.",
              })
            : result.limitations.join(" ")}
        </p>
      )}
    </section>
  );
}

function artifactIcon(artifact: AnalysisArtifact) {
  if (artifact.artifactType.startsWith("notebook")) {
    return <NotebookPen size={14} className="shrink-0 text-muted" />;
  }
  if (artifact.mimeType.startsWith("image/")) {
    return <BarChart3 size={14} className="shrink-0 text-muted" />;
  }
  if (
    artifact.mimeType === "text/csv" ||
    artifact.mimeType === "text/tab-separated-values"
  ) {
    return <FileSpreadsheet size={14} className="shrink-0 text-muted" />;
  }
  return <FileOutput size={14} className="shrink-0 text-muted" />;
}

function ArtifactResultPreview({
  artifact,
  preview,
  loading,
  error,
}: {
  artifact: AnalysisArtifact;
  preview: ArtifactPreview | null;
  loading: boolean;
  error: string | null;
}) {
  const { t } = useTranslation("pages");
  if (loading) {
    return (
      <div className="flex min-h-72 items-center justify-center gap-2 text-xs text-muted">
        <Loader2 size={14} className="animate-spin" />
        {t("analysis.previewLoading", { defaultValue: "Loading result…" })}
      </div>
    );
  }
  if (error) {
    return <p className="min-h-72 break-words p-5 text-xs text-error">{error}</p>;
  }
  if (!preview || preview.artifactId !== artifact.id) return <div className="min-h-72" />;
  if (preview.url && artifact.mimeType.startsWith("image/")) {
    return (
      <div className="flex min-h-72 items-center justify-center bg-white p-5">
        <img
          src={preview.url}
          alt={fileName(artifact.path)}
          className="max-h-[34rem] max-w-full object-contain"
        />
      </div>
    );
  }
  if (
    preview.text != null &&
    (artifact.mimeType === "text/csv" ||
      artifact.mimeType === "text/tab-separated-values")
  ) {
    const table = parseTableFile(artifact.path, preview.text);
    return (
      <div className="max-h-[34rem] overflow-auto">
        <table className="min-w-full border-separate border-spacing-0 text-left text-xs">
          <thead className="sticky top-0 bg-surface-2 text-muted">
            <tr>
              {table.columns.map((column, index) => (
                <th key={`${column}:${index}`} className="border-b border-border px-4 py-2.5 font-medium">
                  {column ||
                    t("analysis.unnamedColumn", {
                      defaultValue: "Column {{index}}",
                      index: index + 1,
                    })}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.slice(0, 100).map((row, rowIndex) => (
              <tr key={rowIndex} className="even:bg-surface-2/55">
                {row.map((cell, cellIndex) => (
                  <td
                    key={cellIndex}
                    className="max-w-72 whitespace-pre-wrap break-words border-b border-border-faint px-4 py-2.5 text-text"
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  if (preview.text != null) {
    return (
      <pre className="max-h-[34rem] min-h-72 overflow-auto whitespace-pre-wrap break-words bg-[#17161b] p-5 font-mono text-[11px] leading-5 text-[#d8d4cc]">
        {preview.text}
      </pre>
    );
  }
  return (
    <div className="flex min-h-72 items-center justify-center p-8 text-center text-xs text-muted">
      {t("analysis.previewUnavailable", {
        defaultValue: "Preview is unavailable for this artifact type.",
      })}
    </div>
  );
}

function InspectorLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-muted">{children}</p>
  );
}

function ArtifactInspector({ artifact }: { artifact: AnalysisArtifact | null }) {
  const { t } = useTranslation("pages");
  if (!artifact) {
    return (
      <p className="text-xs text-muted">
        {t("analysis.noArtifactSelected", { defaultValue: "No artifact selected." })}
      </p>
    );
  }
  return (
    <dl className="space-y-4 text-xs">
      <div>
        <InspectorLabel>{t("analysis.inspector.file", { defaultValue: "File" })}</InspectorLabel>
        <dd className="mt-1 break-all font-medium text-text">{fileName(artifact.path)}</dd>
      </div>
      <div>
        <InspectorLabel>{t("analysis.inspector.type", { defaultValue: "Type" })}</InspectorLabel>
        <dd className="mt-1 text-text">{artifact.artifactType} · {artifact.mimeType}</dd>
      </div>
      <div>
        <InspectorLabel>{t("analysis.inspector.size", { defaultValue: "Size" })}</InspectorLabel>
        <dd className="mt-1 text-text">{formatBytes(artifact.sizeBytes)}</dd>
      </div>
      <div>
        <InspectorLabel>{t("analysis.inspector.created", { defaultValue: "Created" })}</InspectorLabel>
        <dd className="mt-1 text-text">{formatTime(artifact.createdAt)}</dd>
      </div>
      <div>
        <InspectorLabel>{t("analysis.inspector.hash", { defaultValue: "SHA-256" })}</InspectorLabel>
        <dd className="mt-1 break-all font-mono text-[10px] leading-4 text-text">
          {artifact.contentHash}
        </dd>
      </div>
    </dl>
  );
}

function RunInspector({ run }: { run: AnalysisRun }) {
  const { t } = useTranslation("pages");
  return (
    <div className="space-y-4 text-xs">
      <div className="flex items-center justify-between gap-3">
        <InspectorLabel>{t("analysis.inspector.status", { defaultValue: "Status" })}</InspectorLabel>
        <RunStatus status={run.status} />
      </div>
      <dl className="space-y-4">
        <div>
          <InspectorLabel>{t("analysis.inspector.runId", { defaultValue: "Run ID" })}</InspectorLabel>
          <dd className="mt-1 break-all font-mono text-[10px] text-text">{run.id}</dd>
        </div>
        <div>
          <InspectorLabel>{t("analysis.inspector.finished", { defaultValue: "Finished" })}</InspectorLabel>
          <dd className="mt-1 text-text">{formatTime(run.finishedAt ?? run.createdAt)}</dd>
        </div>
        {run.environmentHash && (
          <div>
            <InspectorLabel>{t("analysis.inspector.environment", { defaultValue: "Environment hash" })}</InspectorLabel>
            <dd className="mt-1 break-all font-mono text-[10px] text-text">{run.environmentHash}</dd>
          </div>
        )}
      </dl>
      <div>
        <InspectorLabel>{t("analysis.logs", { defaultValue: "Logs" })}</InspectorLabel>
        {run.logs ? (
          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-input bg-[#17161b] p-3 font-mono text-[10px] leading-4 text-[#d8d4cc]">
            {run.logs}
          </pre>
        ) : (
          <p className="mt-2 text-muted">
            {t("analysis.noLogs", { defaultValue: "No logs reported." })}
          </p>
        )}
      </div>
    </div>
  );
}

function RunStatus({ status }: { status: AnalysisRunStatus }) {
  const { t } = useTranslation("pages");
  const tone =
    status === "completed"
      ? "bg-ok/10 text-ok ring-ok/20"
      : status === "failed"
        ? "bg-error/10 text-error ring-error/20"
        : "bg-warn/10 text-warn ring-warn/20";
  return (
    <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium ring-1", tone)}>
      {t(`analysis.runStatus.${status}`, { defaultValue: status })}
    </span>
  );
}
