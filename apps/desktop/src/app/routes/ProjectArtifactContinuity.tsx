import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CheckCircle2,
  FileCode2,
  FileText,
  FolderArchive,
  Image as ImageIcon,
  RefreshCw,
  Table2,
} from "lucide-react";
import type {
  DatasetAnalysisWorkflowSnapshot,
  ResearchProject,
  ResearchWorkflowSnapshot,
  WorkflowAnalysisArtifact,
} from "@spark/research-domain";
import { scienceCore } from "@/lib/scienceCore";
import { cn } from "@/lib/cn";
import { parseTableFile } from "@/lib/csv";
import { parseIpynb } from "@/lib/notebook-file";

type ArtifactMode = "notebooks" | "artifacts";
type SelectionIssue =
  | "missing-url"
  | "invalid-project"
  | "invalid-workflow"
  | "choose-workflow";
type ArtifactIntegrityState =
  | "recorded"
  | "verifying"
  | "recovered"
  | "missing"
  | "tampered"
  | "unavailable";
const RECORDED_INTEGRITY: ArtifactIntegrityState = "recorded";

interface ArtifactPreview {
  artifact: WorkflowAnalysisArtifact;
  text: string | null;
  url: string | null;
}

function isDatasetWorkflow(
  snapshot: ResearchWorkflowSnapshot,
): snapshot is DatasetAnalysisWorkflowSnapshot {
  return snapshot.workflow.workflowType === "dataset-analysis";
}

function artifactName(path: string): string {
  return path.split("/").filter(Boolean).slice(-1)[0] ?? path;
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

function compactHash(value: string): string {
  return value.length <= 20 ? value : `${value.slice(0, 10)}…${value.slice(-6)}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function integrityFailure(error: unknown): ArtifactIntegrityState {
  const message = errorMessage(error).toLowerCase();
  if (message.includes("missing") || message.includes("not found")) return "missing";
  if (
    message.includes("hash") ||
    message.includes("no longer matches") ||
    message.includes("tamper")
  ) {
    return "tampered";
  }
  return "unavailable";
}

function artifactIcon(artifact: WorkflowAnalysisArtifact) {
  if (artifact.artifactType.startsWith("notebook")) {
    return <FileCode2 size={14} aria-hidden={true} />;
  }
  if (artifact.mimeType.startsWith("image/")) {
    return <ImageIcon size={14} aria-hidden={true} />;
  }
  if (
    artifact.mimeType === "text/csv" ||
    artifact.mimeType === "text/tab-separated-values"
  ) {
    return <Table2 size={14} aria-hidden={true} />;
  }
  return <FileText size={14} aria-hidden={true} />;
}

export function ProjectArtifactContinuity({ mode }: { mode: ArtifactMode }) {
  const { t } = useTranslation("pages");
  const [searchParams, setSearchParams] = useSearchParams();
  const [projects, setProjects] = useState<ResearchProject[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [workflows, setWorkflows] = useState<DatasetAnalysisWorkflowSnapshot[]>([]);
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [workflowCandidateId, setWorkflowCandidateId] = useState<string | null>(null);
  const [selectionIssue, setSelectionIssue] = useState<SelectionIssue | null>(null);
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [loadingWorkflows, setLoadingWorkflows] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [artifactError, setArtifactError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [integrity, setIntegrity] = useState<Record<string, ArtifactIntegrityState>>({});
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  const previewAbort = useRef<AbortController | null>(null);
  const requestedSelection = useRef({
    projectId: searchParams.get("projectId"),
    workflowId: searchParams.get("workflowId"),
  });

  const updateSelectionUrl = useCallback(
    (nextProjectId: string | null, nextWorkflowId: string | null) => {
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          if (nextProjectId) next.set("projectId", nextProjectId);
          else next.delete("projectId");
          if (nextWorkflowId) next.set("workflowId", nextWorkflowId);
          else next.delete("workflowId");
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  useEffect(() => {
    const controller = new AbortController();
    const requestedProjectId = requestedSelection.current.projectId;
    const requestedWorkflowId = requestedSelection.current.workflowId;
    setLoadingProjects(true);
    setError(null);
    void scienceCore
      .listProjects({ signal: controller.signal })
      .then((nextProjects) => {
        if (controller.signal.aborted) return;
        setProjects(nextProjects);
        setWorkflowId(null);
        setWorkflows([]);
        if (!requestedProjectId || !requestedWorkflowId) {
          setProjectId(null);
          setWorkflowCandidateId(null);
          setSelectionIssue("missing-url");
          return;
        }
        const requestedProject = nextProjects.find(
          (project) => project.id === requestedProjectId,
        );
        if (!requestedProject) {
          setProjectId(null);
          setWorkflowCandidateId(null);
          setSelectionIssue("invalid-project");
          return;
        }
        setProjectId(requestedProject.id);
        setWorkflowCandidateId(requestedWorkflowId);
        setSelectionIssue(null);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setProjects([]);
          setProjectId(null);
          setError(errorMessage(reason));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingProjects(false);
      });
    return () => controller.abort();
  }, [reload]);

  useEffect(() => {
    previewAbort.current?.abort();
    setPreview(null);
    setIntegrity({});
    if (!projectId) {
      setWorkflows([]);
      setWorkflowId(null);
      setLoadingWorkflows(false);
      return;
    }

    const controller = new AbortController();
    setLoadingWorkflows(true);
    setError(null);
    void scienceCore
      .listWorkflows(projectId, { activeOnly: false, limit: 100, signal: controller.signal })
      .then((snapshots) => {
        if (controller.signal.aborted) return;
        const datasetWorkflows = snapshots
          .filter(isDatasetWorkflow)
          .filter((snapshot) => snapshot.workflow.projectId === projectId);
        setWorkflows(datasetWorkflows);
        if (workflowCandidateId) {
          const requestedWorkflow = datasetWorkflows.find(
            (snapshot) => snapshot.workflow.id === workflowCandidateId,
          );
          if (!requestedWorkflow) {
            setWorkflowId(null);
            setSelectionIssue("invalid-workflow");
            return;
          }
          setWorkflowId(requestedWorkflow.workflow.id);
          setSelectionIssue(null);
          return;
        }
        setWorkflowId(null);
        setSelectionIssue(
          datasetWorkflows.length > 0 ? "choose-workflow" : null,
        );
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setWorkflows([]);
          setWorkflowId(null);
          setError(errorMessage(reason));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingWorkflows(false);
      });
    return () => controller.abort();
  }, [
    projectId,
    reload,
    workflowCandidateId,
  ]);

  useEffect(() => () => previewAbort.current?.abort(), []);
  useEffect(
    () => () => {
      if (preview?.url) URL.revokeObjectURL(preview.url);
    },
    [preview?.url],
  );

  const selectedProject =
    projects.find((project) => project.id === projectId) ?? null;
  const selectedWorkflow =
    workflows.find((snapshot) => snapshot.workflow.id === workflowId) ?? null;
  const artifacts = useMemo(
    () => {
      const allArtifacts = selectedWorkflow?.analysisRun?.artifacts ?? [];
      return mode === "notebooks"
        ? allArtifacts.filter((artifact) =>
            artifact.artifactType.startsWith("notebook"),
          )
        : allArtifacts;
    },
    [mode, selectedWorkflow?.analysisRun?.artifacts],
  );

  const openArtifact = useCallback(
    async (artifact: WorkflowAnalysisArtifact) => {
      previewAbort.current?.abort();
      const controller = new AbortController();
      previewAbort.current = controller;
      setIntegrity((current) => ({ ...current, [artifact.id]: "verifying" }));
      setPreview(null);
      setArtifactError(null);
      try {
        const blob = await scienceCore.fetchArtifactBlob(artifact.id, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        const textual =
          artifact.artifactType.startsWith("notebook") ||
          artifact.artifactType === "structured-data" ||
          artifact.artifactType === "environment" ||
          artifact.artifactType === "stdout" ||
          artifact.artifactType === "stderr" ||
          artifact.artifactType === "log" ||
          artifact.mimeType.startsWith("text/") ||
          artifact.mimeType === "application/json";
        const text = textual ? await blob.text() : null;
        if (controller.signal.aborted) return;
        const url = artifact.mimeType.startsWith("image/")
          ? URL.createObjectURL(blob)
          : null;
        setIntegrity((current) => ({ ...current, [artifact.id]: "recovered" }));
        setPreview({ artifact, text, url });
      } catch (reason) {
        if (controller.signal.aborted) return;
        setIntegrity((current) => ({
          ...current,
          [artifact.id]: integrityFailure(reason),
        }));
        setArtifactError(errorMessage(reason));
      }
    },
    [],
  );

  const selectProject = (nextProjectId: string) => {
    previewAbort.current?.abort();
    requestedSelection.current = {
      projectId: nextProjectId,
      workflowId: null,
    };
    setProjectId(nextProjectId);
    setWorkflowId(null);
    setWorkflowCandidateId(null);
    setArtifactError(null);
    setSelectionIssue("choose-workflow");
    updateSelectionUrl(nextProjectId, null);
  };

  const selectWorkflow = (nextWorkflowId: string) => {
    previewAbort.current?.abort();
    const selected = workflows.find(
      (snapshot) =>
        snapshot.workflow.id === nextWorkflowId &&
        snapshot.workflow.projectId === projectId,
    );
    if (!selected) {
      setWorkflowId(null);
      setSelectionIssue("invalid-workflow");
      return;
    }
    requestedSelection.current = {
      projectId,
      workflowId: nextWorkflowId,
    };
    setWorkflowId(nextWorkflowId);
    setArtifactError(null);
    setSelectionIssue(null);
    updateSelectionUrl(projectId, nextWorkflowId);
  };

  const retry = () => {
    previewAbort.current?.abort();
    setError(null);
    setArtifactError(null);
    setReload((current) => current + 1);
  };

  const loading = loadingProjects || loadingWorkflows;
  const title =
    mode === "notebooks"
      ? t("artifactContinuity.notebooksTitle", {
          defaultValue: "Project notebooks",
        })
      : t("artifactContinuity.artifactsTitle", {
          defaultValue: "Project artifacts",
        });
  const Icon = mode === "notebooks" ? FileCode2 : FolderArchive;

  return (
    <div className="h-full overflow-y-auto bg-bg">
      <main className="mx-auto w-full max-w-[86rem] px-4 py-5 xl:px-6">
        <header className="flex flex-wrap items-start gap-3 border-b border-border pb-4">
          <Icon size={18} className="mt-0.5 shrink-0 text-accent" aria-hidden={true} />
          <div className="min-w-0 flex-1">
            <h1 className="text-base font-semibold text-text">{title}</h1>
            <p className="mt-1 max-w-[70ch] text-xs leading-relaxed text-muted">
              {t("artifactContinuity.description", {
                defaultValue:
                  "Read persisted analysis outputs from Science Core. This view does not depend on the current OpenCode session or execute new code.",
              })}
            </p>
          </div>
          <button
            type="button"
            onClick={retry}
            className="compact-button secondary-button"
          >
            <RefreshCw size={13} aria-hidden={true} />
            {t("artifactContinuity.refresh", { defaultValue: "Refresh" })}
          </button>
        </header>

        <div className="grid gap-3 border-b border-border bg-surface py-4 md:grid-cols-2">
          <label className="min-w-0 text-caption font-medium text-muted">
            {t("artifactContinuity.project", { defaultValue: "Project" })}
            <select
              value={projectId ?? ""}
              onChange={(event) => selectProject(event.target.value)}
              disabled={loadingProjects || projects.length === 0}
              className="mt-1 block h-9 w-full rounded-input border border-border bg-surface px-2 text-xs text-text"
            >
              <option value="" disabled>
                {t("artifactContinuity.chooseProject", {
                  defaultValue: "Choose a project",
                })}
              </option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.title}
                </option>
              ))}
            </select>
          </label>
          <label className="min-w-0 text-caption font-medium text-muted">
            {t("artifactContinuity.workflow", {
              defaultValue: "Dataset workflow",
            })}
            <select
              value={workflowId ?? ""}
              onChange={(event) => selectWorkflow(event.target.value)}
              disabled={loadingWorkflows || workflows.length === 0}
              className="mt-1 block h-9 w-full rounded-input border border-border bg-surface px-2 text-xs text-text"
            >
              <option value="" disabled>
                {t("artifactContinuity.chooseWorkflow", {
                  defaultValue: "Choose a dataset workflow",
                })}
              </option>
              {workflows.map((snapshot) => (
                <option key={snapshot.workflow.id} value={snapshot.workflow.id}>
                  {snapshot.workflow.goal}
                </option>
              ))}
            </select>
          </label>
        </div>

        {loading && (
          <div
            className="space-y-2 py-5"
            aria-label={t("artifactContinuity.loading", {
              defaultValue: "Loading persisted artifacts",
            })}
          >
            <div className="h-9 animate-pulse rounded-input bg-surface-2" />
            <div className="h-9 animate-pulse rounded-input bg-surface-2" />
            <div className="h-9 animate-pulse rounded-input bg-surface-2" />
          </div>
        )}

        {!loading && error && (
          <section className="flex items-start gap-3 border-b border-error/30 py-5">
            <AlertTriangle size={15} className="mt-0.5 shrink-0 text-error" />
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold text-text">
                {t("artifactContinuity.loadFailed", {
                  defaultValue: "Could not recover project artifacts",
                })}
              </h2>
              <p className="mt-1 break-words text-xs text-error">{error}</p>
              <button
                type="button"
                onClick={retry}
                className="compact-button secondary-button mt-3"
              >
                {t("artifactContinuity.retry", { defaultValue: "Retry" })}
              </button>
            </div>
          </section>
        )}

        {!loading && !error && projects.length === 0 && (
          <ContinuityEmpty
            title={t("artifactContinuity.noProjects", {
              defaultValue: "No research projects yet",
            })}
            body={t("artifactContinuity.noProjectsBody", {
              defaultValue:
                "Create a project and complete a dataset analysis to preserve notebooks and artifacts here.",
            })}
          />
        )}

        {!loading && !error && projects.length > 0 && selectionIssue && (
          <SelectionIssueState issue={selectionIssue} />
        )}

        {!loading &&
          !error &&
          !selectionIssue &&
          projects.length > 0 &&
          projectId &&
          workflows.length === 0 && (
            <ContinuityEmpty
              title={t("artifactContinuity.noWorkflows", {
                defaultValue: "No dataset workflows in this project",
              })}
              body={t("artifactContinuity.noWorkflowsBody", {
                defaultValue:
                  "Artifacts appear only after a project-bound dataset workflow creates a durable analysis run.",
              })}
            />
          )}

        {!loading && !error && selectedWorkflow && (
          <>
            <LineageSummary
              project={selectedProject}
              snapshot={selectedWorkflow}
            />
            {artifacts.length === 0 ? (
              <ContinuityEmpty
                title={
                  mode === "notebooks"
                    ? t("artifactContinuity.noNotebooks", {
                        defaultValue: "No recorded notebook for this workflow",
                      })
                    : t("artifactContinuity.noArtifacts", {
                        defaultValue: "No recorded artifacts for this workflow",
                      })
                }
                body={t("artifactContinuity.noArtifactsBody", {
                  defaultValue:
                    "Complete the exact approved analysis. Spark never infers outputs from unrelated workspace files.",
                })}
              />
            ) : (
              <>
                {artifactError && (
                  <div
                    role="alert"
                    className="flex items-start gap-2 border-b border-error/30 py-3 text-xs text-error"
                  >
                    <AlertTriangle
                      size={13}
                      className="mt-0.5 shrink-0"
                      aria-hidden={true}
                    />
                    <span className="break-words">{artifactError}</span>
                  </div>
                )}
                <ArtifactTable
                  artifacts={artifacts}
                  integrity={integrity}
                  onOpen={openArtifact}
                />
              </>
            )}
          </>
        )}

        {preview && <ArtifactPreviewPanel preview={preview} />}
      </main>
    </div>
  );
}

function SelectionIssueState({ issue }: { issue: SelectionIssue }) {
  const { t } = useTranslation("pages");
  const content =
    issue === "missing-url"
      ? {
          title: t("artifactContinuity.selectionMissing", {
            defaultValue: "Project and workflow are required",
          }),
          body: t("artifactContinuity.selectionMissingBody", {
            defaultValue:
              "Open this page from a completed dataset workflow, or choose a project and dataset workflow below.",
          }),
        }
      : issue === "invalid-project"
        ? {
            title: t("artifactContinuity.projectInvalid", {
              defaultValue: "Project selection is invalid",
            }),
            body: t("artifactContinuity.projectInvalidBody", {
              defaultValue:
                "The requested project is missing or unavailable. Choose an existing project to continue.",
            }),
          }
        : issue === "invalid-workflow"
          ? {
              title: t("artifactContinuity.workflowInvalid", {
                defaultValue: "Workflow selection is invalid",
              }),
              body: t("artifactContinuity.workflowInvalidBody", {
                defaultValue:
                  "The requested workflow is missing or does not belong to the selected project. Choose a workflow from this project.",
              }),
            }
          : {
              title: t("artifactContinuity.chooseWorkflowTitle", {
                defaultValue: "Choose a dataset workflow",
              }),
              body: t("artifactContinuity.chooseWorkflowBody", {
                defaultValue:
                  "Select the exact workflow whose persisted notebooks and artifacts you want to inspect.",
              }),
            };
  return <ContinuityEmpty title={content.title} body={content.body} />;
}

function LineageSummary({
  project,
  snapshot,
}: {
  project: ResearchProject | null;
  snapshot: DatasetAnalysisWorkflowSnapshot;
}) {
  const { t } = useTranslation("pages");
  return (
    <dl className="grid gap-x-5 gap-y-3 border-b border-border py-4 text-caption sm:grid-cols-2 xl:grid-cols-4">
      <LineageDatum
        label={t("artifactContinuity.projectLineage", {
          defaultValue: "Project",
        })}
        value={`${project?.title ?? snapshot.workflow.projectId} · ${snapshot.workflow.projectId}`}
      />
      <LineageDatum
        label={t("artifactContinuity.workflowLineage", {
          defaultValue: "Workflow",
        })}
        value={t("artifactContinuity.workflowLineageValue", {
          defaultValue: "{{id}} · revision {{revision}}",
          id: snapshot.workflow.id,
          revision: snapshot.workflow.revision,
        })}
      />
      <LineageDatum
        label={t("artifactContinuity.datasetLineage", {
          defaultValue: "Dataset source · SHA-256",
        })}
        value={`${snapshot.workflow.datasetSourceId} · ${snapshot.workflow.datasetContentHash}`}
      />
      <LineageDatum
        label={t("artifactContinuity.analysisLineage", {
          defaultValue: "Analysis revision · spec SHA-256",
        })}
        value={
          snapshot.analysisSpec
            ? t("artifactContinuity.analysisLineageValue", {
                defaultValue: "Revision {{revision}} · {{hash}}",
                revision: snapshot.analysisSpec.revision,
                hash: snapshot.analysisSpec.specSha256,
              })
            : t("artifactContinuity.legacyAnalysis", {
                defaultValue: "Legacy analysis without a revisioned spec",
              })
        }
      />
    </dl>
  );
}

function LineageDatum({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="font-medium text-muted">{label}</dt>
      <dd className="mt-1 break-all font-mono text-text">{value}</dd>
    </div>
  );
}

function ArtifactTable({
  artifacts,
  integrity,
  onOpen,
}: {
  artifacts: WorkflowAnalysisArtifact[];
  integrity: Record<string, ArtifactIntegrityState>;
  onOpen: (artifact: WorkflowAnalysisArtifact) => Promise<void>;
}) {
  const { t } = useTranslation("pages");
  return (
    <section className="py-4">
      <div className="overflow-x-auto border-y border-border bg-surface">
        <table className="min-w-full border-separate border-spacing-0 text-left text-caption">
          <thead className="bg-surface-2 text-muted">
            <tr>
              <th className="px-3 py-2 font-medium">
                {t("artifactContinuity.artifact", { defaultValue: "Artifact" })}
              </th>
              <th className="px-3 py-2 font-medium">
                {t("artifactContinuity.path", { defaultValue: "Project path" })}
              </th>
              <th className="px-3 py-2 font-medium">
                {t("artifactContinuity.hash", { defaultValue: "SHA-256" })}
              </th>
              <th className="px-3 py-2 font-medium">
                {t("artifactContinuity.created", { defaultValue: "Created" })}
              </th>
              <th className="px-3 py-2 font-medium">
                {t("artifactContinuity.integrity", { defaultValue: "Recovery" })}
              </th>
              <th className="px-3 py-2 text-right font-medium">
                {t("artifactContinuity.action", { defaultValue: "Action" })}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-faint">
            {artifacts.map((artifact) => {
              const status = integrity[artifact.id] ?? RECORDED_INTEGRITY;
              return (
                <tr key={artifact.id} className="align-top hover:bg-surface-2">
                  <td className="px-3 py-2.5">
                    <div className="flex min-w-44 items-center gap-2 text-text">
                      <span className="text-muted">{artifactIcon(artifact)}</span>
                      <span>
                        <span className="block font-medium">
                          {artifactName(artifact.path)}
                        </span>
                        <span className="block text-muted">
                          {artifact.artifactType}
                        </span>
                      </span>
                    </div>
                  </td>
                  <td
                    className="max-w-md break-all px-3 py-2.5 font-mono text-text"
                    title={artifact.path}
                  >
                    {artifact.path}
                  </td>
                  <td
                    className="px-3 py-2.5 font-mono text-text"
                    title={artifact.contentHash}
                  >
                    {compactHash(artifact.contentHash)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-muted">
                    {formatTimestamp(artifact.createdAt)}
                  </td>
                  <td className="px-3 py-2.5">
                    <IntegrityBadge status={status} />
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      type="button"
                      disabled={status === "verifying"}
                      onClick={() => void onOpen(artifact)}
                      className="compact-button secondary-button"
                    >
                      {status === "verifying"
                        ? t("artifactContinuity.verifying", {
                            defaultValue: "Verifying…",
                          })
                        : artifact.artifactType.startsWith("notebook")
                          ? t("artifactContinuity.openNotebook", {
                              defaultValue: "Open notebook",
                            })
                          : t("artifactContinuity.openArtifact", {
                              defaultValue: "Open",
                            })}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function IntegrityBadge({ status }: { status: ArtifactIntegrityState }) {
  const { t } = useTranslation("pages");
  const tone =
    status === "recovered"
      ? "text-ok"
      : status === "missing" || status === "tampered" || status === "unavailable"
        ? "text-error"
        : status === "verifying"
          ? "text-warn"
          : "text-muted";
  const label = t(`artifactContinuity.status.${status}`, {
    defaultValue:
      status === "recorded"
        ? "Recorded"
        : status === "verifying"
          ? "Verifying"
          : status === "recovered"
            ? "Recovered"
            : status === "missing"
              ? "Missing"
              : status === "tampered"
                ? "Tampered"
                : "Unavailable",
  });
  return (
    <span className={cn("inline-flex items-center gap-1.5 font-medium", tone)}>
      {status === "recovered" ? (
        <CheckCircle2 size={12} aria-hidden={true} />
      ) : status === "missing" ||
        status === "tampered" ||
        status === "unavailable" ? (
        <AlertTriangle size={12} aria-hidden={true} />
      ) : null}
      {label}
    </span>
  );
}

function ArtifactPreviewPanel({ preview }: { preview: ArtifactPreview }) {
  const { t } = useTranslation("pages");
  const { artifact, text, url } = preview;
  const notebook = artifact.artifactType.startsWith("notebook");
  const isTableMime =
    artifact.mimeType === "text/csv" ||
    artifact.mimeType === "text/tab-separated-values";
  const table = isTableMime && text != null ? parseTableFile(artifact.path, text) : null;
  let notebookCells: ReturnType<typeof parseIpynb> | null = null;
  let notebookError: string | null = null;
  if (notebook && text != null) {
    try {
      notebookCells = parseIpynb(text);
    } catch (error) {
      notebookError = errorMessage(error);
    }
  }
  return (
    <section className="border-y border-border bg-surface">
      <header className="flex min-h-11 items-center gap-2 border-b border-border-faint px-4">
        <span className="text-muted">{artifactIcon(artifact)}</span>
        <h2 className="min-w-0 flex-1 truncate text-xs font-semibold text-text">
          {artifactName(artifact.path)}
        </h2>
        <span className="text-caption font-medium text-ok">
          {t("artifactContinuity.verifiedByCore", {
            defaultValue: "Recovered and hash-verified",
          })}
        </span>
      </header>
      {url && (
        <img
          src={url}
          alt={artifactName(artifact.path)}
          className="max-h-[34rem] w-full object-contain p-4"
        />
      )}
      {table && (
        <div className="max-h-[34rem] overflow-auto">
          <table className="min-w-full border-separate border-spacing-0 text-left text-caption">
            <thead className="sticky top-0 bg-surface-2 text-muted">
              <tr>
                {table.columns.map((column, index) => (
                  <th
                    key={`${column}:${index}`}
                    className="border-b border-border px-3 py-2 font-medium"
                  >
                    {column ||
                      t("artifactContinuity.unnamedColumn", {
                        defaultValue: "Column {{index}}",
                        index: index + 1,
                      })}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.slice(0, 100).map((row, rowIndex) => (
                <tr key={rowIndex} className="even:bg-surface-2">
                  {row.map((cell, cellIndex) => (
                    <td
                      key={cellIndex}
                      className="max-w-72 whitespace-pre-wrap break-words border-b border-border-faint px-3 py-2 text-text"
                    >
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {notebookError && (
        <p className="break-words p-4 text-xs text-error">{notebookError}</p>
      )}
      {notebookCells && (
        <ol className="divide-y divide-border-faint">
          {notebookCells.map((cell) => (
            <li
              key={cell.index}
              className="grid min-w-0 grid-cols-[3rem_minmax(0,1fr)]"
            >
              <span className="border-r border-border-faint px-2 py-3 text-right font-mono text-caption text-muted">
                [{cell.index}]
              </span>
              <div className="min-w-0 px-4 py-3">
                <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs leading-5 text-text">
                  {cell.code}
                </pre>
                {cell.output && (
                  <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-all border-t border-border-faint pt-3 font-mono text-caption leading-5 text-muted">
                    {cell.output}
                  </pre>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
      {!url && !table && !notebook && text != null && (
        <pre className="max-h-[34rem] overflow-auto whitespace-pre-wrap break-all p-4 font-mono text-caption leading-5 text-text">
          {text}
        </pre>
      )}
      {!url && text == null && (
        <p className="p-4 text-xs text-muted">
          {t("artifactContinuity.binaryVerified", {
            defaultValue:
              "The binary artifact was recovered and verified. Use the project path and recorded MIME type in the artifact list.",
          })}
        </p>
      )}
      <footer className="break-all border-t border-border-faint px-4 py-2 font-mono text-caption text-muted">
        {artifact.path} · {artifact.contentHash}
      </footer>
    </section>
  );
}

function ContinuityEmpty({ title, body }: { title: string; body: string }) {
  return (
    <section className="border-b border-border py-8">
      <h2 className="text-sm font-semibold text-text">{title}</h2>
      <p className="mt-1 max-w-[70ch] text-xs leading-relaxed text-muted">{body}</p>
    </section>
  );
}
