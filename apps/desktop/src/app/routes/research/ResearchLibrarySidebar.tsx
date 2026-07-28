import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ChevronRight,
  Archive,
  Check,
  Library,
  Loader2,
  Pencil,
  Plus,
  RotateCcw,
  Upload,
  X,
} from "lucide-react";
import type {
  ResearchProject,
  ResearchSource,
  ResearchWorkflowSnapshot,
  ScienceCoreHealth,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";
import type { ResearchPdfSelection } from "./ResearchInspector";
import { ResearchStageRail } from "./ResearchStageRail";
import { RESEARCH_OBJECT_KIND, ResearchObjectIcon } from "./ResearchObjectIcon";
import { ResearchWorkflowList } from "./ResearchWorkflowList";

const PDF_ACCEPT = ".pdf,application/pdf";

export interface ResearchLibrarySidebarProps {
  inactive?: boolean;
  health: ScienceCoreHealth | null;
  booting: boolean;
  serviceReady: boolean;
  projects: ResearchProject[];
  projectId: string | null;
  workflows: ResearchWorkflowSnapshot[];
  snapshot: ResearchWorkflowSnapshot | null;
  selectedWorkflowId: string | null;
  loadingWorkflows: boolean;
  workflowMutating: boolean;
  sources: ResearchSource[];
  loadingSources: boolean;
  selection: ResearchPdfSelection | null;
  importing: boolean;
  projectMutating: boolean;
  showArchivedProjects: boolean;
  pdfInputRef?: React.RefObject<HTMLInputElement>;
  onProjectChange: (projectId: string | null) => void;
  onNewProject: () => void;
  onSelectWorkflow: (workflowId: string) => void;
  onOpenWorkflowReport: (workflowId: string) => void;
  onNewWorkflow: () => void;
  onSelectSource: (source: ResearchSource) => void;
  onImportPdf: (event: React.ChangeEvent<HTMLInputElement>) => void | Promise<void>;
  onToggleArchivedProjects: (show: boolean) => void;
  onRenameProject: (title: string) => void | Promise<void>;
  onArchiveProject: () => void | Promise<void>;
  onRestoreProject: () => void | Promise<void>;
}

export function ResearchLibrarySidebar({
  inactive = false,
  health,
  booting,
  serviceReady,
  projects,
  projectId,
  workflows,
  snapshot,
  selectedWorkflowId,
  loadingWorkflows,
  workflowMutating,
  sources,
  loadingSources,
  selection,
  importing,
  projectMutating,
  showArchivedProjects,
  pdfInputRef,
  onProjectChange,
  onNewProject,
  onSelectWorkflow,
  onOpenWorkflowReport,
  onNewWorkflow,
  onSelectSource,
  onImportPdf,
  onToggleArchivedProjects,
  onRenameProject,
  onArchiveProject,
  onRestoreProject,
}: ResearchLibrarySidebarProps) {
  const { t } = useTranslation("pages");
  const localFileInput = useRef<HTMLInputElement>(null);
  const [editingProject, setEditingProject] = useState(false);
  const [renameDraft, setRenameDraft] = useState("");
  const [confirmingArchive, setConfirmingArchive] = useState(false);
  const fileInput = pdfInputRef ?? localFileInput;
  const visibleProjects = projects.filter((project) => showArchivedProjects || !project.archivedAt);
  const selectedProject = visibleProjects.find((project) => project.id === projectId) ?? null;
  const readySourceCount = sources.filter(
    (source) => source.ingestionStatus === "ready",
  ).length;
  return (
    <aside
      className="research-library-sidebar flex w-72 shrink-0 flex-col border-r border-border bg-surface-2 max-[1180px]:w-64 max-[900px]:w-60 max-[640px]:w-[13.5rem]"
      aria-hidden={inactive || undefined}
      {...(inactive ? { inert: "" } : {})}
    >
      <div className="border-b border-border px-4 pb-3 pt-3.5">
        <div className="flex items-center gap-2">
          <Library size={15} className="text-accent" />
          <h1 className="text-ui font-semibold text-text">
            {t("research.libraryTitle", { defaultValue: "Research library" })}
          </h1>
        </div>
        <HealthBadge health={health} loading={booting} />
      </div>

      <div className="border-b border-border px-4 py-4">
        <div className="mb-2.5 flex items-center justify-between">
          <label htmlFor="research-project" className="text-xs font-medium text-muted">
            {t("research.projectLabel", { defaultValue: "Project" })}
          </label>
          <button
            type="button"
            onClick={onNewProject}
            disabled={booting}
            className="touch-target flex h-10 w-10 items-center justify-center rounded-input text-muted hover:bg-surface hover:text-text disabled:opacity-40"
            aria-label={t("research.newProjectAria", { defaultValue: "Create research project" })}
          >
            <Plus size={14} />
          </button>
        </div>
        {visibleProjects.length > 0 ? (
          <select
            id="research-project"
            value={projectId ?? ""}
            onChange={(event) => {
              setEditingProject(false);
              setConfirmingArchive(false);
              onProjectChange(event.target.value || null);
            }}
            disabled={projectMutating}
            className="min-h-9 w-full rounded-input border border-border bg-surface px-2.5 py-2 text-xs text-text focus:border-accent"
          >
            {visibleProjects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.archivedAt ? `${project.title} · ${t("research.archived", { defaultValue: "Archived" })}` : project.title}
              </option>
            ))}
          </select>
        ) : (
          !booting && (
            <p className="py-1 text-xs leading-relaxed text-muted">
              {t("research.noProjects", { defaultValue: "Create a project to begin." })}
            </p>
          )
        )}
        <div className="mt-2 flex items-center justify-between gap-2">
          <label className="flex min-h-8 cursor-pointer items-center gap-2 text-caption text-muted">
            <input
              type="checkbox"
              checked={showArchivedProjects}
              onChange={(event) => onToggleArchivedProjects(event.target.checked)}
              disabled={projectMutating}
              className="h-3.5 w-3.5 accent-accent"
            />
            {t("research.showArchived", { defaultValue: "Show archived" })}
          </label>
          {selectedProject && !editingProject && !confirmingArchive && (
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => {
                  setRenameDraft(selectedProject.title);
                  setEditingProject(true);
                }}
                disabled={projectMutating}
                className="touch-target inline-flex h-8 items-center gap-1 rounded-input px-2 text-caption text-muted hover:bg-surface hover:text-text disabled:opacity-40"
              >
                <Pencil size={12} />
                {t("research.renameProject", { defaultValue: "Rename" })}
              </button>
              {selectedProject.archivedAt ? (
                <button
                  type="button"
                  onClick={() => void onRestoreProject()}
                  disabled={projectMutating}
                  className="touch-target inline-flex h-8 items-center gap-1 rounded-input px-2 text-caption text-muted hover:bg-surface hover:text-text disabled:opacity-40"
                >
                  {projectMutating ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />}
                  {t("research.restoreProject", { defaultValue: "Restore" })}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => setConfirmingArchive(true)}
                  disabled={projectMutating}
                  className="touch-target inline-flex h-8 items-center gap-1 rounded-input px-2 text-caption text-muted hover:bg-surface hover:text-text disabled:opacity-40"
                >
                  <Archive size={12} />
                  {t("research.archiveProject", { defaultValue: "Archive" })}
                </button>
              )}
            </div>
          )}
        </div>
        {editingProject && selectedProject && (
          <form
            className="mt-2 rounded-input border border-border bg-surface p-2"
            onSubmit={(event) => {
              event.preventDefault();
              void Promise.resolve(onRenameProject(renameDraft)).then(() => setEditingProject(false));
            }}
          >
            <label htmlFor="research-project-rename" className="sr-only">
              {t("research.renameProjectAria", { defaultValue: "Rename project" })}
            </label>
            <input
              id="research-project-rename"
              value={renameDraft}
              onChange={(event) => setRenameDraft(event.target.value)}
              autoFocus
              disabled={projectMutating}
              className="min-h-9 w-full rounded-input border border-border bg-bg px-2.5 py-2 text-xs text-text focus:border-accent"
            />
            <div className="mt-2 flex justify-end gap-1">
              <button type="button" onClick={() => setEditingProject(false)} disabled={projectMutating} className="touch-target inline-flex h-8 items-center gap-1 rounded-input px-2 text-caption text-muted hover:bg-bg disabled:opacity-40">
                <X size={12} />
                {t("common.cancel", { defaultValue: "Cancel" })}
              </button>
              <button type="submit" disabled={projectMutating || !renameDraft.trim()} className="touch-target inline-flex h-8 items-center gap-1 rounded-input bg-accent px-2 text-caption font-medium text-white hover:bg-accent/90 disabled:opacity-40">
                {projectMutating ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                {t("common.save", { defaultValue: "Save" })}
              </button>
            </div>
          </form>
        )}
        {confirmingArchive && selectedProject && (
          <div className="mt-2 rounded-input border border-warn/40 bg-warn/5 p-2 text-caption text-text" role="alert">
            <p>{t("research.archiveProjectConfirm", { defaultValue: "Archive this project? Its sources, tasks, and artifacts will be kept." })}</p>
            <div className="mt-2 flex justify-end gap-1">
              <button type="button" onClick={() => setConfirmingArchive(false)} disabled={projectMutating} className="touch-target inline-flex h-8 items-center gap-1 rounded-input px-2 text-caption text-muted hover:bg-bg disabled:opacity-40">
                <X size={12} />
                {t("common.cancel", { defaultValue: "Cancel" })}
              </button>
              <button type="button" onClick={() => { void Promise.resolve(onArchiveProject()).then(() => setConfirmingArchive(false)); }} disabled={projectMutating} className="touch-target inline-flex h-8 items-center gap-1 rounded-input bg-warn px-2 text-caption font-medium text-white hover:bg-warn/90 disabled:opacity-40">
                {projectMutating ? <Loader2 size={12} className="animate-spin" /> : <Archive size={12} />}
                {t("research.confirmArchiveProject", { defaultValue: "Archive project" })}
              </button>
            </div>
          </div>
        )}
      </div>

      <ResearchStageRail
        projectReady={Boolean(projectId)}
        sourceCount={readySourceCount}
        snapshot={snapshot}
      />

      <ResearchWorkflowList
        workflows={workflows}
        selectedWorkflowId={selectedWorkflowId}
        loading={loadingWorkflows}
        disabled={workflowMutating || !projectId || !serviceReady}
        onSelect={onSelectWorkflow}
        onOpenReport={onOpenWorkflowReport}
        onNew={onNewWorkflow}
      />

      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex items-center justify-between px-4 pb-2 pt-3">
          <span className="text-xs font-medium text-muted">
            {t("research.sourcesHeading", {
              defaultValue: "Sources ({{count}})",
              count: sources.length,
            })}
          </span>
          {loadingSources && <Loader2 size={12} className="animate-spin text-muted" />}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {!loadingSources && projectId && sources.length === 0 && (
            <p className="px-2 py-3 text-xs leading-relaxed text-muted">
              {t("research.noSources", {
                defaultValue: "Import a paper to build a searchable evidence library.",
              })}
            </p>
          )}
          {sources.map((source) => (
            <SourceRow
              key={source.id}
              source={source}
              selected={selection?.sourceId === source.id}
              onSelect={onSelectSource}
            />
          ))}
        </div>
        <input
          ref={fileInput}
          type="file"
          accept={PDF_ACCEPT}
          onChange={(event) => void onImportPdf(event)}
          className="hidden"
        />
        {projectId && readySourceCount > 0 && (
          <div className="border-t border-border p-3">
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              disabled={!serviceReady || importing}
              className="touch-target flex min-h-10 w-full items-center justify-center gap-1.5 rounded-input border border-border bg-surface px-2.5 py-2 text-xs font-medium text-text hover:bg-bg disabled:bg-surface-2 disabled:text-muted"
            >
              {importing ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
              {importing
                ? t("research.importing", { defaultValue: "Indexing PDF…" })
                : t("research.importPdf", { defaultValue: "Import PDF" })}
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}

function SourceRow({
  source,
  selected,
  onSelect,
}: {
  source: ResearchSource;
  selected: boolean;
  onSelect: (source: ResearchSource) => void;
}) {
  const { t } = useTranslation("pages");
  const previewable = source.sourceKind === "pdf";
  const content = (
    <>
      <ResearchObjectIcon
        kind={source.sourceKind === "dataset" ? RESEARCH_OBJECT_KIND.dataset : RESEARCH_OBJECT_KIND.pdf}
        size={15}
        className="mt-0.5"
      />
      <span className="min-w-0 flex-1">
        <span className="block line-clamp-2 text-xs font-medium leading-snug text-text">
          {source.title}
        </span>
        <span className="mt-1 flex flex-wrap items-center gap-x-1 gap-y-0.5 text-caption text-muted">
          <span>
            {t(`research.sourceKind.${source.sourceKind}`, {
              defaultValue: source.sourceKind,
            })}
          </span>
          <span aria-hidden="true">·</span>
          <SourceStatus source={source} />
          {source.pageCount != null && (
            <>
              <span aria-hidden="true">·</span>
              {t("research.pageCount", {
                defaultValue: "{{count}} pages",
                count: source.pageCount,
              })}
            </>
          )}
        </span>
      </span>
      {previewable && (
        <ChevronRight
          size={13}
          className="mt-0.5 shrink-0 text-muted opacity-0 group-focus-visible:opacity-100 group-hover:opacity-100"
        />
      )}
    </>
  );

  if (!previewable) {
    return (
      <div className="flex min-h-12 w-full items-start gap-2 rounded-input px-2 py-2.5 text-left">
        {content}
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onSelect(source)}
      className={cn(
        "group flex min-h-12 w-full items-start gap-2 rounded-input px-2 py-2.5 text-left transition-colors",
        selected ? "bg-surface text-text" : "hover:bg-surface",
      )}
    >
      {content}
    </button>
  );
}

function HealthBadge({ health, loading }: { health: ScienceCoreHealth | null; loading: boolean }) {
  const { t } = useTranslation("pages");
  const available = health?.status === "ok";
  const degraded = health?.status === "degraded";
  return (
    <div className="mt-1.5 flex items-center gap-1.5 text-caption text-muted">
      {loading ? (
        <Loader2 size={11} className="animate-spin" />
      ) : (
        <span className={cn("h-1.5 w-1.5 rounded-full", available ? "bg-ok" : degraded ? "bg-warn" : "bg-error")} />
      )}
      {loading
        ? t("research.health.connecting", { defaultValue: "Connecting to science core…" })
        : available
          ? t("research.health.ready", { defaultValue: "Science core ready" })
          : degraded
            ? t("research.health.degraded", { defaultValue: "Science core degraded" })
            : t("research.health.offline", { defaultValue: "Science core offline" })}
    </div>
  );
}

function SourceStatus({ source }: { source: ResearchSource }) {
  const { t } = useTranslation("pages");
  const ready = source.ingestionStatus === "ready";
  const failed = source.ingestionStatus === "failed";
  return (
    <span className={cn("inline-flex items-center gap-1", failed && "text-error")}>
      <span className={cn("h-1.5 w-1.5 rounded-full", ready ? "bg-ok" : failed ? "bg-error" : "bg-warn")} />
      {t(`research.sourceStatus.${source.ingestionStatus}`, {
        defaultValue:
          source.ingestionStatus === "processing"
            ? "processing"
            : source.ingestionStatus === "pending"
              ? "pending"
              : source.ingestionStatus === "failed"
                ? "failed"
                : "ready",
      })}
    </span>
  );
}
