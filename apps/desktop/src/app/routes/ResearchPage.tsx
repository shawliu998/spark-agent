import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, RefreshCw } from "lucide-react";
import type {
  EvidenceSpan,
  ResearchAnswer,
  ResearchProject,
  ResearchSource,
  ScienceCoreHealth,
  WorkflowEvidenceRelationship,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";
import { scienceCore } from "@/lib/scienceCore";
import { toast } from "@/lib/toast";
import {
  ResearchInspector,
  type ResearchInspectorTab,
  type ResearchPdfSelection,
} from "./research/ResearchInspector";
import { LegacyQuestionPanel } from "./research/LegacyQuestionPanel";
import { ResearchLibrarySidebar } from "./research/ResearchLibrarySidebar";
import { useResearchWorkflow } from "./research/useResearchWorkflow";
import { WorkflowWorkspace } from "./research/WorkflowWorkspace";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Evidence-first literature workspace backed by the local science-core API.
 * This page owns presentation state only; projects, sources, claims, and
 * evidence remain canonical in science-core.
 */
export function ResearchPage() {
  const { t } = useTranslation("pages");
  const [health, setHealth] = useState<ScienceCoreHealth | null>(null);
  const [projects, setProjects] = useState<ResearchProject[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [sources, setSources] = useState<ResearchSource[]>([]);
  const [answer, setAnswer] = useState<ResearchAnswer | null>(null);
  const [pdfSelection, setPdfSelection] = useState<ResearchPdfSelection | null>(null);
  const [inspectorTab, setInspectorTab] = useState<ResearchInspectorTab>("evidence");
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
  const sourcesProjectIdRef = useRef<string | null>(null);
  const workflow = useResearchWorkflow(projectId);

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
    setInspectorTab("evidence");
    setRemoteDataApproved(false);
    if (sourcesProjectIdRef.current !== projectId) {
      sourcesProjectIdRef.current = projectId;
      setSources([]);
    }
    if (!projectId) {
      setSources([]);
      setLoadingSources(false);
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
    health.modelGateway === "configured" &&
    health.modelDestination != null;
  const remoteDestinationApprovalKey = health?.modelDestination
    ? `${health.modelDestination.endpointIdentity}:${health.modelDestination.model}`
    : null;

  useEffect(() => {
    setRemoteDataApproved(false);
  }, [remoteDestinationApprovalKey]);

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
    setInspectorTab("evidence");
  };

  const selectWorkflowEvidence = (evidence: WorkflowEvidenceRelationship) => {
    setPdfSelection({
      sourceId: evidence.sourceId,
      pageIndex: evidence.pageIndex,
      evidenceId: evidence.evidenceId,
    });
    setInspectorTab("evidence");
  };

  const openReviewInspector = () => setInspectorTab("review");
  const openActivityInspector = () => setInspectorTab("activity");
  const selectSource = (source: ResearchSource) => {
    setPdfSelection({ sourceId: source.id, pageIndex: 0 });
    setInspectorTab("evidence");
  };

  return (
    <div className="flex h-full min-h-0 overflow-hidden">
      <ResearchLibrarySidebar
        health={health}
        booting={booting}
        serviceReady={serviceReady}
        projects={projects}
        projectId={projectId}
        projectTitle={projectTitle}
        showProjectForm={showProjectForm}
        creatingProject={creatingProject}
        workflows={workflow.workflows}
        selectedWorkflowId={workflow.selectedWorkflowId}
        loadingWorkflows={workflow.loadingList}
        sources={paperSources}
        loadingSources={loadingSources}
        selection={pdfSelection}
        importing={importing}
        onProjectChange={setProjectId}
        onProjectTitleChange={setProjectTitle}
        onProjectFormToggle={() => setShowProjectForm((open) => !open)}
        onCreateProject={createProject}
        onSelectWorkflow={workflow.selectWorkflow}
        onNewWorkflow={() => {
          workflow.startNew();
          setAnswer(null);
        }}
        onSelectSource={selectSource}
        onImportPdf={importPdf}
      />

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
                defaultValue:
                  "PaperQA is not available. Local workflows and PDF import remain available; legacy quick questions are paused.",
              })}
            </div>
          )}

          {health?.paperQa === "available" && health.modelGateway === "unconfigured" && (
            <div className="mb-4 flex items-start gap-2 rounded-card border border-warn/30 bg-warn/5 px-3 py-2.5 text-xs text-muted">
              <AlertTriangle size={15} className="shrink-0 text-warn" />
              {t("research.modelGatewayUnconfigured", {
                defaultValue:
                  "PaperQA is installed, but its model gateway is not configured. Local workflows remain available; legacy quick questions are paused.",
              })}
            </div>
          )}

          <WorkflowWorkspace
            snapshot={workflow.snapshot}
            sources={paperSources}
            loading={workflow.loadingSnapshot}
            mutating={workflow.mutating}
            connection={workflow.connection}
            error={workflow.error}
            canStart={Boolean(
              projectId && serviceReady && !loadingSources && readySources.length > 0,
            )}
            remoteDestination={health?.modelDestination ?? null}
            onCreate={workflow.create}
            onApprovePlan={workflow.approvePlan}
            onCancel={workflow.cancel}
            onRetry={workflow.retry}
            onResume={workflow.resume}
            onRefresh={workflow.refresh}
            onNew={() => {
              workflow.startNew();
              setAnswer(null);
            }}
            onSelectEvidence={selectWorkflowEvidence}
            onOpenReview={openReviewInspector}
            onOpenActivity={openActivityInspector}
            legacyContent={
              <LegacyQuestionPanel
                question={question}
                approved={remoteDataApproved}
                asking={asking}
                answer={answer}
                projectReady={Boolean(projectId)}
                literatureReady={literatureReady}
                remoteDestination={health?.modelDestination ?? null}
                sources={paperSources}
                readySourceCount={readySources.length}
                selection={pdfSelection}
                onQuestionChange={(value) => {
                  setQuestion(value);
                  setRemoteDataApproved(false);
                }}
                onApprovalChange={setRemoteDataApproved}
                onSubmit={ask}
                onSelectEvidence={selectEvidence}
              />
            }
          />
        </div>
      </main>

      <ResearchInspector
        activeTab={inspectorTab}
        onTabChange={setInspectorTab}
        selectedSource={selectedSource}
        selection={pdfSelection}
        review={workflow.snapshot?.latestReview ?? null}
        events={workflow.events}
      />
    </div>
  );
}
