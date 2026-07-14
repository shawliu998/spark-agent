import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  ChevronRight,
  FileText,
  Library,
  Loader2,
  Plus,
  Quote,
  RefreshCw,
  Search,
  Upload,
} from "lucide-react";
import type {
  EvidenceSpan,
  ResearchAnswer,
  ResearchProject,
  ResearchSource,
  ScienceCoreHealth,
} from "@spark/research-domain";
import { MarkdownViewer } from "@/components/markdown-viewer/MarkdownViewer";
import { cn } from "@/lib/cn";
import { scienceCore } from "@/lib/scienceCore";
import { toast } from "@/lib/toast";

const PDF_ACCEPT = ".pdf,application/pdf";

interface PdfSelection {
  sourceId: string;
  pageIndex: number;
  evidenceId?: string;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function percent(value: number): string {
  const normalized = value > 1 ? value / 100 : value;
  return new Intl.NumberFormat(undefined, {
    style: "percent",
    maximumFractionDigits: 0,
  }).format(Math.max(0, Math.min(1, normalized)));
}

/**
 * Evidence-first literature workspace backed by the local science-core API.
 * This page owns presentation state only; projects, sources, claims, and
 * evidence remain canonical in science-core.
 */
export function ResearchPage() {
  const { t } = useTranslation("pages");
  const fileInput = useRef<HTMLInputElement>(null);
  const [health, setHealth] = useState<ScienceCoreHealth | null>(null);
  const [projects, setProjects] = useState<ResearchProject[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [sources, setSources] = useState<ResearchSource[]>([]);
  const [answer, setAnswer] = useState<ResearchAnswer | null>(null);
  const [pdfSelection, setPdfSelection] = useState<PdfSelection | null>(null);
  const [question, setQuestion] = useState("");
  const [remoteDataApproved, setRemoteDataApproved] = useState(false);
  const [projectTitle, setProjectTitle] = useState("");
  const [showProjectForm, setShowProjectForm] = useState(false);
  const [booting, setBooting] = useState(true);
  const [loadingSources, setLoadingSources] = useState(false);
  const [creatingProject, setCreatingProject] = useState(false);
  const [importing, setImporting] = useState(false);
  const [asking, setAsking] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);
  const [sourceRefresh, setSourceRefresh] = useState(0);

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
      setSourceRefresh((version) => version + 1);
      setProjectId((current) =>
        nextProjects.some((project) => project.id === current)
          ? current
          : nextProjects[0]?.id ?? null,
      );
    } catch (error) {
      setHealth(null);
      setPageError(message(error));
    } finally {
      setBooting(false);
    }
  }, []);

  useEffect(() => {
    void loadWorkspace();
  }, [loadWorkspace]);

  useEffect(() => {
    let cancelled = false;
    setAnswer(null);
    setPdfSelection(null);
    setRemoteDataApproved(false);
    if (!projectId) {
      setSources([]);
      return;
    }

    setLoadingSources(true);
    void scienceCore
      .listSources(projectId)
      .then((nextSources) => {
        if (!cancelled) setSources(nextSources);
      })
      .catch((error) => {
        if (!cancelled) {
          setSources([]);
          toast.error(
            t("research.toast.loadSourcesFailed", {
              defaultValue: "Could not load sources: {{error}}",
              error: message(error),
            }),
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingSources(false);
      });

    return () => {
      cancelled = true;
    };
  }, [projectId, sourceRefresh, t]);

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === projectId) ?? null,
    [projectId, projects],
  );
  const selectedSource = useMemo(
    () =>
      sources.find(
        (source) =>
          source.sourceKind === "pdf" && source.id === pdfSelection?.sourceId,
      ) ?? null,
    [pdfSelection?.sourceId, sources],
  );
  const paperSources = sources.filter((source) => source.sourceKind === "pdf");
  const readySources = paperSources.filter(
    (source) => source.ingestionStatus === "ready",
  );
  const serviceReady = health?.database === "ok";
  const literatureReady =
    serviceReady &&
    health?.paperQa === "available" &&
    health.modelGateway === "configured";

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
        t("research.toast.projectCreated", {
          defaultValue: "Research project created.",
        }),
      );
    } catch (error) {
      toast.error(
        t("research.toast.createProjectFailed", {
          defaultValue: "Could not create project: {{error}}",
          error: message(error),
        }),
      );
    } finally {
      setCreatingProject(false);
    }
  };

  const importPdf = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !projectId || !serviceReady) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      toast.error(
        t("research.toast.pdfOnly", {
          defaultValue: "Choose a PDF file.",
        }),
      );
      return;
    }

    setImporting(true);
    try {
      const source = await scienceCore.importPdf(projectId, file);
      setSources((current) => [source, ...current.filter((item) => item.id !== source.id)]);
      setPdfSelection({ sourceId: source.id, pageIndex: 0 });
      toast.success(
        t("research.toast.sourceImported", {
          defaultValue: "PDF imported and indexed.",
        }),
      );
    } catch (error) {
      toast.error(
        t("research.toast.importFailed", {
          defaultValue: "Could not import PDF: {{error}}",
          error: message(error),
        }),
      );
    } finally {
      setImporting(false);
    }
  };

  const ask = async (event: React.FormEvent) => {
    event.preventDefault();
    const nextQuestion = question.trim();
    if (
      !projectId ||
      !nextQuestion ||
      !literatureReady ||
      readySources.length === 0 ||
      !remoteDataApproved
    )
      return;
    setAsking(true);
    try {
      const result = await scienceCore.ask(projectId, {
        question: nextQuestion,
        remoteDataApproved: true,
      });
      setAnswer(result);
      const firstEvidence = result.claims.flatMap((claim) => claim.evidence)[0];
      if (firstEvidence) selectEvidence(firstEvidence);
    } catch (error) {
      toast.error(
        t("research.toast.askFailed", {
          defaultValue: "Could not answer the question: {{error}}",
          error: message(error),
        }),
      );
    } finally {
      setRemoteDataApproved(false);
      setAsking(false);
    }
  };

  const selectEvidence = (evidence: EvidenceSpan) => {
    setPdfSelection({
      sourceId: evidence.sourceId,
      pageIndex: evidence.pageIndex,
      evidenceId: evidence.id,
    });
  };

  const pdfUrl = selectedSource
    ? scienceCore.sourceFileUrl(selectedSource.id, pdfSelection?.pageIndex ?? 0)
    : null;

  return (
    <div className="flex h-full min-h-0 overflow-hidden">
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
              onClick={() => setShowProjectForm((open) => !open)}
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
              onChange={(event) => setProjectId(event.target.value || null)}
              className="w-full rounded-input border border-border bg-bg px-2.5 py-1.5 text-[13px] text-text outline-none focus:border-accent"
            >
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.title}
                </option>
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
            <form onSubmit={(event) => void createProject(event)} className="mt-2 space-y-2">
              <input
                value={projectTitle}
                onChange={(event) => setProjectTitle(event.target.value)}
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

        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center justify-between px-4 pb-2 pt-3">
            <span className="text-[11px] font-medium uppercase tracking-wider text-muted">
              {t("research.sourcesHeading", {
                defaultValue: "Sources ({{count}})",
                count: paperSources.length,
              })}
            </span>
            {loadingSources && <Loader2 size={12} className="animate-spin text-muted" />}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
            {!loadingSources && projectId && paperSources.length === 0 && (
              <p className="px-2 py-3 text-xs leading-relaxed text-muted">
                {t("research.noSources", {
                  defaultValue: "Import a paper to build a searchable evidence library.",
                })}
              </p>
            )}
            {paperSources.map((source) => (
              <button
                key={source.id}
                type="button"
                onClick={() => setPdfSelection({ sourceId: source.id, pageIndex: 0 })}
                className={cn(
                  "group flex w-full items-start gap-2 rounded-input px-2 py-2 text-left hover:bg-surface-2",
                  pdfSelection?.sourceId === source.id && "bg-surface-2",
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
              onChange={(event) => void importPdf(event)}
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

      <main className="flex min-w-[21rem] flex-1 flex-col bg-bg">
        <header className="shrink-0 border-b border-border px-6 py-4">
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <h2 className="font-serif text-xl text-text">
                {selectedProject?.title ?? t("research.title", { defaultValue: "Research" })}
              </h2>
              <p className="mt-0.5 text-xs text-muted">
                {selectedProject?.description ||
                  t("research.subtitle", {
                    defaultValue: "Ask questions grounded in page-level evidence from your papers.",
                  })}
              </p>
            </div>
            <button
              type="button"
              onClick={() => void loadWorkspace()}
              disabled={booting}
              className="rounded p-1.5 text-muted hover:bg-surface-2 hover:text-text disabled:opacity-40"
              aria-label={t("research.refreshAria", { defaultValue: "Refresh research workspace" })}
            >
              <RefreshCw size={14} className={cn(booting && "animate-spin")} />
            </button>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 xl:px-7">
          {pageError && (
            <div className="mb-4 flex items-start gap-3 rounded-card border border-error/30 bg-error/5 p-4 text-sm">
              <AlertTriangle size={17} className="mt-0.5 shrink-0 text-error" />
              <div className="min-w-0 flex-1">
                <p className="font-medium text-text">
                  {t("research.offlineTitle", { defaultValue: "Science core is offline" })}
                </p>
                <p className="mt-1 break-words text-xs text-muted">{pageError}</p>
              </div>
              <button
                type="button"
                onClick={() => void loadWorkspace()}
                className="text-xs text-link hover:underline"
              >
                {t("research.retry", { defaultValue: "Retry" })}
              </button>
            </div>
          )}

          {health?.paperQa === "unavailable" && (
            <div className="mb-4 flex items-start gap-2 rounded-card border border-warn/30 bg-warn/5 px-3 py-2.5 text-xs text-muted">
              <AlertTriangle size={15} className="shrink-0 text-warn" />
              {t("research.paperQaUnavailable", {
                defaultValue: "PaperQA is not available. PDF import works, but evidence-grounded questions are paused.",
              })}
            </div>
          )}

          {health?.paperQa === "available" && health.modelGateway === "unconfigured" && (
            <div className="mb-4 flex items-start gap-2 rounded-card border border-warn/30 bg-warn/5 px-3 py-2.5 text-xs text-muted">
              <AlertTriangle size={15} className="shrink-0 text-warn" />
              {t("research.modelGatewayUnconfigured", {
                defaultValue:
                  "PaperQA is installed, but its model gateway is not configured. Set OPENAI_API_KEY before starting science core.",
              })}
            </div>
          )}

          <form onSubmit={(event) => void ask(event)} className="rounded-card border border-border bg-surface p-3 shadow-card">
            <label htmlFor="research-question" className="text-xs font-medium text-text">
              {t("research.questionLabel", { defaultValue: "Research question" })}
            </label>
            <textarea
              id="research-question"
              value={question}
              onChange={(event) => {
                setQuestion(event.target.value);
                setRemoteDataApproved(false);
              }}
              rows={3}
              placeholder={t("research.questionPlaceholder", {
                defaultValue: "What does the evidence say about…?",
              })}
              className="mt-2 w-full resize-y bg-transparent text-sm leading-relaxed text-text outline-none placeholder:text-muted"
            />
            {literatureReady && readySources.length > 0 && (
              <label className="mt-2 flex cursor-pointer items-start gap-2 rounded-input border border-warn/25 bg-warn/5 px-3 py-2.5 text-[11px] leading-relaxed text-muted">
                <input
                  type="checkbox"
                  checked={remoteDataApproved}
                  onChange={(event) => setRemoteDataApproved(event.target.checked)}
                  className="mt-0.5 h-3.5 w-3.5 accent-[var(--color-accent)]"
                />
                <span>
                  {t("research.remoteApproval", {
                    defaultValue:
                      "For this request, allow the configured remote model gateway to receive the question and PDF text needed for embedding, retrieval, and answering.",
                  })}
                </span>
              </label>
            )}
            <div className="mt-2 flex items-center gap-3 border-t border-border-faint pt-2">
              <span className="min-w-0 flex-1 truncate text-[11px] text-muted">
                {readySources.length > 0
                  ? t("research.readySourceCount", {
                      defaultValue: "{{count}} indexed sources",
                      count: readySources.length,
                    })
                  : t("research.needsSource", { defaultValue: "Import an indexed PDF to ask a question." })}
              </span>
              <button
                type="submit"
                disabled={!question.trim() || !projectId || !literatureReady || readySources.length === 0 || !remoteDataApproved || asking}
                className="flex shrink-0 items-center gap-1.5 rounded-input bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-40"
              >
                {asking ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />}
                {asking
                  ? t("research.searching", { defaultValue: "Building evidence…" })
                  : t("research.ask", { defaultValue: "Ask library" })}
              </button>
            </div>
          </form>

          {asking && !answer && (
            <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted">
              <Loader2 size={15} className="animate-spin" />
              {t("research.searchingSources", { defaultValue: "Searching and verifying source passages…" })}
            </div>
          )}

          {!asking && !answer && (
            <EmptyResearchState hasProject={Boolean(projectId)} hasSources={readySources.length > 0} />
          )}

          {answer && (
            <AnswerView
              answer={answer}
              sources={paperSources}
              selection={pdfSelection}
              onSelectEvidence={selectEvidence}
            />
          )}
        </div>
      </main>

      <section className="flex w-[38%] min-w-[18rem] shrink-0 flex-col border-l border-border bg-surface xl:w-[44%]">
        <div className="flex h-[53px] shrink-0 items-center gap-2 border-b border-border px-4">
          <BookOpen size={15} className="shrink-0 text-muted" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-medium text-text">
              {selectedSource?.title ?? t("research.previewTitle", { defaultValue: "Source preview" })}
            </p>
            {selectedSource && (
              <p className="text-[10px] text-muted">
                {t("research.previewPage", {
                  defaultValue: "Page {{page}}",
                  page: (pdfSelection?.pageIndex ?? 0) + 1,
                })}
              </p>
            )}
          </div>
          {pdfSelection?.evidenceId && (
            <span className="rounded-full bg-ok/10 px-2 py-0.5 text-[10px] font-medium text-ok ring-1 ring-ok/20">
              {t("research.evidenceBadge", { defaultValue: "Evidence" })}
            </span>
          )}
        </div>
        <div className="min-h-0 flex-1 bg-surface-2">
          {pdfUrl && selectedSource ? (
            <iframe
              key={`${selectedSource.id}:${pdfSelection?.pageIndex ?? 0}`}
              src={pdfUrl}
              title={t("research.pdfTitle", {
                defaultValue: "PDF preview for {{title}}",
                title: selectedSource.title,
              })}
              className="h-full w-full border-0 bg-white"
            />
          ) : (
            <div className="flex h-full flex-col items-center justify-center px-6 text-center">
              <BookOpen size={25} strokeWidth={1.4} className="text-muted" />
              <p className="mt-3 text-sm font-medium text-text">
                {t("research.previewEmptyTitle", { defaultValue: "Open a source" })}
              </p>
              <p className="mt-1 max-w-xs text-xs leading-relaxed text-muted">
                {t("research.previewEmptyBody", {
                  defaultValue: "Select a paper or click an evidence quote to jump to its page.",
                })}
              </p>
            </div>
          )}
        </div>
      </section>
    </div>
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
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full",
            available ? "bg-ok" : degraded ? "bg-warn" : "bg-error",
          )}
        />
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

function EmptyResearchState({ hasProject, hasSources }: { hasProject: boolean; hasSources: boolean }) {
  const { t } = useTranslation("pages");
  return (
    <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
      <Quote size={24} strokeWidth={1.4} className="text-muted" />
      <p className="mt-3 text-sm font-medium text-text">
        {!hasProject
          ? t("research.empty.createTitle", { defaultValue: "Create a research project" })
          : !hasSources
            ? t("research.empty.importTitle", { defaultValue: "Build your evidence library" })
            : t("research.empty.askTitle", { defaultValue: "Ask a research question" })}
      </p>
      <p className="mt-1 max-w-md text-xs leading-relaxed text-muted">
        {!hasProject
          ? t("research.empty.createBody", { defaultValue: "Projects keep sources, questions, and evidence together." })
          : !hasSources
            ? t("research.empty.importBody", { defaultValue: "Import one or more PDF papers. Text and page locations stay local." })
            : t("research.empty.askBody", { defaultValue: "Answers are split into claims, each linked back to exact source passages." })}
      </p>
    </div>
  );
}

function AnswerView({
  answer,
  sources,
  selection,
  onSelectEvidence,
}: {
  answer: ResearchAnswer;
  sources: ResearchSource[];
  selection: PdfSelection | null;
  onSelectEvidence: (evidence: EvidenceSpan) => void;
}) {
  const { t } = useTranslation("pages");
  const hasVerifiedEvidence = answer.claims.some((claim) =>
    claim.evidence.some((evidence) => evidence.verified),
  );
  return (
    <article className="mt-5 space-y-4">
      <section className="rounded-card border border-border bg-surface p-4 shadow-card">
        <div className="mb-3 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-muted">
          {hasVerifiedEvidence ? (
            <CheckCircle2 size={14} className="text-ok" />
          ) : (
            <AlertTriangle size={14} className="text-warn" />
          )}
          {hasVerifiedEvidence
            ? t("research.answerHeading", { defaultValue: "Evidence-grounded answer" })
            : t("research.unverifiedAnswerHeading", {
                defaultValue: "Answer — evidence needs review",
              })}
        </div>
        <MarkdownViewer>{answer.answer}</MarkdownViewer>
      </section>

      <div className="flex items-center gap-2 pt-1">
        <h3 className="text-xs font-medium uppercase tracking-wider text-muted">
          {t("research.claimsHeading", {
            defaultValue: "Claims ({{count}})",
            count: answer.claims.length,
          })}
        </h3>
        <div className="h-px flex-1 bg-border" />
      </div>

      {answer.claims.map((claim, index) => (
        <section key={claim.id} className="rounded-card border border-border bg-surface p-4">
          <div className="flex items-start gap-3">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface-2 text-[10px] font-semibold text-muted ring-1 ring-border">
              {index + 1}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium leading-relaxed text-text">{claim.statement}</p>
              <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px]">
                <span className="rounded-full bg-surface-2 px-2 py-0.5 text-muted ring-1 ring-border">
                  {t("research.confidence", {
                    defaultValue: "{{value}} confidence",
                    value: percent(claim.confidence),
                  })}
                </span>
                <span
                  className={cn(
                    "rounded-full px-2 py-0.5 ring-1",
                    claim.reviewStatus === "verified"
                      ? "bg-ok/10 text-ok ring-ok/20"
                      : claim.reviewStatus === "rejected"
                        ? "bg-error/10 text-error ring-error/20"
                        : "bg-warn/10 text-warn ring-warn/20",
                  )}
                >
                  {t(`research.reviewStatus.${claim.reviewStatus}`, {
                    defaultValue: claim.reviewStatus,
                  })}
                </span>
              </div>
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
              const active = selection?.evidenceId === evidence.id;
              const sourceTitle =
                sources.find((source) => source.id === evidence.sourceId)?.title ??
                t("research.unknownSource", { defaultValue: "Unknown source" });
              return (
                <button
                  key={evidence.id}
                  type="button"
                  onClick={() => onSelectEvidence(evidence)}
                  className={cn(
                    "group w-full rounded-input border px-3 py-2.5 text-left transition-colors",
                    active
                      ? "border-accent/50 bg-accent/5"
                      : "border-border bg-bg hover:border-accent/30 hover:bg-surface-2",
                  )}
                >
                  <span className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-wide text-muted">
                    <Quote size={12} className="text-accent" />
                    {t("research.evidencePage", {
                      defaultValue: "{{source}}, page {{page}}",
                      source: sourceTitle,
                      page: evidence.pageLabel ?? evidence.pageIndex + 1,
                    })}
                    <span className="ml-auto normal-case tracking-normal">
                      {evidence.verified
                        ? t("research.verified", { defaultValue: "verified" })
                        : t("research.needsReview", { defaultValue: "needs review" })}
                    </span>
                    <ChevronRight size={12} className="transition-transform group-hover:translate-x-0.5" />
                  </span>
                  <span className="mt-1.5 block line-clamp-4 font-serif text-[13px] leading-relaxed text-text/90">
                    {evidence.text}
                  </span>
                </button>
              );
            })}
          </div>
        </section>
      ))}

      {answer.unresolvedQuestions.length > 0 && (
        <section className="rounded-card border border-warn/30 bg-warn/5 p-4">
          <h3 className="flex items-center gap-2 text-xs font-medium text-text">
            <AlertTriangle size={14} className="text-warn" />
            {t("research.unresolvedHeading", { defaultValue: "Unresolved questions" })}
          </h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed text-muted">
            {answer.unresolvedQuestions.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}
