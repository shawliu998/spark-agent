import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, PanelRightOpen, RefreshCw } from "lucide-react";
import type {
  CreateDiscoveryRunInput,
  CreateDiscoveryQueryInput,
  CreateExactEvidenceSpanInput,
  CandidateTriageDecision,
  DiscoveryCandidate,
  EvidenceDirectionJudgment,
  EvidenceSpan,
  ExtractionMatrix,
  UpsertExtractionCellInput,
  UpsertCandidateTriageDecisionInput,
  UpsertEvidenceDirectionJudgmentInput,
  CreateExtractionColumnInput,
  ResearchAnswer,
  ResearchProject,
  ResearchSource,
  ResearchWorkflowSnapshot,
  ResearchWorkflowResult,
  ScreeningDecision,
  UpsertScreeningDecisionInput,
  ScienceCoreHealth,
  WorkflowDiscoverySnapshot,
  WorkflowEvidenceCoverage,
  WorkflowEvidenceRelationship,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";

const LITERATURE_PRESENTATION = "competitive";
const DATASET_PRESENTATION = "dataset";
const WORKFLOW_PRESENTATION = "workflow";
// eslint-disable-next-line i18next/no-literal-string -- public product download URL
const DOCKER_DESKTOP_DOWNLOAD_URL = "https://www.docker.com/products/docker-desktop/";
import {
  getScienceCoreRuntimeStatus,
  retryScienceCoreRuntime,
  scienceCore,
  scienceCoreConfigurationError,
} from "@/lib/scienceCore";
import {
  openExternal,
  type ScienceCoreRuntimeStatus,
} from "@/lib/tauri";
import { toast } from "@/lib/toast";
import {
  ResearchInspector,
  type ResearchInspectorTab,
  type ResearchPdfSelection,
} from "./research/ResearchInspector";
import { LegacyQuestionPanel } from "./research/LegacyQuestionPanel";
import { ResearchLibrarySidebar } from "./research/ResearchLibrarySidebar";
import { useResearchWorkflow } from "./research/useResearchWorkflow";
import {
  canonicalAutonomousLiteratureIdentity,
  matchesAutonomousLiteratureIdentity,
} from "./research/workflowModel";
import { WorkflowWorkspace } from "./research/WorkflowWorkspace";
import { CompetitiveResearchWorkspace } from "./research/CompetitiveResearchWorkspace";
import {
  DatasetResearchWorkspace,
  shouldDefaultToDatasetWorkspace,
} from "./research/DatasetResearchWorkspace";
import {
  readLastProjectId,
  writeLastProjectId,
} from "./research/researchPreferences";
import { useResearchMemoryWorkspace } from "./research/ResearchMemoryPanel";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

type PublicDiscoveryProvider = "crossref" | "openalex" | "crossref-openalex";

const PUBLIC_DISCOVERY_CANDIDATE_LIMIT = 5;
const PUBLIC_DISCOVERY_QUERY_MAX_CHARS = 500;
const AUTO_PROJECT_TITLE_MAX_CHARS = 80;

export function projectTitleFromQuestion(question: string): string {
  const normalized = question.replace(/\s+/g, " ").trim();
  if (normalized.length <= AUTO_PROJECT_TITLE_MAX_CHARS) return normalized;
  return `${normalized.slice(0, AUTO_PROJECT_TITLE_MAX_CHARS - 1).trimEnd()}…`;
}

function discoveryCreateInput(
  question: string,
  provider: PublicDiscoveryProvider,
): CreateDiscoveryRunInput {
  const queryBase = {
    id: "query-primary",
    query: question,
    yearFrom: null,
    yearTo: null,
    sort: "relevance" as const,
    maxResultsPerProvider: PUBLIC_DISCOVERY_CANDIDATE_LIMIT,
  };
  const query: CreateDiscoveryQueryInput =
    provider === "crossref-openalex"
      ? { ...queryBase, providers: ["crossref", "openalex"] }
      : provider === "openalex"
        ? { ...queryBase, providers: ["openalex"] }
        : { ...queryBase, providers: ["crossref"] };
  const operationCount = query.providers.length;
  return {
    goal: question,
    discoverySpec: {
      schemaVersion: "1",
      question,
      queries: [query],
      stopPolicy: {
        minUniqueCandidates: PUBLIC_DISCOVERY_CANDIDATE_LIMIT,
        maxAttempts: operationCount,
        maxConsecutiveNoNovelty: operationCount,
      },
      downloadOpenAccessPdfs: false,
      maxPdfDownloads: 0,
    },
  };
}

function createRequestKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

interface PendingCandidatePdfAttachment {
  projectId: string;
  workflowId: string;
  candidateId: string;
  candidateSha256: string;
  occurrenceInvocationId: string;
}

function paperDiscoveryIdentity(snapshot: ResearchWorkflowSnapshot | null): {
  key: string;
  workflowId: string;
  projectId: string;
  discoverySpecId: string;
  discoverySpecRevision: number;
  discoverySpecSha256: string;
  polling: boolean;
} | null {
  const spec = snapshot?.plan?.spec;
  if (!snapshot || !spec || spec.planType !== "paper-discovery") return null;
  const { workflow } = snapshot;
  return {
    key: [
      workflow.projectId,
      workflow.id,
      spec.discoverySpecId,
      spec.discoverySpecRevision,
      spec.discoverySpecSha256,
    ].join(":"),
    workflowId: workflow.id,
    projectId: workflow.projectId,
    discoverySpecId: spec.discoverySpecId,
    discoverySpecRevision: spec.discoverySpecRevision,
    discoverySpecSha256: spec.discoverySpecSha256,
    polling:
      workflow.status === "waiting-plan-approval" || workflow.status === "running",
  };
}

function evidenceCoverageIdentity(snapshot: ResearchWorkflowSnapshot | null): {
  key: string;
  workflowId: string;
  projectId: string;
  planId: string;
  planVersion: number;
  planSha256: string;
} | null {
  const plan = snapshot?.plan;
  if (
    !snapshot || !plan || plan.status !== "approved"
    || snapshot.workflow.workflowType !== "literature-synthesis"
    || plan.spec.planType === "paper-discovery"
  ) return null;
  return {
    key: [snapshot.workflow.projectId, snapshot.workflow.id, plan.id, plan.version, plan.planSha256].join(":"),
    workflowId: snapshot.workflow.id,
    projectId: snapshot.workflow.projectId,
    planId: plan.id,
    planVersion: plan.version,
    planSha256: plan.planSha256,
  };
}

/**
 * Evidence-first literature workspace backed by the local science-core API.
 * This page owns presentation state only; projects, sources, claims, and
 * evidence remain canonical in science-core.
 */
export function ResearchPage() {
  const { t } = useTranslation("pages");
  const navigate = useNavigate();
  const [health, setHealth] = useState<ScienceCoreHealth | null>(null);
  const [runtimeStatus, setRuntimeStatus] =
    useState<ScienceCoreRuntimeStatus | null>(null);
  const [projects, setProjects] = useState<ResearchProject[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [showArchivedProjects, setShowArchivedProjects] = useState(false);
  const [sources, setSources] = useState<ResearchSource[]>([]);
  const [candidateTriageDecisions, setCandidateTriageDecisions] = useState<
    CandidateTriageDecision[]
  >([]);
  const [candidateTriageLoading, setCandidateTriageLoading] = useState(false);
  const [candidateTriageError, setCandidateTriageError] = useState<string | null>(null);
  const [candidateTriageMutationPending, setCandidateTriageMutationPending] =
    useState(false);
  const [screeningDecisions, setScreeningDecisions] = useState<ScreeningDecision[]>([]);
  const [screeningDecisionsLoading, setScreeningDecisionsLoading] = useState(false);
  const [screeningDecisionsError, setScreeningDecisionsError] = useState<string | null>(null);
  const [screeningMutationPending, setScreeningMutationPending] = useState(false);
  const [evidenceDirections, setEvidenceDirections] = useState<EvidenceDirectionJudgment[]>([]);
  const [evidenceDirectionsLoading, setEvidenceDirectionsLoading] = useState(false);
  const [evidenceDirectionsError, setEvidenceDirectionsError] = useState<string | null>(null);
  const [evidenceDirectionMutationPending, setEvidenceDirectionMutationPending] = useState(false);
  const [extractionMatrix, setExtractionMatrix] = useState<ExtractionMatrix>({ columns: [], cells: [] });
  const [extractionLoading, setExtractionLoading] = useState(false);
  const [extractionError, setExtractionError] = useState<string | null>(null);
  const [discoverySnapshot, setDiscoverySnapshot] = useState<WorkflowDiscoverySnapshot | null>(null);
  const [discoveryLoading, setDiscoveryLoading] = useState(false);
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);
  const [discoveryCreating, setDiscoveryCreating] = useState(false);
  const [discoveryCreateError, setDiscoveryCreateError] = useState<string | null>(null);
  const [evidenceCoverage, setEvidenceCoverage] = useState<WorkflowEvidenceCoverage | null>(null);
  const [evidenceCoverageLoading, setEvidenceCoverageLoading] = useState(false);
  const [evidenceCoverageError, setEvidenceCoverageError] = useState<string | null>(null);
  const [evidenceCoverageRetry, setEvidenceCoverageRetry] = useState(0);
  const [answer, setAnswer] = useState<ResearchAnswer | null>(null);
  const [citedBriefResult, setCitedBriefResult] = useState<ResearchWorkflowResult | null>(null);
  const [pdfSelection, setPdfSelection] = useState<ResearchPdfSelection | null>(null);
  const [readerSourceRequest, setReaderSourceRequest] = useState<{
    projectId: string;
    sourceId: string;
    requestId: string;
  } | null>(null);
  const [reportOpenRequest, setReportOpenRequest] = useState<{
    projectId: string;
    workflowId: string;
    requestId: string;
  } | null>(null);
  const [inspectorTab, setInspectorTab] = useState<ResearchInspectorTab>("evidence");
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [compactInspector, setCompactInspector] = useState(() =>
    window.matchMedia("(max-width: 1440px)").matches,
  );
  const [question, setQuestion] = useState("");
  const [remoteDataApproved, setRemoteDataApproved] = useState(false);
  const [projectTitle, setProjectTitle] = useState("");
  const [booting, setBooting] = useState(true);
  const [projectListResolved, setProjectListResolved] = useState(false);
  const [createProjectError, setCreateProjectError] = useState<string | null>(null);
  const [loadingSources, setLoadingSources] = useState(false);
  const [creatingProject, setCreatingProject] = useState(false);
  const [projectMutationPending, setProjectMutationPending] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importingDataset, setImportingDataset] = useState(false);
  const [asking, setAsking] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);
  const [presentationMode, setPresentationMode] = useState<"competitive" | "dataset" | "workflow">(
    "competitive",
  );
  const [sourceRefresh, setSourceRefresh] = useState(0);
  const [discoveryRefresh, setDiscoveryRefresh] = useState(0);
  const [cslJsonImporting, setCslJsonImporting] = useState(false);
  const sourcesProjectIdRef = useRef<string | null>(null);
  const candidateTriageProjectIdRef = useRef<string | null>(projectId);
  const candidateTriageLoadGenerationRef = useRef(0);
  const candidateTriageMutationControllersRef = useRef(
    new Set<AbortController>(),
  );
  const screeningProjectIdRef = useRef<string | null>(projectId);
  const screeningLoadGenerationRef = useRef(0);
  const screeningMutationControllersRef = useRef(new Set<AbortController>());
  const evidenceDirectionIdentityRef = useRef<string | null>(null);
  const evidenceDirectionLoadGenerationRef = useRef(0);
  const evidenceDirectionMutationControllersRef = useRef(new Set<AbortController>());
  const researchProjectGenerationRef = useRef(0);
  const researchProjectIdRef = useRef<string | null>(projectId);
  const createProjectControllerRef = useRef<AbortController | null>(null);
  const createProjectEpochRef = useRef(0);
  const mountedRef = useRef(true);
  const extractionProjectIdRef = useRef<string | null>(projectId);
  const extractionLoadGenerationRef = useRef(0);
  const extractionMutationControllersRef = useRef(new Set<AbortController>());
  const discoveryLoadGenerationRef = useRef(0);
  const discoveryIdentityKeyRef = useRef<string | null>(null);
  const discoveryCreateControllerRef = useRef<AbortController | null>(null);
  const discoveryCreateInFlightRef = useRef(false);
  const discoveryCreateIntentRef = useRef<{
    projectId: string;
    question: string;
    provider: PublicDiscoveryProvider;
    idempotencyKey: string;
  } | null>(null);
  const pendingFirstDiscoveryRef = useRef<{
    projectId: string;
    question: string;
    provider: PublicDiscoveryProvider;
  } | null>(null);
  const evidenceCoverageGenerationRef = useRef(0);
  const evidenceCoverageIdentityKeyRef = useRef<string | null>(null);
  const projectMutationGenerationRef = useRef(0);
  const inspectorToggleRef = useRef<HTMLButtonElement>(null);
  const pdfInputRef = useRef<HTMLInputElement>(null);
  const candidatePdfInputRef = useRef<HTMLInputElement>(null);
  const cslJsonInputRef = useRef<HTMLInputElement>(null);
  const datasetInputRef = useRef<HTMLInputElement>(null);
  const workspaceLoadGenerationRef = useRef(0);
  const workflow = useResearchWorkflow(projectId);
  const memoryWorkflowId =
    workflow.snapshot?.workflow.projectId === projectId
      ? workflow.snapshot.workflow.id
      : null;
  const memoryController = useResearchMemoryWorkspace(
    projectId,
    memoryWorkflowId,
  );
  screeningProjectIdRef.current = projectId;
  candidateTriageProjectIdRef.current = projectId;
  researchProjectIdRef.current = projectId;
  extractionProjectIdRef.current = projectId;

  useEffect(() => {
    setCslJsonImporting(false);
  }, [projectId]);

  const closeInspector = useCallback(() => {
    setInspectorOpen(false);
    queueMicrotask(() => inspectorToggleRef.current?.focus());
  }, []);

  const loadWorkspace = useCallback(async (retryRuntime = false) => {
    const generation = ++workspaceLoadGenerationRef.current;
    const isCurrent = () => workspaceLoadGenerationRef.current === generation;
    setBooting(true);
    setProjectListResolved(false);
    setPageError(null);
    if (scienceCoreConfigurationError) {
      if (!isCurrent()) return;
      setHealth(null);
      setRuntimeStatus(null);
      setProjects([]);
      setProjectId(null);
      setSources([]);
      setPageError(scienceCoreConfigurationError);
      setBooting(false);
      return;
    }
    try {
      let observedRuntimeStatus = await getScienceCoreRuntimeStatus();
      if (isCurrent()) setRuntimeStatus(observedRuntimeStatus);
      if (retryRuntime) {
        if (
          observedRuntimeStatus &&
          ["failed", "stopped", "unavailable"].includes(observedRuntimeStatus.state)
        ) {
          observedRuntimeStatus = await retryScienceCoreRuntime();
          if (isCurrent()) setRuntimeStatus(observedRuntimeStatus);
        }
      }
      const [nextHealth, nextProjects, readyRuntimeStatus] = await Promise.all([
        scienceCore.health(),
        scienceCore.listProjects({ includeArchived: showArchivedProjects }),
        getScienceCoreRuntimeStatus(),
      ]);
      if (!isCurrent()) return;
      setHealth(nextHealth);
      setRuntimeStatus(readyRuntimeStatus ?? observedRuntimeStatus);
      setProjects(nextProjects);
      setProjectListResolved(true);
      setSourceRefresh((version) => version + 1);
      setProjectId((current) => {
        const preferredId = current ?? readLastProjectId();
        const preferredActive = nextProjects.find(
          (project) => project.id === preferredId && !project.archivedAt,
        );
        return preferredActive?.id
          ?? nextProjects.find((project) => !project.archivedAt)?.id
          ?? null;
      });
    } catch (error) {
      if (!isCurrent()) return;
      setHealth(null);
      const failedRuntimeStatus = await getScienceCoreRuntimeStatus().catch(() => null);
      if (!isCurrent()) return;
      setRuntimeStatus(failedRuntimeStatus);
      setPageError(message(error));
    } finally {
      if (isCurrent()) setBooting(false);
    }
  }, [showArchivedProjects]);

  useEffect(() => {
    void loadWorkspace();
  }, [loadWorkspace]);

  useEffect(() => {
    if (!projectListResolved) return;
    const selected = projects.find((project) => project.id === projectId);
    writeLastProjectId(selected?.archivedAt ? null : projectId);
  }, [projectId, projectListResolved, projects]);

  useEffect(() => {
    const compact = window.matchMedia("(max-width: 1440px)");
    const handleChange = (event: MediaQueryListEvent) => {
      setCompactInspector(event.matches);
      if (event.matches) setInspectorOpen(false);
    };
    compact.addEventListener("change", handleChange);
    return () => compact.removeEventListener("change", handleChange);
  }, []);

  useEffect(() => {
    if (!inspectorOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeInspector();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [closeInspector, inspectorOpen]);

  useEffect(() => {
    let cancelled = false;
    setAnswer(null);
    setCitedBriefResult(null);
    setPdfSelection(null);
    setReaderSourceRequest(null);
    const candidateInput = candidatePdfInputRef.current;
    if (candidateInput) {
      delete candidateInput.dataset.projectId;
      delete candidateInput.dataset.workflowId;
      delete candidateInput.dataset.candidateId;
      delete candidateInput.dataset.candidateSha256;
      delete candidateInput.dataset.occurrenceInvocationId;
    }
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
  const activeDiscoveryWorkflow = useMemo(
    () => {
      const statusPriority: Partial<Record<ResearchWorkflowSnapshot["workflow"]["status"], number>> = {
        "waiting-plan-approval": 0,
        running: 0,
        blocked: 1,
        failed: 1,
        completed: 2,
      };
      const selectedDiscoveryWorkflow = (
        workflow.snapshot?.workflow.projectId === projectId
        && workflow.snapshot.plan?.spec.planType === "paper-discovery"
        && statusPriority[workflow.snapshot.workflow.status] !== undefined
      )
        ? workflow.snapshot
        : null;
      if (selectedDiscoveryWorkflow) return selectedDiscoveryWorkflow;

      return workflow.workflows
        .filter((candidate) =>
          candidate.workflow.projectId === projectId
          && candidate.plan?.spec.planType === "paper-discovery"
          && statusPriority[candidate.workflow.status] !== undefined)
        .slice()
        .sort((left, right) => {
          const priorityDifference =
            (statusPriority[left.workflow.status] ?? 3)
            - (statusPriority[right.workflow.status] ?? 3);
          if (priorityDifference !== 0) return priorityDifference;
          return Date.parse(right.workflow.updatedAt) - Date.parse(left.workflow.updatedAt);
        })[0] ?? null;
    },
    [projectId, workflow.snapshot, workflow.workflows],
  );
  const discoveryIdentity = useMemo(
    () => paperDiscoveryIdentity(activeDiscoveryWorkflow),
    [activeDiscoveryWorkflow],
  );
  discoveryIdentityKeyRef.current = discoveryIdentity?.key ?? null;
  const activeEvidenceCoverageWorkflow = useMemo(
    () => (
      workflow.snapshot?.workflow.projectId === projectId
      && evidenceCoverageIdentity(workflow.snapshot) !== null
        ? workflow.snapshot
        : null
    ),
    [projectId, workflow.snapshot],
  );
  const coverageIdentity = useMemo(
    () => evidenceCoverageIdentity(activeEvidenceCoverageWorkflow),
    [activeEvidenceCoverageWorkflow],
  );
  evidenceCoverageIdentityKeyRef.current = coverageIdentity?.key ?? null;
  const evidenceDirectionIdentity = useMemo(() => {
    const snapshot = workflow.snapshot;
    if (
      !projectId
      || snapshot?.workflow.projectId !== projectId
      || snapshot.workflow.workflowType !== "literature-synthesis"
      || snapshot.workflow.status !== "completed"
      || !snapshot.result?.answerId
    ) return null;
    return {
      projectId,
      answerId: snapshot.result.answerId,
      key: `${projectId}:${snapshot.result.answerId}`,
    };
  }, [projectId, workflow.snapshot]);
  evidenceDirectionIdentityRef.current = evidenceDirectionIdentity?.key ?? null;
  const datasetWorkspaceDefault = shouldDefaultToDatasetWorkspace(
    sources,
    workflow.snapshot?.workflow ?? null,
  );
  useEffect(() => {
    const input = candidatePdfInputRef.current;
    if (!input) return;
    const clearCandidateAttachment = () => {
      delete input.dataset.projectId;
      delete input.dataset.workflowId;
      delete input.dataset.candidateId;
      delete input.dataset.candidateSha256;
      delete input.dataset.occurrenceInvocationId;
      input.value = "";
    };
    input.addEventListener("cancel", clearCandidateAttachment);
    return () => input.removeEventListener("cancel", clearCandidateAttachment);
  }, [booting, datasetWorkspaceDefault, presentationMode]);
  const readySources = paperSources.filter(
    (source) => source.ingestionStatus === "ready",
  );

  useEffect(() => {
    const requestProjectId = projectId;
    const generation = ++screeningLoadGenerationRef.current;
    const controller = new AbortController();
    const isCurrent = () =>
      screeningProjectIdRef.current === requestProjectId &&
      screeningLoadGenerationRef.current === generation &&
      !controller.signal.aborted;
    setScreeningDecisions([]);
    setScreeningDecisionsError(null);
    if (!requestProjectId || paperSources.length === 0) {
      setScreeningDecisionsLoading(false);
      return;
    }
    setScreeningDecisionsLoading(true);
    void scienceCore
      .listScreeningDecisions(requestProjectId, { signal: controller.signal })
      .then((decisions) => {
        if (isCurrent()) setScreeningDecisions(decisions);
      })
      .catch((error) => {
        if (isCurrent()) setScreeningDecisionsError(message(error));
      })
      .finally(() => {
        if (isCurrent()) setScreeningDecisionsLoading(false);
      });
    return () => {
      controller.abort();
    };
  }, [projectId, paperSources.length, sourceRefresh]);

  useEffect(() => {
    const identity = discoveryIdentity;
    const generation = ++discoveryLoadGenerationRef.current;
    const controller = new AbortController();
    let pollTimer: number | undefined;
    let firstLoad = true;
    const isCurrent = (snapshot?: WorkflowDiscoverySnapshot) =>
      !controller.signal.aborted
      && discoveryLoadGenerationRef.current === generation
      && discoveryIdentityKeyRef.current === identity?.key
      && (snapshot == null || (
        snapshot.workflowId === identity?.workflowId
        && snapshot.projectId === identity.projectId
        && snapshot.discoverySpecId === identity.discoverySpecId
        && snapshot.discoverySpecRevision === identity.discoverySpecRevision
        && snapshot.discoverySpecSha256 === identity.discoverySpecSha256
      ));

    setDiscoverySnapshot(null);
    setDiscoveryError(null);
    if (!identity || typeof scienceCore.getWorkflowDiscovery !== "function") {
      setDiscoveryLoading(false);
      return () => controller.abort();
    }

    const load = async () => {
      if (firstLoad) setDiscoveryLoading(true);
      try {
        const snapshot = await scienceCore.getWorkflowDiscovery(identity.workflowId, {
          offset: 0,
          limit: 50,
          signal: controller.signal,
        });
        if (isCurrent(snapshot)) {
          setDiscoverySnapshot(snapshot);
          setDiscoveryError(null);
        }
      } catch (error) {
        if (isCurrent()) setDiscoveryError(message(error));
      } finally {
        if (isCurrent()) {
          if (firstLoad) setDiscoveryLoading(false);
          firstLoad = false;
          if (identity.polling) {
            pollTimer = window.setTimeout(() => void load(), 5_000);
          }
        }
      }
    };
    void load();
    return () => {
      controller.abort();
      if (pollTimer !== undefined) window.clearTimeout(pollTimer);
    };
  }, [discoveryIdentity, discoveryRefresh]);

  useEffect(() => {
    const requestProjectId = projectId;
    const generation = ++candidateTriageLoadGenerationRef.current;
    const controller = new AbortController();
    const isCurrent = () =>
      candidateTriageProjectIdRef.current === requestProjectId
      && candidateTriageLoadGenerationRef.current === generation
      && !controller.signal.aborted;
    setCandidateTriageDecisions([]);
    setCandidateTriageError(null);
    if (!requestProjectId || !discoveryIdentity) {
      setCandidateTriageLoading(false);
      return () => controller.abort();
    }
    setCandidateTriageLoading(true);
    void scienceCore
      .listCandidateTriageDecisions(requestProjectId, {
        signal: controller.signal,
      })
      .then((decisions) => {
        if (isCurrent()) setCandidateTriageDecisions(decisions);
      })
      .catch((error) => {
        if (isCurrent()) setCandidateTriageError(message(error));
      })
      .finally(() => {
        if (isCurrent()) setCandidateTriageLoading(false);
      });
    return () => controller.abort();
  }, [discoveryIdentity, projectId]);

  useEffect(() => {
    const identity = coverageIdentity;
    const generation = ++evidenceCoverageGenerationRef.current;
    const controller = new AbortController();
    const isCurrent = (snapshot?: WorkflowEvidenceCoverage) =>
      !controller.signal.aborted
      && evidenceCoverageGenerationRef.current === generation
      && evidenceCoverageIdentityKeyRef.current === identity?.key
      && (snapshot == null || (
        snapshot.workflowId === identity?.workflowId
        && snapshot.projectId === identity.projectId
        && snapshot.planId === identity.planId
        && snapshot.planVersion === identity.planVersion
        && snapshot.planSha256 === identity.planSha256
      ));
    setEvidenceCoverage(null);
    setEvidenceCoverageError(null);
    if (!identity || typeof scienceCore.getWorkflowEvidenceCoverage !== "function") {
      setEvidenceCoverageLoading(false);
      return () => controller.abort();
    }
    setEvidenceCoverageLoading(true);
    void scienceCore.getWorkflowEvidenceCoverage(identity.workflowId, { signal: controller.signal })
      .then((snapshot) => { if (isCurrent(snapshot)) setEvidenceCoverage(snapshot); })
      .catch((error) => { if (isCurrent()) setEvidenceCoverageError(message(error)); })
      .finally(() => { if (isCurrent()) setEvidenceCoverageLoading(false); });
    return () => controller.abort();
  }, [coverageIdentity, evidenceCoverageRetry, extractionMatrix, sourceRefresh]);

  useEffect(() => {
    const identity = evidenceDirectionIdentity;
    const generation = ++evidenceDirectionLoadGenerationRef.current;
    const controller = new AbortController();
    const isCurrent = () =>
      !controller.signal.aborted
      && evidenceDirectionLoadGenerationRef.current === generation
      && evidenceDirectionIdentityRef.current === identity?.key;
    setEvidenceDirections([]);
    setEvidenceDirectionsError(null);
    if (!identity || typeof scienceCore.listEvidenceDirectionJudgments !== "function") {
      setEvidenceDirectionsLoading(false);
      return () => controller.abort();
    }
    setEvidenceDirectionsLoading(true);
    void scienceCore
      .listEvidenceDirectionJudgments(identity.projectId, identity.answerId, {
        signal: controller.signal,
      })
      .then((judgments) => {
        if (isCurrent()) setEvidenceDirections(judgments);
      })
      .catch((error) => {
        if (isCurrent()) setEvidenceDirectionsError(message(error));
      })
      .finally(() => {
        if (isCurrent()) setEvidenceDirectionsLoading(false);
      });
    return () => controller.abort();
  }, [evidenceDirectionIdentity]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      createProjectEpochRef.current += 1;
      createProjectControllerRef.current?.abort();
      createProjectControllerRef.current = null;
    };
  }, []);

  useEffect(() => {
    researchProjectGenerationRef.current += 1;
    discoveryCreateControllerRef.current?.abort();
    discoveryCreateControllerRef.current = null;
    discoveryCreateInFlightRef.current = false;
    discoveryCreateIntentRef.current = null;
    setDiscoveryCreating(false);
    setDiscoveryCreateError(null);
    setCreatingProject(false);
    setCreateProjectError(null);
    return () => discoveryCreateControllerRef.current?.abort();
  }, [projectId]);

  useEffect(() => {
    for (const controller of candidateTriageMutationControllersRef.current) {
      controller.abort();
    }
    candidateTriageMutationControllersRef.current.clear();
    setCandidateTriageMutationPending(false);
  }, [projectId]);

  useEffect(() => {
    for (const controller of screeningMutationControllersRef.current) controller.abort();
    screeningMutationControllersRef.current.clear();
    setScreeningMutationPending(false);
  }, [projectId]);

  useEffect(() => {
    for (const controller of evidenceDirectionMutationControllersRef.current) controller.abort();
    evidenceDirectionMutationControllersRef.current.clear();
    setEvidenceDirectionMutationPending(false);
  }, [evidenceDirectionIdentity?.key]);

  useEffect(() => {
    const requestProjectId = projectId;
    const generation = ++extractionLoadGenerationRef.current;
    const controller = new AbortController();
    const isCurrent = () => extractionProjectIdRef.current === requestProjectId
      && extractionLoadGenerationRef.current === generation && !controller.signal.aborted;
    setExtractionMatrix({ columns: [], cells: [] });
    setExtractionError(null);
    if (!requestProjectId || paperSources.length === 0) {
      setExtractionLoading(false);
      return;
    }
    // Test doubles from the frozen surface may not yet expose this new typed method.
    // The production client always does; skip rather than treating a missing mock as a service failure.
    if (typeof scienceCore.getExtractionMatrix !== "function") {
      setExtractionLoading(false);
      return;
    }
    setExtractionLoading(true);
    void scienceCore.getExtractionMatrix(requestProjectId, { signal: controller.signal })
      .then((matrix) => { if (isCurrent()) setExtractionMatrix(matrix); })
      .catch((error) => { if (isCurrent()) setExtractionError(message(error)); })
      .finally(() => { if (isCurrent()) setExtractionLoading(false); });
    return () => controller.abort();
  }, [projectId, paperSources.length, sourceRefresh]);

  useEffect(() => {
    for (const controller of extractionMutationControllersRef.current) controller.abort();
    extractionMutationControllersRef.current.clear();
  }, [projectId]);

  const refreshExtractionMatrix = useCallback(async (requestProjectId: string, signal: AbortSignal) => {
    const matrix = await scienceCore.getExtractionMatrix(requestProjectId, { signal });
    if (extractionProjectIdRef.current === requestProjectId && !signal.aborted) setExtractionMatrix(matrix);
    return matrix;
  }, []);

  const createExtractionColumn = useCallback(async (input: CreateExtractionColumnInput) => {
    const requestProjectId = projectId;
    if (!requestProjectId) throw new Error("A project is required for extraction");
    const controller = new AbortController();
    extractionMutationControllersRef.current.add(controller);
    const isCurrent = () => extractionProjectIdRef.current === requestProjectId && !controller.signal.aborted;
    if (isCurrent()) setExtractionError(null);
    try {
      const column = await scienceCore.createExtractionColumn(requestProjectId, input, { signal: controller.signal });
      if (isCurrent()) setExtractionMatrix((current) => ({ ...current, columns: [...current.columns, column] }));
      return column;
    } catch (error) {
      if (isCurrent()) {
        try { await refreshExtractionMatrix(requestProjectId, controller.signal); } catch { /* retain mutation error */ }
        if (isCurrent()) setExtractionError(message(error));
      }
      throw error;
    } finally { extractionMutationControllersRef.current.delete(controller); }
  }, [projectId, refreshExtractionMatrix]);

  const upsertExtractionCell = useCallback(async (
    sourceId: string, columnId: string, input: UpsertExtractionCellInput,
  ) => {
    const requestProjectId = projectId;
    if (!requestProjectId) throw new Error("A project is required for extraction");
    const controller = new AbortController();
    extractionMutationControllersRef.current.add(controller);
    const isCurrent = () => extractionProjectIdRef.current === requestProjectId && !controller.signal.aborted;
    if (isCurrent()) setExtractionError(null);
    try {
      const cell = await scienceCore.upsertExtractionCell(requestProjectId, sourceId, columnId, input, { signal: controller.signal });
      if (isCurrent()) setExtractionMatrix((current) => ({ ...current, cells: [cell, ...current.cells.filter((item) => item.sourceId !== sourceId || item.columnId !== columnId)] }));
      return cell;
    } catch (error) {
      if (isCurrent()) {
        try { await refreshExtractionMatrix(requestProjectId, controller.signal); } catch { /* retain mutation error */ }
        if (isCurrent()) setExtractionError(message(error));
      }
      throw error;
    } finally { extractionMutationControllersRef.current.delete(controller); }
  }, [projectId, refreshExtractionMatrix]);

  const createExactEvidenceSpan = useCallback(async (
    sourceId: string,
    input: CreateExactEvidenceSpanInput,
  ) => {
    const requestProjectId = projectId;
    if (!requestProjectId) throw new Error("A project is required for evidence capture");
    const controller = new AbortController();
    extractionMutationControllersRef.current.add(controller);
    const isCurrent = () =>
      extractionProjectIdRef.current === requestProjectId && !controller.signal.aborted;
    if (isCurrent()) setExtractionError(null);
    try {
      return await scienceCore.createExactEvidenceSpan(
        requestProjectId,
        sourceId,
        input,
        { signal: controller.signal, idempotencyKey: createRequestKey() },
      );
    } catch (error) {
      if (isCurrent()) setExtractionError(message(error));
      throw error;
    } finally {
      extractionMutationControllersRef.current.delete(controller);
    }
  }, [projectId]);

  const createConfirmedExtractionCitedBrief = useCallback(async () => {
    const requestProjectId = projectId;
    if (!requestProjectId) throw new Error("A project is required for a cited brief");
    const controller = new AbortController();
    extractionMutationControllersRef.current.add(controller);
    const isCurrent = () =>
      extractionProjectIdRef.current === requestProjectId && !controller.signal.aborted;
    if (isCurrent()) setExtractionError(null);
    try {
      const result = await scienceCore.createConfirmedExtractionCitedBrief(
        requestProjectId,
        { signal: controller.signal, idempotencyKey: createRequestKey() },
      );
      if (isCurrent()) setCitedBriefResult(result);
      return result;
    } catch (error) {
      if (isCurrent()) setExtractionError(message(error));
      throw error;
    } finally {
      extractionMutationControllersRef.current.delete(controller);
    }
  }, [projectId]);

  const deleteExtractionCell = useCallback(async (sourceId: string, columnId: string, expectedVersion: number) => {
    const requestProjectId = projectId;
    if (!requestProjectId) throw new Error("A project is required for extraction");
    const controller = new AbortController();
    extractionMutationControllersRef.current.add(controller);
    const isCurrent = () => extractionProjectIdRef.current === requestProjectId && !controller.signal.aborted;
    if (isCurrent()) setExtractionError(null);
    try {
      await scienceCore.deleteExtractionCell(requestProjectId, sourceId, columnId, expectedVersion, { signal: controller.signal });
      if (isCurrent()) setExtractionMatrix((current) => ({ ...current, cells: current.cells.filter((cell) => cell.sourceId !== sourceId || cell.columnId !== columnId) }));
    } catch (error) {
      if (isCurrent()) {
        try { await refreshExtractionMatrix(requestProjectId, controller.signal); } catch { /* retain mutation error */ }
        if (isCurrent()) setExtractionError(message(error));
      }
      throw error;
    } finally { extractionMutationControllersRef.current.delete(controller); }
  }, [projectId, refreshExtractionMatrix]);

  const upsertCandidateTriageDecision = useCallback(
    async (
      candidateId: string,
      input: UpsertCandidateTriageDecisionInput,
    ) => {
      const requestProjectId = projectId;
      if (!requestProjectId) {
        throw new Error("A project is required for candidate triage");
      }
      const controller = new AbortController();
      candidateTriageMutationControllersRef.current.add(controller);
      setCandidateTriageMutationPending(true);
      const isCurrent = () =>
        candidateTriageProjectIdRef.current === requestProjectId
        && !controller.signal.aborted;
      if (isCurrent()) setCandidateTriageError(null);
      try {
        const saved = await scienceCore.upsertCandidateTriageDecision(
          requestProjectId,
          candidateId,
          input,
          { signal: controller.signal },
        );
        if (isCurrent()) {
          setCandidateTriageDecisions((current) => [
            saved,
            ...current.filter(
              (decision) => decision.candidateId !== candidateId,
            ),
          ]);
        }
        return saved;
      } catch (error) {
        if (isCurrent()) {
          try {
            const canonical = await scienceCore.listCandidateTriageDecisions(
              requestProjectId,
              { signal: controller.signal },
            );
            if (isCurrent()) setCandidateTriageDecisions(canonical);
          } catch {
            // Preserve the mutation error while keeping the prior local snapshot.
          }
          setCandidateTriageError(message(error));
        }
        throw error;
      } finally {
        candidateTriageMutationControllersRef.current.delete(controller);
        if (candidateTriageProjectIdRef.current === requestProjectId) {
          setCandidateTriageMutationPending(
            candidateTriageMutationControllersRef.current.size > 0,
          );
        }
      }
    },
    [projectId],
  );

  const upsertScreeningDecision = useCallback(
    async (sourceId: string, input: UpsertScreeningDecisionInput) => {
      const requestProjectId = projectId;
      if (!requestProjectId) throw new Error("A project is required for screening");
      const controller = new AbortController();
      screeningMutationControllersRef.current.add(controller);
      setScreeningMutationPending(true);
      const isCurrent = () =>
        screeningProjectIdRef.current === requestProjectId && !controller.signal.aborted;
      if (isCurrent()) setScreeningDecisionsError(null);
      try {
        const saved = await scienceCore.upsertScreeningDecision(
          requestProjectId,
          sourceId,
          input,
          { signal: controller.signal },
        );
        if (isCurrent()) {
          setScreeningDecisions((current) => [
            saved,
            ...current.filter((decision) => decision.sourceId !== sourceId),
          ]);
        }
        return saved;
      } catch (error) {
        if (isCurrent()) {
          try {
            const canonical = await scienceCore.listScreeningDecisions(
              requestProjectId,
              { signal: controller.signal },
            );
            if (isCurrent()) setScreeningDecisions(canonical);
          } catch {
            // Preserve the mutation error; the visible state remains rolled back locally.
          }
        }
        if (isCurrent()) setScreeningDecisionsError(message(error));
        throw error;
      } finally {
        screeningMutationControllersRef.current.delete(controller);
        if (screeningProjectIdRef.current === requestProjectId) {
          setScreeningMutationPending(
            screeningMutationControllersRef.current.size > 0,
          );
        }
      }
  },
    [projectId],
  );

  const upsertEvidenceDirection = useCallback(
    async (sourceId: string, input: UpsertEvidenceDirectionJudgmentInput) => {
      const identity = evidenceDirectionIdentity;
      if (!identity) throw new Error("A completed literature answer is required");
      const controller = new AbortController();
      evidenceDirectionMutationControllersRef.current.add(controller);
      setEvidenceDirectionMutationPending(true);
      const isCurrent = () =>
        evidenceDirectionIdentityRef.current === identity.key && !controller.signal.aborted;
      if (isCurrent()) setEvidenceDirectionsError(null);
      try {
        const saved = await scienceCore.upsertEvidenceDirectionJudgment(
          identity.projectId,
          identity.answerId,
          sourceId,
          input,
          { signal: controller.signal },
        );
        if (isCurrent()) {
          setEvidenceDirections((current) => [
            saved,
            ...current.filter((judgment) => judgment.sourceId !== sourceId),
          ]);
        }
        return saved;
      } catch (error) {
        if (isCurrent()) {
          try {
            const canonical = await scienceCore.listEvidenceDirectionJudgments(
              identity.projectId,
              identity.answerId,
              { signal: controller.signal },
            );
            if (isCurrent()) setEvidenceDirections(canonical);
          } catch {
            // Preserve the mutation error; the canonical reload remains available on retry.
          }
          if (isCurrent()) setEvidenceDirectionsError(message(error));
        }
        throw error;
      } finally {
        evidenceDirectionMutationControllersRef.current.delete(controller);
        if (evidenceDirectionIdentityRef.current === identity.key) {
          setEvidenceDirectionMutationPending(
            evidenceDirectionMutationControllersRef.current.size > 0,
          );
        }
      }
    },
    [evidenceDirectionIdentity],
  );

  const healthStatusReason = health?.database === "error"
    ? t("research.healthReason.databaseError", {
        defaultValue: "Science core database is unavailable.",
      })
    : health?.runtime === "unavailable"
      ? t("research.healthReason.runtimeUnavailable", {
          defaultValue:
            "Restricted analysis runtime is unavailable; local literature storage remains available.",
        })
      : health?.status === "degraded"
        ? t("research.healthReason.degraded", {
            defaultValue:
              "Science core reports degraded health; available local literature actions remain enabled.",
          })
        : null;
  const configurationServiceReason = scienceCoreConfigurationError
    ? t("research.offlineBody", {
        defaultValue:
          "The local research service is not connected. Existing local data was not changed; open Settings to check the service configuration, then retry.",
      })
    : null;
  const runtimeNeedsContainerEngine =
    runtimeStatus?.state === "failed" && !runtimeStatus.dockerReady;
  const runtimeResourcesUnavailable = runtimeStatus?.state === "unavailable";
  const runtimeRecoveryReason = runtimeNeedsContainerEngine
    ? t("research.runtimeDockerBody", {
        defaultValue:
          "Spark's local research engine needs Docker Desktop or OrbStack running. Start one, then retry.",
      })
    : runtimeResourcesUnavailable
      ? t("research.runtimeResourcesBody", {
          defaultValue:
            "This Spark installation is missing its local research runtime. Reinstall the application, then retry.",
        })
      : null;
  const userFacingPageError =
    pageError === scienceCoreConfigurationError
      ? configurationServiceReason
      : runtimeRecoveryReason ?? pageError;
  const blockingServiceReason =
    configurationServiceReason ??
    runtimeRecoveryReason ??
    pageError ??
    (health?.database === "error" ? healthStatusReason : null);
  // Local literature storage/import depends on the authenticated core database,
  // not the restricted analysis runtime. A runtime-only degradation stays
  // visible without disabling actions that the current contract still supports.
  const serviceReady = health?.database === "ok" && blockingServiceReason === null;
  const serviceUnavailableReason = blockingServiceReason ?? healthStatusReason;
  const literatureReady =
    serviceReady &&
    health?.paperQa === "available" &&
    health.modelGateway === "configured" &&
    health.modelDestination != null;
  const remoteDestinationApprovalKey = health?.modelDestination
    ? `${health.modelDestination.endpointIdentity}:${health.modelDestination.model}`
    : null;
  const startLiteratureSynthesis = useCallback(async (goal: string, sourceIds: string[]) => {
    const requestProjectId = projectId;
    const requestGeneration = researchProjectGenerationRef.current;
    const identity = canonicalAutonomousLiteratureIdentity(requestProjectId ?? "", goal, sourceIds);
    if (!identity || !serviceReady || loadingSources || screeningDecisionsLoading || screeningMutationPending || screeningDecisionsError) return;
    const matching = workflow.workflows.find((candidate) =>
      candidate.workflow.status !== "cancelled" &&
      matchesAutonomousLiteratureIdentity(candidate, identity),
    );
    // An existing run is authoritative. Failed/blocked runs are deliberately
    // selected for their existing allowed actions rather than retried here.
    if (matching) {
      workflow.selectWorkflow(matching.workflow.id);
      return;
    }
    workflow.startNew();
    if (researchProjectIdRef.current !== requestProjectId || researchProjectGenerationRef.current !== requestGeneration) return;
    await workflow.create(identity.goal, {
      mode: "autonomous",
      sourceIds: identity.sourceIds,
      remoteDataApproved: false,
    });
  }, [loadingSources, projectId, screeningDecisionsError, screeningDecisionsLoading, screeningMutationPending, serviceReady, workflow]);
  const startDiscovery = useCallback(async (
    rawQuestion: string,
    provider: PublicDiscoveryProvider,
  ) => {
    const requestProjectId = projectId;
    const requestQuestion = rawQuestion.trim();
    const requestGeneration = researchProjectGenerationRef.current;
    if (requestQuestion.length > PUBLIC_DISCOVERY_QUERY_MAX_CHARS) {
      setDiscoveryCreateError(
        t("research.literature.discoveryQueryTooLong", {
          count: requestQuestion.length,
          max: PUBLIC_DISCOVERY_QUERY_MAX_CHARS,
        }),
      );
      return false;
    }
    if (
      !requestProjectId
      || !requestQuestion
      || !serviceReady
      || discoveryCreateInFlightRef.current
    ) {
      return false;
    }
    const previousIntent = discoveryCreateIntentRef.current;
    const intent = (
      previousIntent?.projectId === requestProjectId
      && previousIntent.question === requestQuestion
      && previousIntent.provider === provider
    )
      ? previousIntent
      : {
          projectId: requestProjectId,
          question: requestQuestion,
          provider,
          idempotencyKey: createRequestKey(),
        };
    discoveryCreateIntentRef.current = intent;
    const controller = new AbortController();
    discoveryCreateControllerRef.current = controller;
    discoveryCreateInFlightRef.current = true;
    setDiscoveryCreating(true);
    setDiscoveryCreateError(null);
    const isCurrent = () =>
      !controller.signal.aborted
      && researchProjectIdRef.current === requestProjectId
      && researchProjectGenerationRef.current === requestGeneration;
    try {
      const created = await scienceCore.createDiscoveryRun(
        requestProjectId,
        discoveryCreateInput(requestQuestion, provider),
        { idempotencyKey: intent.idempotencyKey, signal: controller.signal },
      );
      if (!isCurrent()) return false;
      if (
        created.workflow.projectId !== requestProjectId
        || created.workflow.sourceIds.length !== 0
        || created.plan?.spec.planType !== "paper-discovery"
      ) {
        throw new Error("Science core returned a discovery run outside the requested scope");
      }
      discoveryCreateIntentRef.current = null;
      workflow.selectWorkflow(created.workflow.id);
      setPresentationMode(WORKFLOW_PRESENTATION);
      return true;
    } catch (error) {
      if (isCurrent()) setDiscoveryCreateError(message(error));
      return false;
    } finally {
      if (discoveryCreateControllerRef.current === controller) {
        discoveryCreateControllerRef.current = null;
        discoveryCreateInFlightRef.current = false;
        if (isCurrent()) setDiscoveryCreating(false);
      }
    }
  }, [projectId, serviceReady, t, workflow]);
  const workspaceGuidance = pageError
    ? t("research.offlineNext", { defaultValue: "Open Settings to connect the local research service" })
    : !projectId
    ? t("research.next.createProject", { defaultValue: "Next: create a research project" })
    : sources.length === 0
      ? t("research.next.addSources", { defaultValue: "Next: import a paper or dataset" })
      : workflow.snapshot?.workflow.status === "waiting-clarification"
        ? t("research.next.clarify", { defaultValue: "Next: answer the clarification request" })
        : workflow.snapshot?.workflow.status === "waiting-plan-approval"
          ? t("research.next.approvePlan", { defaultValue: "Next: review and approve the plan" })
          : workflow.snapshot?.workflow.status === "running"
            ? t("research.next.running", { defaultValue: "Execution is in progress" })
            : workflow.snapshot?.workflow.status === "reviewing"
              ? t("research.next.reviewing", { defaultValue: "Reviewing evidence and outputs" })
              : workflow.snapshot?.workflow.status === "failed" ||
                  workflow.snapshot?.workflow.status === "blocked"
                ? t("research.next.recover", { defaultValue: "Action required: inspect the failure and recover" })
                : workflow.snapshot?.workflow.status === "completed"
                  ? t("research.next.reviewResult", { defaultValue: "Next: review the conclusion and cited evidence" })
                  : t("research.next.defineQuestion", { defaultValue: "Next: define the research question" });

  useEffect(() => {
    setRemoteDataApproved(false);
  }, [remoteDestinationApprovalKey]);

  const createProjectWithTitle = async (
    candidate: string,
    beforeSelect?: (project: ResearchProject) => void,
  ) => {
    const title = candidate.trim();
    if (!title || !serviceReady) return false;
    const requestProjectId = researchProjectIdRef.current;
    const requestGeneration = researchProjectGenerationRef.current;
    const requestEpoch = createProjectEpochRef.current + 1;
    createProjectEpochRef.current = requestEpoch;
    createProjectControllerRef.current?.abort();
    const controller = new AbortController();
    createProjectControllerRef.current = controller;
    const isCurrent = () =>
      mountedRef.current
      && !controller.signal.aborted
      && createProjectEpochRef.current === requestEpoch
      && createProjectControllerRef.current === controller;
    setCreatingProject(true);
    setCreateProjectError(null);
    try {
      const project = await scienceCore.createProject({ title }, { signal: controller.signal });
      if (
        !isCurrent()
        ||
        researchProjectIdRef.current !== requestProjectId
        || researchProjectGenerationRef.current !== requestGeneration
      ) return false;
      setProjects((current) => [project, ...current.filter((item) => item.id !== project.id)]);
      beforeSelect?.(project);
      setProjectId(project.id);
      setProjectTitle("");
      toast.success(
        t("research.toast.projectCreated", {
          defaultValue: "Research project created.",
        }),
      );
      return true;
    } catch (error) {
      if (
        isCurrent()
        &&
        researchProjectIdRef.current === requestProjectId
        && researchProjectGenerationRef.current === requestGeneration
      ) setCreateProjectError(message(error));
      return false;
    } finally {
      const current = isCurrent();
      if (createProjectControllerRef.current === controller) createProjectControllerRef.current = null;
      if (
        current
        &&
        researchProjectIdRef.current === requestProjectId
        && researchProjectGenerationRef.current === requestGeneration
      ) setCreatingProject(false);
    }
  };

  const startFirstDiscovery = async (
    rawQuestion: string,
    provider: PublicDiscoveryProvider,
  ) => {
    const question = rawQuestion.trim();
    if (
      !question
      || question.length > PUBLIC_DISCOVERY_QUERY_MAX_CHARS
      || !serviceReady
      || creatingProject
    ) return false;
    return createProjectWithTitle(
      projectTitleFromQuestion(question),
      (project) => {
        pendingFirstDiscoveryRef.current = {
          projectId: project.id,
          question,
          provider,
        };
      },
    );
  };

  useEffect(() => {
    const pending = pendingFirstDiscoveryRef.current;
    if (!pending || pending.projectId !== projectId || !serviceReady) return;
    pendingFirstDiscoveryRef.current = null;
    void startDiscovery(pending.question, pending.provider);
  }, [projectId, serviceReady, startDiscovery]);

  const mutateProject = async (
    operation: "rename" | "archive" | "restore",
    title?: string,
  ) => {
    const requestProjectId = projectId;
    const project = projects.find((item) => item.id === requestProjectId);
    if (!requestProjectId || !project || !serviceReady) return;
    const generation = projectMutationGenerationRef.current + 1;
    projectMutationGenerationRef.current = generation;
    setProjectMutationPending(true);
    try {
      const options = {
        idempotencyKey: `project-${operation}:${requestProjectId}:${project.rowVersion}:${createRequestKey()}`,
      };
      const updated =
        operation === "rename"
          ? await scienceCore.renameProject(
              requestProjectId,
              title ?? project.title,
              project.rowVersion,
              options,
            )
          : operation === "archive"
            ? await scienceCore.archiveProject(requestProjectId, project.rowVersion, options)
            : await scienceCore.restoreProject(requestProjectId, project.rowVersion, options);
      if (generation !== projectMutationGenerationRef.current || projectId !== requestProjectId) return;
      if (operation === "archive" && !showArchivedProjects) {
        const nextProjects = await scienceCore.listProjects({ includeArchived: false });
        if (generation !== projectMutationGenerationRef.current || projectId !== requestProjectId) return;
        setProjects(nextProjects);
        setProjectId((current) => current === requestProjectId ? nextProjects[0]?.id ?? null : current);
      } else {
        setProjects((current) => current.map((item) => item.id === updated.id ? updated : item));
      }
      toast.success(
        operation === "rename"
          ? t("research.toast.projectRenamed", { defaultValue: "Project renamed." })
          : operation === "archive"
            ? t("research.toast.projectArchived", { defaultValue: "Project archived." })
            : t("research.toast.projectRestored", { defaultValue: "Project restored." }),
      );
    } catch (error) {
      if (generation === projectMutationGenerationRef.current && projectId === requestProjectId) {
        toast.error(
          t("research.toast.projectMutationFailed", {
            defaultValue: "Could not update project: {{error}}",
            error: message(error),
          }),
        );
      }
    } finally {
      if (generation === projectMutationGenerationRef.current) setProjectMutationPending(false);
    }
  };

  const renameProject = async (title: string) => {
    const normalized = title.trim();
    if (!normalized) return;
    await mutateProject("rename", normalized);
  };

  const archiveProject = async () => {
    await mutateProject("archive");
  };

  const restoreProject = async () => {
    await mutateProject("restore");
  };

  const createProject = async (event: React.FormEvent) => {
    event.preventDefault();
    await createProjectWithTitle(projectTitle);
  };

  const importPdf = async (
    event: React.ChangeEvent<HTMLInputElement>,
    pendingAttachment: PendingCandidatePdfAttachment | null = null,
  ) => {
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
      const requestProjectId = projectId;
      const attachment =
        pendingAttachment?.projectId === requestProjectId &&
        pendingAttachment.occurrenceInvocationId
          ? {
              workflowId: pendingAttachment.workflowId,
              candidateId: pendingAttachment.candidateId,
              candidateSha256: pendingAttachment.candidateSha256,
              occurrenceInvocationId: pendingAttachment.occurrenceInvocationId,
            }
          : undefined;
      let source: ResearchSource;
      try {
        source = await scienceCore.importPdf(requestProjectId, file, attachment);
      } catch (error) {
        if (
          attachment &&
          (error as { code?: string }).code === "candidate-pdf-identity-mismatch" &&
          window.confirm(
            t("research.literature.discovery.confirmMismatch", {
              defaultValue:
                "The PDF title does not clearly match this candidate. Attach it anyway?",
            }),
          )
        ) {
          source = await scienceCore.importPdf(requestProjectId, file, {
            ...attachment,
            confirmIdentityMismatch: true,
          });
        } else {
          throw error;
        }
      }
      if (sourcesProjectIdRef.current !== requestProjectId) return;
      setSources((current) => [source, ...current.filter((item) => item.id !== source.id)]);
      setPdfSelection({ sourceId: source.id, pageIndex: 0 });
      if (attachment) {
        setReaderSourceRequest({
          projectId: requestProjectId,
          sourceId: source.id,
          requestId: createRequestKey(),
        });
        setDiscoverySnapshot((current) =>
          current
            ? {
                ...current,
                candidates: {
                  ...current.candidates,
                  items: current.candidates.items.map((candidate) =>
                    candidate.id === attachment.candidateId
                      ? {
                          ...candidate,
                          attachmentStatus: "verified-local-source",
                          attachedSourceId: source.id,
                        }
                      : candidate,
                  ),
                },
              }
            : current,
        );
      }
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

  const importCandidatePdf = async (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const input = event.currentTarget;
    const {
      projectId: attachmentProjectId,
      workflowId,
      candidateId,
      candidateSha256,
      occurrenceInvocationId,
    } = input.dataset;
    const attachment =
      attachmentProjectId &&
      workflowId &&
      candidateId &&
      candidateSha256 &&
      occurrenceInvocationId
        ? {
            projectId: attachmentProjectId,
            workflowId,
            candidateId,
            candidateSha256,
            occurrenceInvocationId,
          }
        : null;
    delete input.dataset.projectId;
    delete input.dataset.workflowId;
    delete input.dataset.candidateId;
    delete input.dataset.candidateSha256;
    delete input.dataset.occurrenceInvocationId;
    await importPdf(event, attachment);
  };

  const requestCandidatePdf = (
    candidate: DiscoveryCandidate,
    workflowId: string,
  ) => {
    const occurrence = candidate.occurrences[0];
    const input = candidatePdfInputRef.current;
    if (!projectId || !serviceReady || !occurrence || !input) return;
    input.dataset.projectId = projectId;
    input.dataset.workflowId = workflowId;
    input.dataset.candidateId = candidate.id;
    input.dataset.candidateSha256 = candidate.candidateSha256;
    input.dataset.occurrenceInvocationId = occurrence.invocationId;
    input.click();
  };

  const importCslJson = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const input = event.currentTarget;
    const file = input.files?.[0];
    input.value = "";
    const identity = discoveryIdentity;
    if (!file || !projectId || !identity || !serviceReady) return;
    if (!file.name.toLowerCase().endsWith(".json")) {
      toast.error(t("research.literature.discovery.cslJsonOnly"));
      return;
    }
    const expectedIdentityKey = identity.key;
    setCslJsonImporting(true);
    try {
      const result = await scienceCore.importCslJsonCandidates(
        projectId,
        identity.workflowId,
        file,
        { idempotencyKey: createRequestKey() },
      );
      if (
        projectId !== researchProjectIdRef.current
        || discoveryIdentityKeyRef.current !== expectedIdentityKey
        || result.projectId !== projectId
        || result.workflowId !== identity.workflowId
      ) return;
      toast.success(
        t("research.literature.discovery.cslJsonImported", {
          imported: result.importedCount,
          unchanged: result.unchangedCount,
        }),
      );
      setDiscoveryRefresh((value) => value + 1);
    } catch (error) {
      if (
        projectId === researchProjectIdRef.current
        && discoveryIdentityKeyRef.current === expectedIdentityKey
      ) {
        toast.error(
          t("research.literature.discovery.cslJsonImportFailed", {
            error: message(error),
          }),
        );
      }
    } finally {
      if (
        projectId === researchProjectIdRef.current
        && discoveryIdentityKeyRef.current === expectedIdentityKey
      ) setCslJsonImporting(false);
    }
  };

  const importDataset = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !projectId || !serviceReady) return;
    if (!file.name.toLowerCase().endsWith(".csv")) {
      toast.error(
        t("research.toast.csvOnly", {
          defaultValue: "Choose a CSV file.",
        }),
      );
      return;
    }

    setImportingDataset(true);
    try {
      const source = await scienceCore.importDataset(projectId, file);
      setSources((current) => [
        source,
        ...current.filter((item) => item.id !== source.id),
      ]);
      toast.success(
        t("research.toast.datasetImported", {
          defaultValue: "CSV dataset imported and ready for analysis.",
        }),
      );
    } catch (error) {
      toast.error(
        t("research.toast.importDatasetFailed", {
          defaultValue: "Could not import dataset: {{error}}",
          error: message(error),
        }),
      );
    } finally {
      setImportingDataset(false);
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
      evidence: {
        text: evidence.text,
        pageLabel: evidence.pageLabel,
        quoteHash: evidence.quoteHash,
        extractionMethod: evidence.extractionMethod,
        confidence: evidence.confidence,
        verified: evidence.verified,
      },
    });
    setInspectorTab("evidence");
    setInspectorOpen(true);
  };

  const selectWorkflowEvidence = (evidence: WorkflowEvidenceRelationship) => {
    setPdfSelection({
      sourceId: evidence.sourceId,
      pageIndex: evidence.pageIndex,
      evidenceId: evidence.evidenceId,
      evidence: {
        text: evidence.text,
        pageLabel: evidence.pageLabel,
        quoteHash: evidence.quoteHash,
        extractionMethod: evidence.extractionMethod,
        confidence: evidence.confidence,
        verified: evidence.verified,
        relationship: evidence.relationship,
        sourceContentHash: evidence.sourceContentHash,
        sourcePageManifestHash: evidence.sourcePageManifestHash,
      },
    });
    setInspectorTab("evidence");
    setInspectorOpen(true);
  };

  const openReviewInspector = () => {
    setInspectorTab("review");
    setInspectorOpen(true);
  };
  const openActivityInspector = () => {
    setInspectorTab("activity");
    setInspectorOpen(true);
  };
  const selectSource = (source: ResearchSource) => {
    setPdfSelection({ sourceId: source.id, pageIndex: 0 });
    setInspectorTab("evidence");
    setInspectorOpen(true);
  };
  const openWorkflowReport = (workflowId: string) => {
    if (!projectId) return;
    workflow.selectWorkflow(workflowId);
    setReportOpenRequest({
      projectId,
      workflowId,
      requestId: createRequestKey(),
    });
    setPresentationMode(LITERATURE_PRESENTATION);
  };

  if (presentationMode === "competitive" && !datasetWorkspaceDefault) {
    return (
      <div className="h-full min-h-0 overflow-hidden bg-bg">
        <input
          ref={pdfInputRef}
          data-pdf-input="standard"
          type="file"
          accept="application/pdf,.pdf"
          onChange={(event) => void importPdf(event)}
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
        />
        <input
          ref={candidatePdfInputRef}
          data-pdf-input="candidate"
          type="file"
          accept="application/pdf,.pdf"
          onChange={(event) => void importCandidatePdf(event)}
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
        />
        <input
          ref={cslJsonInputRef}
          data-csl-json-input="candidate"
          type="file"
          accept=".json,application/json"
          onChange={(event) => void importCslJson(event)}
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
        />
        <input
          ref={datasetInputRef}
          type="file"
          accept=".csv,text/csv"
          onChange={(event) => void importDataset(event)}
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
        />
        <CompetitiveResearchWorkspace
          projectId={projectId}
          projectReady={Boolean(projectId)}
          projectTitle={selectedProject?.title ?? null}
          projectListLoading={booting}
          projectListResolved={projectListResolved}
          projectListError={userFacingPageError}
          onRetryProjectList={() => void loadWorkspace(true)}
          sources={sources}
          candidateTriageDecisions={candidateTriageDecisions}
          candidateTriageLoading={candidateTriageLoading}
          candidateTriageError={candidateTriageError}
          candidateTriageMutationPending={candidateTriageMutationPending}
          onUpsertCandidateTriageDecision={upsertCandidateTriageDecision}
          screeningDecisions={screeningDecisions}
          screeningDecisionsLoading={screeningDecisionsLoading}
          screeningDecisionsError={screeningDecisionsError}
          screeningMutationPending={screeningMutationPending}
          onUpsertScreeningDecision={upsertScreeningDecision}
          evidenceDirections={evidenceDirections}
          evidenceDirectionsLoading={evidenceDirectionsLoading}
          evidenceDirectionsError={evidenceDirectionsError}
          evidenceDirectionMutationPending={evidenceDirectionMutationPending}
          onUpsertEvidenceDirection={upsertEvidenceDirection}
          extractionMatrix={extractionMatrix}
          extractionLoading={extractionLoading}
          extractionError={extractionError}
          extractionProjectIdRef={extractionProjectIdRef}
          extractionGeneration={extractionLoadGenerationRef.current}
          onCreateExtractionColumn={createExtractionColumn}
          onUpsertExtractionCell={upsertExtractionCell}
          onCreateExactEvidenceSpan={createExactEvidenceSpan}
          citedBriefResult={citedBriefResult}
          onCreateCitedBrief={createConfirmedExtractionCitedBrief}
          onDeleteExtractionCell={deleteExtractionCell}
          workflowStatus={workflow.snapshot?.workflow.status ?? null}
          workflowEvents={workflow.events}
          workflowResult={workflow.snapshot?.result ?? null}
          workflowSnapshot={workflow.snapshot}
          discoveryActive={discoveryIdentity !== null}
          discoverySnapshot={discoverySnapshot}
          discoveryLoading={discoveryLoading}
          discoveryError={discoveryError}
          discoveryCreating={discoveryCreating}
          discoveryCreateError={discoveryCreateError}
          evidenceCoverage={evidenceCoverage}
          evidenceCoverageLoading={evidenceCoverageLoading}
          evidenceCoverageError={evidenceCoverageError}
          onRetryEvidenceCoverage={() => setEvidenceCoverageRetry((value) => value + 1)}
          memoryController={memoryController}
          answer={answer}
          serviceReady={Boolean(serviceReady)}
          serviceUnavailableReason={serviceUnavailableReason}
          creatingProject={creatingProject}
          createProjectError={createProjectError}
          onCreateProject={createProjectWithTitle}
          onStartFirstDiscovery={startFirstDiscovery}
          onOpenRuntimeHelp={
            runtimeNeedsContainerEngine
              ? () => void openExternal(DOCKER_DESKTOP_DOWNLOAD_URL)
              : undefined
          }
          runtimeHelpLabel={
            runtimeNeedsContainerEngine
              ? t("research.installDocker", { defaultValue: "Get Docker Desktop" })
              : undefined
          }
          onImportPdfRequest={() => pdfInputRef.current?.click()}
          onAttachCandidatePdfRequest={requestCandidatePdf}
          onImportCslJsonRequest={() => cslJsonInputRef.current?.click()}
          cslJsonImporting={cslJsonImporting}
          readerSourceRequest={readerSourceRequest}
          reportOpenRequest={reportOpenRequest}
          onReportOpenRequestConsumed={(requestId) => {
            setReportOpenRequest((current) =>
              current?.requestId === requestId ? null : current,
            );
          }}
          onOpenDataset={() => setPresentationMode(DATASET_PRESENTATION)}
          onOpenWorkflow={() => setPresentationMode(WORKFLOW_PRESENTATION)}
          onStartDiscovery={startDiscovery}
          onStartSynthesis={startLiteratureSynthesis}
        />
      </div>
    );
  }

  if (
    presentationMode === "dataset" ||
    (presentationMode === "competitive" && datasetWorkspaceDefault)
  ) {
    return (
      <div className="h-full min-h-0 overflow-hidden bg-bg">
        <input
          ref={datasetInputRef}
          type="file"
          accept=".csv,text/csv"
          onChange={(event) => void importDataset(event)}
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
        />
        <DatasetResearchWorkspace
          projectTitle={selectedProject?.title ?? null}
          sources={sources}
          snapshot={workflow.snapshot}
          events={workflow.events}
          mutating={workflow.mutating}
          importing={importingDataset}
          serviceReady={Boolean(serviceReady)}
          literatureAvailable={paperSources.length > 0}
          onImportDatasetRequest={() => datasetInputRef.current?.click()}
          onOpenWorkflow={() => setPresentationMode(WORKFLOW_PRESENTATION)}
          onOpenLiterature={() => setPresentationMode(LITERATURE_PRESENTATION)}
          onRefresh={workflow.refresh}
          onDecision={workflow.decideAnalysis}
          onResolveAgentDecision={workflow.resolveAgentDecision}
          onCancel={workflow.cancel}
          onRetry={workflow.retry}
          onResume={workflow.resume}
          onAcceptReviewWarnings={workflow.acceptReviewWarnings}
        />
      </div>
    );
  }

  return (
    <div className="research-workbench relative flex h-full min-h-0 overflow-hidden bg-bg">
      <ResearchLibrarySidebar
        inactive={compactInspector && inspectorOpen}
        health={health}
        booting={booting}
        serviceReady={serviceReady}
        projects={projects}
        projectId={projectId}
        workflows={workflow.workflows}
        snapshot={workflow.snapshot}
        selectedWorkflowId={workflow.selectedWorkflowId}
        loadingWorkflows={workflow.loadingList}
        workflowMutating={workflow.mutating}
        sources={sources}
        loadingSources={loadingSources}
        selection={pdfSelection}
        importing={importing}
        projectMutating={projectMutationPending}
        showArchivedProjects={showArchivedProjects}
        pdfInputRef={pdfInputRef}
        onProjectChange={(nextProjectId) => {
          projectMutationGenerationRef.current += 1;
          setProjectMutationPending(false);
          setProjectId(nextProjectId);
          setProjectTitle("");
        }}
        onNewProject={() => {
          setProjectId(null);
          setProjectTitle("");
        }}
        onSelectWorkflow={workflow.selectWorkflow}
        onOpenWorkflowReport={openWorkflowReport}
        onNewWorkflow={() => {
          workflow.startNew();
          setAnswer(null);
        }}
        onSelectSource={selectSource}
        onImportPdf={importPdf}
        onToggleArchivedProjects={(show) => {
          setShowArchivedProjects(show);
          projectMutationGenerationRef.current += 1;
          setProjectMutationPending(false);
        }}
        onRenameProject={renameProject}
        onArchiveProject={archiveProject}
        onRestoreProject={restoreProject}
      />

      <main
        className="flex min-w-[25rem] flex-1 flex-col bg-bg max-[1180px]:min-w-[19rem]"
        aria-hidden={(compactInspector && inspectorOpen) || undefined}
        {...(compactInspector && inspectorOpen ? { inert: "" } : {})}
      >
        <header className="shrink-0 border-b border-border bg-surface px-4 pb-3 pt-3.5 xl:px-6">
          <div className="mx-auto flex w-full max-w-6xl items-start gap-3">
            <div className="min-w-0 flex-1">
              <h2 className="truncate text-base font-semibold leading-6 text-text">
                {selectedProject?.title ?? t("research.title", { defaultValue: "Research" })}
              </h2>
              <p className="mt-0.5 truncate text-xs text-muted">
                {workspaceGuidance}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <button
                type="button"
                onClick={() => setPresentationMode(LITERATURE_PRESENTATION)}
                className="touch-target flex min-h-10 items-center rounded-input border border-border bg-surface px-3 text-xs font-medium text-text hover:bg-surface-2"
              >
                {t("research.literature.literatureReview")}
              </button>
              <button
                type="button"
                onClick={() => setPresentationMode(DATASET_PRESENTATION)}
                className="touch-target flex min-h-10 items-center rounded-input border border-border bg-surface px-3 text-xs font-medium text-text hover:bg-surface-2"
              >
                {t("research.literature.datasetTitle")}
              </button>
              <button
                type="button"
                onClick={() => void loadWorkspace(true)}
                disabled={booting}
                className="touch-target flex h-10 w-10 items-center justify-center rounded-input text-muted hover:bg-surface-2 hover:text-text disabled:opacity-40"
                aria-label={t("research.refreshAria", { defaultValue: "Refresh research workspace" })}
              >
                <RefreshCw size={14} className={cn(booting && "animate-spin")} />
              </button>
              {projectId && (
                <button
                  ref={inspectorToggleRef}
                  type="button"
                  onClick={() => (inspectorOpen ? closeInspector() : setInspectorOpen(true))}
                  aria-pressed={inspectorOpen}
                  className={cn(
                    "touch-target flex min-h-10 items-center gap-2 rounded-input px-3 text-xs font-medium",
                    inspectorOpen
                      ? "bg-surface-2 text-text"
                      : "text-muted hover:bg-surface-2 hover:text-text",
                  )}
                  aria-label={t("research.inspector.toggle", {
                    defaultValue: "Toggle evidence inspector",
                  })}
                >
                  <PanelRightOpen size={15} />
                  <span>{t("research.inspector.evidence", { defaultValue: "Evidence" })}</span>
                </button>
              )}
            </div>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 xl:px-6">
          <div className="mx-auto w-full max-w-6xl">
            {pageError && (
              <div
                role="alert"
                className="mb-3 flex flex-col gap-3 rounded-card border border-error/25 bg-error/5 px-3.5 py-3 text-ui sm:flex-row sm:items-start"
              >
                <AlertTriangle size={15} className="mt-0.5 shrink-0 text-error" />
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-text">
                    {runtimeNeedsContainerEngine
                      ? t("research.runtimeDockerTitle", {
                          defaultValue: "Start the local research engine",
                        })
                      : runtimeResourcesUnavailable
                        ? t("research.runtimeResourcesTitle", {
                            defaultValue: "Local research runtime is missing",
                          })
                        : t("research.offlineTitle", {
                            defaultValue: "Local research service needs attention",
                          })}
                  </p>
                  <p className="mt-0.5 text-xs leading-5 text-muted">
                    {userFacingPageError}
                  </p>
                  {pageError !== scienceCoreConfigurationError && (
                    <details className="mt-1 text-xs text-muted">
                      <summary className="w-fit cursor-pointer text-link hover:underline">
                        {t("research.offlineDetails", { defaultValue: "View technical reason" })}
                      </summary>
                      <p className="mt-1.5 break-words font-mono text-caption leading-5">{pageError}</p>
                    </details>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-1.5 sm:pl-3">
                  {runtimeNeedsContainerEngine ? (
                    <button
                      type="button"
                      onClick={() => void openExternal(DOCKER_DESKTOP_DOWNLOAD_URL)}
                      className="touch-target flex min-h-9 items-center rounded-input border border-border bg-surface px-3 text-xs font-medium text-text hover:bg-surface-2"
                    >
                      {t("research.installDocker", { defaultValue: "Get Docker Desktop" })}
                    </button>
                  ) : !runtimeResourcesUnavailable ? (
                    <button
                      type="button"
                      onClick={() => navigate("/settings")}
                      className="touch-target flex min-h-9 items-center rounded-input border border-border bg-surface px-3 text-xs font-medium text-text hover:bg-surface-2"
                    >
                      {t("research.openSettings", { defaultValue: "Open Settings" })}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => void loadWorkspace(true)}
                    disabled={booting}
                    className="touch-target flex min-h-9 items-center rounded-input px-2.5 text-xs font-medium text-link hover:bg-surface disabled:opacity-40"
                  >
                    {t("research.retry", { defaultValue: "Retry" })}
                  </button>
                </div>
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
              <span className="min-w-0 flex-1">
                {t("research.modelGatewayUnconfigured", {
                  defaultValue:
                    "PaperQA is installed, but its model gateway is not configured. Local workflows remain available; legacy quick questions are paused.",
                })}
              </span>
              <button
                type="button"
                onClick={() => navigate("/settings")}
                className="shrink-0 font-medium text-link hover:underline"
              >
                {t("research.configureModel", { defaultValue: "Configure model" })}
              </button>
            </div>
          )}

            <WorkflowWorkspace
            snapshot={workflow.snapshot}
            interactions={workflow.interactions}
            sources={sources}
            loading={workflow.loadingSnapshot}
            loadingInteractions={workflow.loadingInteractions}
            mutating={workflow.mutating}
            connection={workflow.connection}
            error={workflow.error}
            canStart={Boolean(projectId && serviceReady && !loadingSources)}
            projectReady={Boolean(projectId)}
            serviceReady={Boolean(serviceReady)}
            sourcesLoading={loadingSources}
            importingSource={importing}
            importingDataset={importingDataset}
            projectTitle={projectTitle}
            creatingProject={creatingProject}
            remoteDestination={health?.modelDestination ?? null}
            onCreate={workflow.create}
            onProjectTitleChange={setProjectTitle}
            onCreateProject={createProject}
            onRespondToInteraction={workflow.respondToInteraction}
            onApprovePlan={workflow.approvePlan}
            onDecideAnalysis={workflow.decideAnalysis}
            onResolveAgentDecision={workflow.resolveAgentDecision}
            onAcceptReviewWarnings={workflow.acceptReviewWarnings}
            onImportDataset={importDataset}
            onImportPdfRequest={() => pdfInputRef.current?.click()}
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
        </div>
      </main>

      {inspectorOpen && (
        <>
          <button
            type="button"
            onClick={closeInspector}
            className="absolute inset-0 z-20 hidden cursor-default bg-black/20 max-[1440px]:block"
            aria-hidden="true"
            tabIndex={-1}
          />
          <ResearchInspector
            modal={compactInspector}
            activeTab={inspectorTab}
            onTabChange={setInspectorTab}
            onClose={closeInspector}
            selectedSource={selectedSource}
            selection={pdfSelection}
            review={workflow.snapshot?.latestReview ?? null}
            result={workflow.snapshot?.result ?? null}
            onSelectEvidence={selectWorkflowEvidence}
            events={workflow.events}
            memoryController={memoryController}
          />
        </>
      )}
    </div>
  );
}
