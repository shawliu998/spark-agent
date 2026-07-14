import { useRef } from "react";
import { useTranslation } from "react-i18next";
import {
  ChevronRight,
  FileText,
  Library,
  Loader2,
  Plus,
  Upload,
} from "lucide-react";
import type {
  ResearchProject,
  ResearchSource,
  ResearchWorkflowSnapshot,
  ScienceCoreHealth,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";
import type { ResearchPdfSelection } from "./ResearchInspector";
import { ResearchWorkflowList } from "./ResearchWorkflowList";

const PDF_ACCEPT = ".pdf,application/pdf";

export interface ResearchLibrarySidebarProps {
  health: ScienceCoreHealth | null;
  booting: boolean;
  serviceReady: boolean;
  projects: ResearchProject[];
  projectId: string | null;
  projectTitle: string;
  showProjectForm: boolean;
  creatingProject: boolean;
  workflows: ResearchWorkflowSnapshot[];
  selectedWorkflowId: string | null;
  loadingWorkflows: boolean;
  sources: ResearchSource[];
  loadingSources: boolean;
  selection: ResearchPdfSelection | null;
  importing: boolean;
  onProjectChange: (projectId: string | null) => void;
  onProjectTitleChange: (title: string) => void;
  onProjectFormToggle: () => void;
  onCreateProject: (event: React.FormEvent) => void | Promise<void>;
  onSelectWorkflow: (workflowId: string) => void;
  onNewWorkflow: () => void;
  onSelectSource: (source: ResearchSource) => void;
  onImportPdf: (event: React.ChangeEvent<HTMLInputElement>) => void | Promise<void>;
}

export function ResearchLibrarySidebar({
  health,
  booting,
  serviceReady,
  projects,
  projectId,
  projectTitle,
  showProjectForm,
  creatingProject,
  workflows,
  selectedWorkflowId,
  loadingWorkflows,
  sources,
  loadingSources,
  selection,
  importing,
  onProjectChange,
  onProjectTitleChange,
  onProjectFormToggle,
  onCreateProject,
  onSelectWorkflow,
  onNewWorkflow,
  onSelectSource,
  onImportPdf,
}: ResearchLibrarySidebarProps) {
  const { t } = useTranslation("pages");
  const fileInput = useRef<HTMLInputElement>(null);
  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-surface xl:w-64">
      <div className="border-b border-border px-4 py-4">
        <div className="flex items-center gap-2">
          <Library size={16} className="text-muted" />
          <h1 className="font-serif text-lg text-text">
            {t("research.libraryTitle", { defaultValue: "Research library" })}
          </h1>
        </div>
        <HealthBadge health={health} loading={booting} />
      </div>

      <div className="border-b border-border p-3">
        <div className="mb-1.5 flex items-center justify-between">
          <label htmlFor="research-project" className="text-[11px] font-medium uppercase tracking-wider text-muted">
            {t("research.projectLabel", { defaultValue: "Project" })}
          </label>
          <button
            type="button"
            onClick={onProjectFormToggle}
            disabled={!serviceReady}
            className="rounded p-1 text-muted hover:bg-surface-2 hover:text-text disabled:opacity-40"
            aria-label={t("research.newProjectAria", { defaultValue: "Create research project" })}
          >
            <Plus size={14} />
          </button>
        </div>
        {projects.length > 0 ? (
          <select
            id="research-project"
            value={projectId ?? ""}
            onChange={(event) => onProjectChange(event.target.value || null)}
            className="w-full rounded-input border border-border bg-bg px-2.5 py-1.5 text-[13px] text-text outline-none focus:border-accent"
          >
            {projects.map((project) => (
              <option key={project.id} value={project.id}>{project.title}</option>
            ))}
          </select>
        ) : (
          !booting && (
            <p className="text-xs leading-relaxed text-muted">
              {t("research.noProjects", { defaultValue: "Create a project to begin." })}
            </p>
          )
        )}

        {(showProjectForm || (!booting && projects.length === 0)) && (
          <form onSubmit={(event) => void onCreateProject(event)} className="mt-2 space-y-2">
            <input
              value={projectTitle}
              onChange={(event) => onProjectTitleChange(event.target.value)}
              placeholder={t("research.projectNamePlaceholder", { defaultValue: "Project name" })}
              autoFocus={showProjectForm}
              className="w-full rounded-input border border-border bg-bg px-2.5 py-1.5 text-[13px] text-text outline-none placeholder:text-muted focus:border-accent"
            />
            <button
              type="submit"
              disabled={!projectTitle.trim() || creatingProject || !serviceReady}
              className="flex w-full items-center justify-center gap-1.5 rounded-input bg-accent px-2.5 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-40"
            >
              {creatingProject && <Loader2 size={12} className="animate-spin" />}
              {t("research.createProject", { defaultValue: "Create project" })}
            </button>
          </form>
        )}
      </div>

      <ResearchWorkflowList
        workflows={workflows}
        selectedWorkflowId={selectedWorkflowId}
        loading={loadingWorkflows}
        disabled={!projectId || !serviceReady}
        onSelect={onSelectWorkflow}
        onNew={onNewWorkflow}
      />

      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex items-center justify-between px-4 pb-2 pt-3">
          <span className="text-[11px] font-medium uppercase tracking-wider text-muted">
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
            <button
              key={source.id}
              type="button"
              onClick={() => onSelectSource(source)}
              className={cn(
                "group flex w-full items-start gap-2 rounded-input px-2 py-2 text-left hover:bg-surface-2",
                selection?.sourceId === source.id && "bg-surface-2",
              )}
            >
              <FileText size={15} className="mt-0.5 shrink-0 text-muted" />
              <span className="min-w-0 flex-1">
                <span className="block line-clamp-2 text-xs font-medium leading-snug text-text">
                  {source.title}
                </span>
                <span className="mt-1 flex items-center gap-1 text-[10px] text-muted">
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
              <ChevronRight size={13} className="mt-0.5 shrink-0 text-muted opacity-0 group-hover:opacity-100" />
            </button>
          ))}
        </div>
        <div className="border-t border-border p-3">
          <input
            ref={fileInput}
            type="file"
            accept={PDF_ACCEPT}
            onChange={(event) => void onImportPdf(event)}
            className="hidden"
          />
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            disabled={!projectId || !serviceReady || importing}
            className="flex w-full items-center justify-center gap-1.5 rounded-input border border-border bg-bg px-2.5 py-1.5 text-xs text-text hover:bg-surface-2 disabled:opacity-40"
          >
            {importing ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
            {importing
              ? t("research.importing", { defaultValue: "Indexing PDF…" })
              : t("research.importPdf", { defaultValue: "Import PDF" })}
          </button>
        </div>
      </div>
    </aside>
  );
}

function HealthBadge({ health, loading }: { health: ScienceCoreHealth | null; loading: boolean }) {
  const { t } = useTranslation("pages");
  const available = health?.status === "ok";
  const degraded = health?.status === "degraded";
  return (
    <div className="mt-2 flex items-center gap-1.5 text-[11px] text-muted">
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
