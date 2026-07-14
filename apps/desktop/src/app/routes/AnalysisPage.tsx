import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
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
  AnalysisIntent,
  AnalysisIntentStatus,
  AnalysisRun,
  AnalysisRunStatus,
  ResearchProject,
  ResearchSource,
  ScienceCoreHealth,
} from "@spark/research-domain";
import type { AnalysisIntentDecision } from "@spark/research-sdk";
import { cn } from "@/lib/cn";
import { scienceCore } from "@/lib/scienceCore";
import { toast } from "@/lib/toast";

const CSV_ACCEPT = ".csv,text/csv";

const DEFAULT_ANALYSIS_CODE = `from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

input_path = Path(DATASET_PATH)
output_dir = Path(RUN_DIR)

df = pd.read_csv(input_path)

summary = df.describe(include="all").transpose()
summary.to_csv(output_dir / "summary.csv")

numeric = df.select_dtypes(include="number")
fig, ax = plt.subplots(figsize=(8, 5))
if numeric.empty:
    ax.text(0.5, 0.5, "No numeric columns", ha="center", va="center")
    ax.set_axis_off()
else:
    column = numeric.columns[0]
    numeric[column].dropna().plot.hist(bins=30, ax=ax)
    ax.set_title(f"Distribution of {column}")
    ax.set_xlabel(str(column))
fig.tight_layout()
fig.savefig(output_dir / "figure.png", dpi=160)
plt.close(fig)
`;

const FORBIDDEN_SHELL_PATTERNS = [
  /(^|\n)\s*![^=]/,
  /(^|\n)\s*%%(?:bash|sh|script)\b/,
  /\b(?:import|from)\s+(?:subprocess|pty)\b/,
  /\bos\.(?:system|popen|spawn\w*)\s*\(/,
  /\bget_ipython\(\)\.(?:system|getoutput)\s*\(/,
  /\bshell\s*=\s*True\b/,
];

function hasShellExecution(code: string): boolean {
  return FORBIDDEN_SHELL_PATTERNS.some((pattern) => pattern.test(code));
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
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

/** Safe-by-default CSV analysis workflow backed by science-core approvals. */
export function AnalysisPage() {
  const { t } = useTranslation("pages");
  const fileInput = useRef<HTMLInputElement>(null);
  const [health, setHealth] = useState<ScienceCoreHealth | null>(null);
  const [projects, setProjects] = useState<ResearchProject[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [sources, setSources] = useState<ResearchSource[]>([]);
  const [datasetId, setDatasetId] = useState<string | null>(null);
  const [runs, setRuns] = useState<AnalysisRun[]>([]);
  const [objective, setObjective] = useState("");
  const [code, setCode] = useState(DEFAULT_ANALYSIS_CODE);
  const [intent, setIntent] = useState<AnalysisIntent | null>(null);
  const [projectTitle, setProjectTitle] = useState("");
  const [showProjectForm, setShowProjectForm] = useState(false);
  const [booting, setBooting] = useState(true);
  const [loadingProject, setLoadingProject] = useState(false);
  const [creatingProject, setCreatingProject] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [deciding, setDeciding] = useState<AnalysisIntentDecision | null>(null);
  const [executing, setExecuting] = useState(false);
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
        nextProjects.some((project) => project.id === current)
          ? current
          : nextProjects[0]?.id ?? null,
      );
    } catch (error) {
      setHealth(null);
      setPageError(errorMessage(error));
    } finally {
      setBooting(false);
    }
  }, []);

  useEffect(() => {
    void loadWorkspace();
  }, [loadWorkspace]);

  useEffect(() => {
    let cancelled = false;
    setIntent(null);
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
    ])
      .then(([nextSources, nextRuns]) => {
        if (cancelled) return;
        setSources(nextSources);
        setRuns(nextRuns);
        const datasets = nextSources.filter((source) => source.sourceKind === "dataset");
        setDatasetId(datasets[0]?.id ?? null);
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
  const shellViolation = hasShellExecution(code);
  const intentRun = intent ? runs.find((run) => run.intentId === intent.id) ?? null : null;
  const hasActiveRun = runs.some((run) => run.status === "pending" || run.status === "running");
  const proposalLocked = intent !== null;

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

  const intentId = intent?.id;
  useEffect(() => {
    if (!intentId) return;
    const run = runs.find((item) => item.intentId === intentId);
    if (!run) return;
    const nextStatus: AnalysisIntentStatus =
      run.status === "completed"
        ? "completed"
        : run.status === "failed"
          ? "failed"
          : "executing";
    setIntent((current) =>
      current && current.id === intentId && current.status !== nextStatus
        ? { ...current, status: nextStatus }
        : current,
    );
  }, [intentId, runs]);

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
      setIntent(null);
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

  const prepareIntent = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!projectId || !datasetId || !objective.trim() || !code.trim() || shellViolation) return;
    setPreparing(true);
    try {
      const prepared = await scienceCore.prepareAnalysisIntent(projectId, {
        datasetSourceId: datasetId,
        objective: objective.trim(),
        code,
      });
      setIntent(prepared);
      toast.success(
        t("analysis.toast.intentPrepared", {
          defaultValue: "Immutable execution intent prepared for review.",
        }),
      );
    } catch (error) {
      toast.error(
        t("analysis.toast.prepareFailed", {
          defaultValue: "Could not prepare intent: {{error}}",
          error: errorMessage(error),
        }),
      );
    } finally {
      setPreparing(false);
    }
  };

  const decide = async (decision: AnalysisIntentDecision) => {
    if (!intent || intent.status !== "waiting-approval") return;
    setDeciding(decision);
    try {
      const updated = await scienceCore.decideAnalysisIntent(intent.id, decision);
      setIntent(updated);
      if (updated.status === "approved") {
        toast.success(
          t("analysis.toast.approved", {
            defaultValue: "Intent approved. Execution is now available.",
          }),
        );
      } else {
        toast.success(
          t("analysis.toast.rejected", {
            defaultValue: "Intent rejected. Nothing was executed.",
          }),
        );
      }
    } catch (error) {
      toast.error(
        t("analysis.toast.decisionFailed", {
          defaultValue: "Could not record decision: {{error}}",
          error: errorMessage(error),
        }),
      );
    } finally {
      setDeciding(null);
    }
  };

  const execute = async () => {
    if (!intent || intent.status !== "approved" || intentRun) return;
    setExecuting(true);
    try {
      const run = await scienceCore.executeAnalysisIntent(intent.id);
      setRuns((current) => [run, ...current.filter((item) => item.id !== run.id)]);
      if (run.status === "completed") {
        toast.success(
          t("analysis.toast.completed", { defaultValue: "Analysis run completed." }),
        );
      } else if (run.status === "failed") {
        toast.error(
          run.error ?? t("analysis.toast.failed", { defaultValue: "Analysis run failed." }),
        );
      } else {
        toast.success(
          t("analysis.toast.started", { defaultValue: "Analysis run started." }),
        );
      }
    } catch (error) {
      toast.error(
        t("analysis.toast.executeFailed", {
          defaultValue: "Execution did not complete: {{error}}",
          error: errorMessage(error),
        }),
      );
      await refreshRuns(true);
    } finally {
      setExecuting(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto bg-bg">
      <div className="mx-auto max-w-6xl px-6 py-6 xl:px-8">
        <header className="flex items-start gap-4">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-card bg-accent/10 text-accent">
            <BarChart3 size={19} />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="font-serif text-xl text-text">
              {t("analysis.title", { defaultValue: "Safe data analysis" })}
            </h1>
            <p className="mt-1 text-sm text-muted">
              {t("analysis.subtitle", {
                defaultValue: "Prepare reproducible Python, approve its exact payload, then execute it in the isolated runtime.",
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

        <div className="mt-5 grid items-start gap-5 lg:grid-cols-[17rem_minmax(0,1fr)]">
          <aside className="space-y-4">
            <section className="rounded-card border border-border bg-surface p-4">
              <div className="flex items-center justify-between">
                <h2 className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted">
                  <Database size={14} />
                  {t("analysis.projectHeading", { defaultValue: "Research project" })}
                </h2>
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
                  className="mt-3 w-full rounded-input border border-border bg-bg px-2.5 py-1.5 text-[13px] text-text outline-none focus:border-accent"
                  aria-label={t("analysis.projectSelectAria", { defaultValue: "Research project" })}
                >
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.title}
                    </option>
                  ))}
                </select>
              ) : (
                !booting && (
                  <p className="mt-3 text-xs leading-relaxed text-muted">
                    {t("analysis.noProjects", { defaultValue: "Create a project to begin." })}
                  </p>
                )
              )}
              {(showProjectForm || (!booting && projects.length === 0)) && (
                <form onSubmit={(event) => void createProject(event)} className="mt-2 space-y-2">
                  <input
                    value={projectTitle}
                    onChange={(event) => setProjectTitle(event.target.value)}
                    placeholder={t("analysis.projectNamePlaceholder", { defaultValue: "Project name" })}
                    className="w-full rounded-input border border-border bg-bg px-2.5 py-1.5 text-[13px] text-text outline-none placeholder:text-muted focus:border-accent"
                  />
                  <button
                    type="submit"
                    disabled={!projectTitle.trim() || creatingProject || !serviceReady}
                    className="flex w-full items-center justify-center gap-1.5 rounded-input bg-accent px-2.5 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-40"
                  >
                    {creatingProject && <Loader2 size={12} className="animate-spin" />}
                    {t("analysis.createProject", { defaultValue: "Create project" })}
                  </button>
                </form>
              )}
              {selectedProject && (
                <p className="mt-2 truncate text-[10px] text-muted" title={selectedProject.projectPath}>
                  {selectedProject.projectPath}
                </p>
              )}
            </section>

            <section className="rounded-card border border-border bg-surface p-4">
              <h2 className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted">
                <FileSpreadsheet size={14} />
                {t("analysis.datasetHeading", { defaultValue: "Input dataset" })}
              </h2>
              {loadingProject ? (
                <div className="mt-3 flex items-center gap-2 text-xs text-muted">
                  <Loader2 size={13} className="animate-spin" />
                  {t("analysis.loadingDatasets", { defaultValue: "Loading datasets…" })}
                </div>
              ) : datasets.length > 0 ? (
                <select
                  value={datasetId ?? ""}
                  onChange={(event) => {
                    setDatasetId(event.target.value || null);
                    setIntent(null);
                  }}
                  disabled={proposalLocked}
                  className="mt-3 w-full rounded-input border border-border bg-bg px-2.5 py-1.5 text-[13px] text-text outline-none focus:border-accent disabled:opacity-60"
                  aria-label={t("analysis.datasetSelectAria", { defaultValue: "Dataset" })}
                >
                  {datasets.map((dataset) => (
                    <option key={dataset.id} value={dataset.id}>
                      {dataset.title}
                    </option>
                  ))}
                </select>
              ) : (
                <p className="mt-3 text-xs leading-relaxed text-muted">
                  {projectId
                    ? t("analysis.noDatasets", { defaultValue: "Upload a CSV dataset to continue." })
                    : t("analysis.selectProjectFirst", { defaultValue: "Select a project first." })}
                </p>
              )}
              {selectedDataset && (
                <div className="mt-2 rounded-input bg-bg px-2.5 py-2 text-[10px] text-muted ring-1 ring-border-faint">
                  <p className="truncate font-medium text-text">{selectedDataset.title}</p>
                  <p className="mt-1 truncate font-mono">{selectedDataset.contentHash}</p>
                </div>
              )}
              <input
                ref={fileInput}
                type="file"
                accept={CSV_ACCEPT}
                onChange={(event) => void uploadDataset(event)}
                className="hidden"
              />
              <button
                type="button"
                onClick={() => fileInput.current?.click()}
                disabled={!projectId || !serviceReady || uploading || proposalLocked}
                className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-input border border-border bg-bg px-2.5 py-1.5 text-xs text-text hover:bg-surface-2 disabled:opacity-40"
              >
                {uploading ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
                {uploading
                  ? t("analysis.uploading", { defaultValue: "Uploading…" })
                  : t("analysis.uploadCsv", { defaultValue: "Upload CSV" })}
              </button>
            </section>

            <section className="rounded-card border border-border bg-surface p-4 text-xs text-muted">
              <h2 className="flex items-center gap-2 font-medium uppercase tracking-wider">
                <ShieldAlert size={14} />
                {t("analysis.safetyHeading", { defaultValue: "Execution boundary" })}
              </h2>
              <ul className="mt-3 space-y-2 leading-relaxed">
                <li className="flex items-start gap-2">
                  <WifiOff size={13} className="mt-0.5 shrink-0" />
                  {t("analysis.safetyNoNetwork", { defaultValue: "External network access is denied." })}
                </li>
                <li className="flex items-start gap-2">
                  <NotebookPen size={13} className="mt-0.5 shrink-0" />
                  {t("analysis.safetyJupyter", { defaultValue: "Code runs through the isolated Jupyter runtime." })}
                </li>
                <li className="flex items-start gap-2">
                  <LockKeyhole size={13} className="mt-0.5 shrink-0" />
                  {t("analysis.safetyApproval", { defaultValue: "Approval applies only to the displayed payload hash." })}
                </li>
              </ul>
            </section>
          </aside>

          <main className="min-w-0 space-y-5">
            <form onSubmit={(event) => void prepareIntent(event)} className="rounded-card border border-border bg-surface shadow-card">
              <div className="flex items-center gap-2 border-b border-border px-4 py-3">
                <Code2 size={15} className="text-muted" />
                <h2 className="text-sm font-medium text-text">
                  {t("analysis.proposalHeading", { defaultValue: "Analysis proposal" })}
                </h2>
                {proposalLocked && (
                  <span className="ml-auto flex items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 text-[10px] text-muted ring-1 ring-border">
                    <LockKeyhole size={10} />
                    {t("analysis.locked", { defaultValue: "Locked" })}
                  </span>
                )}
              </div>
              <div className="space-y-4 p-4">
                <div>
                  <label htmlFor="analysis-objective" className="text-xs font-medium text-text">
                    {t("analysis.objectiveLabel", { defaultValue: "Objective" })}
                  </label>
                  <textarea
                    id="analysis-objective"
                    value={objective}
                    onChange={(event) => setObjective(event.target.value)}
                    disabled={proposalLocked}
                    rows={2}
                    placeholder={t("analysis.objectivePlaceholder", {
                      defaultValue: "Describe the question this analysis should answer.",
                    })}
                    className="mt-2 w-full resize-y rounded-input border border-border bg-bg px-3 py-2 text-sm leading-relaxed text-text outline-none placeholder:text-muted focus:border-accent disabled:opacity-60"
                  />
                </div>
                <div>
                  <div className="flex items-center justify-between gap-3">
                    <label htmlFor="analysis-code" className="text-xs font-medium text-text">
                      {t("analysis.codeLabel", { defaultValue: "Python code" })}
                    </label>
                    <span className="text-[10px] text-muted">
                      {t("analysis.inputContract", { defaultValue: "Input: DATASET_PATH · Output: RUN_DIR" })}
                    </span>
                  </div>
                  <textarea
                    id="analysis-code"
                    value={code}
                    onChange={(event) => setCode(event.target.value)}
                    disabled={proposalLocked}
                    rows={22}
                    spellCheck={false}
                    className="mt-2 w-full resize-y rounded-input border border-border bg-[#17161b] px-3 py-3 font-mono text-[12px] leading-5 text-[#ece9e2] outline-none focus:border-accent disabled:opacity-70"
                  />
                  <div className={cn("mt-2 flex items-start gap-2 text-[11px]", shellViolation ? "text-error" : "text-muted")}>
                    {shellViolation ? <XCircle size={13} className="mt-0.5 shrink-0" /> : <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-ok" />}
                    {shellViolation
                      ? t("analysis.shellBlocked", { defaultValue: "Shell execution is not allowed. Remove subprocess, system calls, or notebook shell syntax." })
                      : t("analysis.shellSafe", { defaultValue: "No obvious shell execution syntax was detected in this editor. Server-side policy remains authoritative." })}
                  </div>
                </div>
                <div className="flex items-center gap-3 border-t border-border-faint pt-3">
                  <p className="min-w-0 flex-1 text-[11px] leading-relaxed text-muted">
                    {proposalLocked
                      ? t("analysis.proposalLockedHint", { defaultValue: "This proposal is locked to its server-generated hash. Start a new proposal to edit it." })
                      : t("analysis.prepareHint", { defaultValue: "Prepare creates an immutable intent. It does not execute code." })}
                  </p>
                  {proposalLocked ? (
                    <button
                      type="button"
                      onClick={() => setIntent(null)}
                      disabled={executing || intent?.status === "executing"}
                      className="shrink-0 rounded-input border border-border px-3 py-1.5 text-xs text-text hover:bg-surface-2 disabled:opacity-40"
                    >
                      {t("analysis.newProposal", { defaultValue: "New proposal" })}
                    </button>
                  ) : (
                    <button
                      type="submit"
                      disabled={!serviceReady || !datasetId || !objective.trim() || !code.trim() || shellViolation || preparing}
                      className="flex shrink-0 items-center gap-1.5 rounded-input bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-40"
                    >
                      {preparing ? <Loader2 size={13} className="animate-spin" /> : <LockKeyhole size={13} />}
                      {preparing
                        ? t("analysis.preparing", { defaultValue: "Preparing…" })
                        : t("analysis.prepare", { defaultValue: "Prepare intent" })}
                    </button>
                  )}
                </div>
              </div>
            </form>

            {intent && (
              <ApprovalCard
                intent={intent}
                run={intentRun}
                deciding={deciding}
                executing={executing}
                onDecision={decide}
                onExecute={() => void execute()}
              />
            )}

            <RunHistory
              runs={runs}
              loading={loadingProject}
              refreshing={refreshingRuns}
              onRefresh={() => void refreshRuns()}
            />
          </main>
        </div>
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

function ApprovalCard({
  intent,
  run,
  deciding,
  executing,
  onDecision,
  onExecute,
}: {
  intent: AnalysisIntent;
  run: AnalysisRun | null;
  deciding: AnalysisIntentDecision | null;
  executing: boolean;
  onDecision: (decision: AnalysisIntentDecision) => void;
  onExecute: () => void;
}) {
  const { t } = useTranslation("pages");
  const waiting = intent.status === "waiting-approval";
  const approved = intent.status === "approved";
  return (
    <section className="overflow-hidden rounded-card border border-warn/40 bg-surface shadow-card">
      <div className="flex items-center gap-2 border-b border-warn/25 bg-warn/5 px-4 py-3">
        <ShieldAlert size={16} className="text-warn" />
        <h2 className="text-sm font-medium text-text">
          {t("analysis.approvalHeading", { defaultValue: "High-risk execution approval" })}
        </h2>
        <IntentStatus status={intent.status} />
      </div>
      <div className="p-4">
        <p className="text-xs leading-relaxed text-muted">
          {t("analysis.approvalBody", {
            defaultValue: "Review the exact immutable payload below. Approval does not cover later edits or a different dataset.",
          })}
        </p>
        <dl className="mt-4 grid gap-3 sm:grid-cols-2">
          <PolicyCell
            icon={<Hash size={14} />}
            label={t("analysis.payloadHash", { defaultValue: "Payload SHA-256" })}
            value={intent.payloadSha256}
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
            icon={<WifiOff size={14} />}
            label={t("analysis.networkPolicy", { defaultValue: "Network policy" })}
            value={t("analysis.noNetwork", { defaultValue: "No external network" })}
          />
          <PolicyCell
            icon={<NotebookPen size={14} />}
            label={t("analysis.runtime", { defaultValue: "Runtime" })}
            value={t("analysis.jupyterRuntime", { defaultValue: "Isolated Jupyter" })}
          />
        </dl>
        <div className="mt-3 rounded-input border border-border bg-bg px-3 py-2.5">
          <p className="text-[10px] font-medium uppercase tracking-wider text-muted">
            {t("analysis.affectedResources", { defaultValue: "Affected resources" })}
          </p>
          {intent.affectedResources.length > 0 ? (
            <ul className="mt-1.5 space-y-1 font-mono text-[11px] text-text">
              {intent.affectedResources.map((resource) => (
                <li key={resource} className="break-all">{resource}</li>
              ))}
            </ul>
          ) : (
            <p className="mt-1.5 text-xs text-muted">
              {t("analysis.noAffectedResources", { defaultValue: "No affected resources reported." })}
            </p>
          )}
        </div>
        <p className="mt-3 flex items-start gap-2 text-[11px] leading-relaxed text-muted">
          <AlertTriangle size={13} className="mt-0.5 shrink-0 text-warn" />
          {t("analysis.isolationFailureNote", {
            defaultValue: "Execution succeeds only if science core enforces the requested isolation. A runtime or isolation failure is recorded as failed, never completed.",
          })}
        </p>

        <div className="mt-4 flex flex-wrap items-center justify-end gap-2 border-t border-border-faint pt-3">
          {waiting && (
            <>
              <button
                type="button"
                onClick={() => onDecision("rejected")}
                disabled={deciding !== null}
                className="flex items-center gap-1.5 rounded-input border border-border px-3 py-1.5 text-xs text-text hover:bg-surface-2 disabled:opacity-40"
              >
                {deciding === "rejected" ? <Loader2 size={13} className="animate-spin" /> : <XCircle size={13} />}
                {t("analysis.reject", { defaultValue: "Reject" })}
              </button>
              <button
                type="button"
                onClick={() => onDecision("approved")}
                disabled={deciding !== null}
                className="flex items-center gap-1.5 rounded-input bg-warn px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-40"
              >
                {deciding === "approved" ? <Loader2 size={13} className="animate-spin" /> : <ShieldAlert size={13} />}
                {t("analysis.approve", { defaultValue: "Approve exact payload" })}
              </button>
            </>
          )}
          {approved && !run && (
            <button
              type="button"
              onClick={onExecute}
              disabled={executing}
              className="flex items-center gap-1.5 rounded-input bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-40"
            >
              {executing ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} fill="currentColor" />}
              {executing
                ? t("analysis.executing", { defaultValue: "Starting isolated run…" })
                : t("analysis.execute", { defaultValue: "Execute approved payload" })}
            </button>
          )}
          {intent.status === "rejected" && (
            <p className="text-xs text-muted">
              {t("analysis.rejectedNote", { defaultValue: "Rejected — no execution was started." })}
            </p>
          )}
          {run && (
            <p className="text-xs text-muted">
              {t("analysis.runCreated", {
                defaultValue: "Run {{id}} records this execution attempt.",
                id: run.id.slice(0, 8),
              })}
            </p>
          )}
        </div>
      </div>
    </section>
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

function IntentStatus({ status }: { status: AnalysisIntentStatus }) {
  const { t } = useTranslation("pages");
  const tone =
    status === "completed" || status === "approved"
      ? "bg-ok/10 text-ok ring-ok/20"
      : status === "failed" || status === "rejected"
        ? "bg-error/10 text-error ring-error/20"
        : "bg-warn/10 text-warn ring-warn/20";
  return (
    <span className={cn("ml-auto rounded-full px-2 py-0.5 text-[10px] font-medium ring-1", tone)}>
      {t(`analysis.intentStatus.${status}`, { defaultValue: status })}
    </span>
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
        <div className="space-y-3">
          {runs.map((run) => (
            <RunCard key={run.id} run={run} />
          ))}
        </div>
      </div>
    </section>
  );
}

function RunCard({ run }: { run: AnalysisRun }) {
  const { t } = useTranslation("pages");
  return (
    <article className="overflow-hidden rounded-input border border-border bg-bg">
      <div className="flex items-center gap-2 px-3 py-2.5">
        <RunStatus status={run.status} />
        <span className="font-mono text-[11px] text-text">{run.id.slice(0, 12)}</span>
        <span className="ml-auto text-[10px] text-muted">{formatTime(run.finishedAt ?? run.createdAt)}</span>
      </div>
      {run.error && (
        <div className="flex items-start gap-2 border-t border-error/20 bg-error/5 px-3 py-2.5 text-xs text-error">
          <XCircle size={13} className="mt-0.5 shrink-0" />
          <span className="break-words">{run.error}</span>
        </div>
      )}
      <div className="grid gap-3 border-t border-border p-3 xl:grid-cols-2">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-muted">
            <ScrollText size={12} />
            {t("analysis.logs", { defaultValue: "Logs" })}
          </p>
          <pre className="mt-2 max-h-44 overflow-auto whitespace-pre-wrap break-words rounded-input bg-[#17161b] p-2.5 font-mono text-[10px] leading-4 text-[#d8d4cc]">
            {run.logs || t("analysis.noLogs", { defaultValue: "No logs reported." })}
          </pre>
        </div>
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-muted">
            <FileOutput size={12} />
            {t("analysis.artifacts", {
              defaultValue: "Artifacts ({{count}})",
              count: run.artifacts.length,
            })}
          </p>
          {run.artifacts.length > 0 ? (
            <ul className="mt-2 space-y-1.5">
              {run.artifacts.map((artifact) => (
                <ArtifactRow key={artifact.id} artifact={artifact} />
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-xs text-muted">
              {run.status === "failed"
                ? t("analysis.noArtifactsFailed", { defaultValue: "No artifacts were accepted from the failed run." })
                : t("analysis.noArtifacts", { defaultValue: "No artifacts reported yet." })}
            </p>
          )}
        </div>
      </div>
    </article>
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

function ArtifactRow({ artifact }: { artifact: AnalysisArtifact }) {
  const { t } = useTranslation("pages");
  const [opening, setOpening] = useState(false);

  const openArtifact = async () => {
    if (opening) return;
    const preview = window.open("about:blank", "_blank");
    if (preview) preview.opener = null;
    setOpening(true);
    try {
      const blob = await scienceCore.fetchArtifactBlob(artifact.id);
      const objectUrl = URL.createObjectURL(blob);
      if (preview) {
        preview.location.replace(objectUrl);
      } else {
        const anchor = document.createElement("a");
        anchor.href = objectUrl;
        anchor.target = "_blank";
        anchor.rel = "noreferrer";
        anchor.click();
      }
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
    } catch (error) {
      preview?.close();
      toast.error(
        t("analysis.toast.openArtifactFailed", {
          defaultValue: "Could not open artifact: {{error}}",
          error: errorMessage(error),
        }),
      );
    } finally {
      setOpening(false);
    }
  };

  return (
    <li>
      <button
        type="button"
        onClick={() => void openArtifact()}
        disabled={opening}
        className="flex w-full items-center gap-2 rounded-input border border-border-faint bg-surface px-2.5 py-2 text-left hover:border-accent/30 hover:bg-surface-2 disabled:opacity-60"
      >
        {opening ? (
          <Loader2 size={13} className="shrink-0 animate-spin text-muted" />
        ) : artifact.artifactType.startsWith("notebook") ? (
          <NotebookPen size={13} className="shrink-0 text-muted" />
        ) : artifact.mimeType.startsWith("image/") ? (
          <BarChart3 size={13} className="shrink-0 text-muted" />
        ) : (
          <FileOutput size={13} className="shrink-0 text-muted" />
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium text-text">{fileName(artifact.path)}</p>
          <p className="mt-0.5 truncate text-[10px] text-muted">
            {artifact.artifactType} · {artifact.contentHash.slice(0, 12)}
          </p>
        </div>
      </button>
    </li>
  );
}
