import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  ArrowRight,
  BookOpen,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  FileSearch,
  FileText,
  FileUp,
  Filter,
  FolderOpen,
  ListFilter,
  Loader2,
  Maximize2,
  Plus,
  Search,
  Table2,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import type {
  ResearchAnswer,
  EvidenceSpan,
  CreateExactEvidenceSpanInput,
  CandidateTriageDecision,
  CandidateTriageDecisionValue,
  DiscoveryCandidate,
  EvidenceDirectionJudgment,
  EvidenceDirectionValue,
  ResearchSource,
  ReportCitationRebaseInput,
  ReportDraftExport,
  ReportDraftRecord,
  ExtractionMatrix,
  CreateExtractionColumnInput,
  UpsertExtractionCellInput,
  UpsertCandidateTriageDecisionInput,
  UpsertEvidenceDirectionJudgmentInput,
  ScreeningDecision,
  UpsertScreeningDecisionInput,
  ResearchWorkflowResult,
  ResearchWorkflowSnapshot,
  ResearchWorkflowStatus,
  WorkflowDiscoverySnapshot,
  WorkflowEvidenceCoverage,
  WorkflowEvent,
} from "@spark/research-domain";
import { ScienceCoreApiError } from "@spark/research-sdk";
import { MarkdownViewer } from "@/components/markdown-viewer/MarkdownViewer";
import { cn } from "@/lib/cn";
import { useTranslation } from "react-i18next";
import { useSourcePdfBlob } from "./useSourcePdfBlob";
import { copyText } from "@/lib/clipboard";
import {
  saveBinaryWithFeedback,
  saveTextWithFeedback,
  type SaveTextFeedbackMessages,
} from "@/lib/download";
import { toast } from "@/lib/toast";
import { scienceCore } from "@/lib/scienceCore";
import {
  buildVerifiedReportExport,
  canonicalEvidenceKey,
  serializeCitationText,
  type VerifiedCitationExport,
  type VerifiedReportExport,
} from "./researchReportExport";
import { buildReportDocx } from "./researchReportDocx";
import {
  buildExtractionCsv,
  type ExtractionExportPaper,
} from "./researchExtractionExport";
import {
  canonicalAutonomousLiteratureIdentity,
  matchesAutonomousLiteratureIdentity,
} from "./workflowModel";
import {
  RESEARCH_MEMORY_PANEL_DENSITY,
  ResearchMemoryPanel,
  type ResearchMemoryPanelController,
} from "./ResearchMemoryPanel";

export type LiteratureSurface =
  | "home"
  | "answer"
  | "papers"
  | "screening"
  | "extraction"
  | "reader"
  | "report";

type ScreeningExportState = "ready" | "loading" | "error" | "saving";
type ReportDocumentFormat = "markdown" | "docx" | "pdf";

const DOCX_MIME =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

async function exportReportDocument(
  format: ReportDocumentFormat,
  markdown: string,
  messages: SaveTextFeedbackMessages,
): Promise<"saved" | "print-opened"> {
  if (format === "markdown") {
    await saveTextWithFeedback(
      "spark-research-synthesis.md",
      markdown,
      "text/markdown",
      messages,
    );
    return "saved";
  }
  if (format === "docx") {
    const bytes = await buildReportDocx(markdown);
    await saveBinaryWithFeedback(
      "spark-research-synthesis.docx",
      bytes,
      DOCX_MIME,
      messages,
    );
    return "saved";
  }

  document.body.classList.add("spark-report-printing");
  try {
    window.print();
  } finally {
    document.body.classList.remove("spark-report-printing");
  }
  return "print-opened";
}

interface CompetitiveResearchWorkspaceProps {
  projectId: string | null;
  projectReady: boolean;
  projectTitle: string | null;
  projectListLoading?: boolean;
  projectListResolved?: boolean;
  projectListError?: string | null;
  onRetryProjectList?: () => void;
  sources: ResearchSource[];
  candidateTriageDecisions: CandidateTriageDecision[];
  candidateTriageLoading: boolean;
  candidateTriageError: string | null;
  candidateTriageMutationPending?: boolean;
  onUpsertCandidateTriageDecision: (
    candidateId: string,
    input: UpsertCandidateTriageDecisionInput,
  ) => Promise<CandidateTriageDecision>;
  screeningDecisions: ScreeningDecision[];
  screeningDecisionsLoading: boolean;
  screeningDecisionsError: string | null;
  screeningMutationPending?: boolean;
  evidenceDirections?: EvidenceDirectionJudgment[];
  evidenceDirectionsLoading?: boolean;
  evidenceDirectionsError?: string | null;
  evidenceDirectionMutationPending?: boolean;
  onUpsertEvidenceDirection?: (
    sourceId: string,
    input: UpsertEvidenceDirectionJudgmentInput,
  ) => Promise<EvidenceDirectionJudgment>;
  workflowStatus: ResearchWorkflowStatus | null;
  workflowEvents: WorkflowEvent[];
  workflowResult: ResearchWorkflowResult | null;
  workflowSnapshot?: ResearchWorkflowSnapshot | null;
  discoveryActive?: boolean;
  discoverySnapshot?: WorkflowDiscoverySnapshot | null;
  discoveryLoading?: boolean;
  discoveryError?: string | null;
  discoveryCreating?: boolean;
  discoveryCreateError?: string | null;
  evidenceCoverage?: WorkflowEvidenceCoverage | null;
  evidenceCoverageLoading?: boolean;
  evidenceCoverageError?: string | null;
  onRetryEvidenceCoverage?: () => void;
  memoryController?: ResearchMemoryPanelController;
  answer: ResearchAnswer | null;
  serviceReady: boolean;
  serviceUnavailableReason: string | null;
  creatingProject: boolean;
  createProjectError?: string | null;
  onCreateProject: (title: string) => Promise<boolean>;
  onStartFirstDiscovery?: (
    question: string,
    provider: "crossref" | "openalex" | "crossref-openalex",
  ) => Promise<boolean>;
  onOpenRuntimeHelp?: () => void;
  runtimeHelpLabel?: string;
  onUpsertScreeningDecision: (
    sourceId: string,
    input: UpsertScreeningDecisionInput,
  ) => Promise<ScreeningDecision>;
  extractionMatrix: ExtractionMatrix;
  extractionLoading: boolean;
  extractionError: string | null;
  /** Parent-owned request identity keeps an old project's mutations inert. */
  extractionProjectIdRef?: { current: string | null };
  extractionGeneration?: number;
  onCreateExtractionColumn: (input: CreateExtractionColumnInput) => Promise<unknown>;
  onUpsertExtractionCell: (
    sourceId: string, columnId: string, input: UpsertExtractionCellInput,
  ) => Promise<unknown>;
  onCreateExactEvidenceSpan?: (
    sourceId: string,
    input: CreateExactEvidenceSpanInput,
  ) => Promise<EvidenceSpan>;
  citedBriefResult?: ResearchWorkflowResult | null;
  onCreateCitedBrief?: () => Promise<ResearchWorkflowResult>;
  onDeleteExtractionCell: (sourceId: string, columnId: string, expectedVersion: number) => Promise<void>;
  onImportPdfRequest: () => void;
  onAttachCandidatePdfRequest?: (
    candidate: DiscoveryCandidate,
    workflowId: string,
  ) => void;
  onImportCslJsonRequest?: () => void;
  cslJsonImporting?: boolean;
  readerSourceRequest?: {
    projectId: string;
    sourceId: string;
    requestId: string;
  } | null;
  reportOpenRequest?: {
    projectId: string;
    workflowId: string;
    requestId: string;
  } | null;
  onReportOpenRequestConsumed?: (requestId: string) => void;
  onOpenDataset: () => void;
  onOpenWorkflow: () => void;
  /** Persists an exact provider scope with no downloads; it never approves or executes it. */
  onStartDiscovery?: (
    question: string,
    provider: "crossref" | "openalex" | "crossref-openalex",
  ) => Promise<boolean>;
  /** Starts or resumes the exact persisted-screening literature input. */
  onStartSynthesis?: (goal: string, sourceIds: string[]) => Promise<void>;
}

interface ResearchPaper {
  id: string;
  title: string;
  authors: string;
  year: string;
  journal: string;
  citations: string;
  population: string;
  relevance: "High" | "Medium" | "Needs review";
  studyType: string;
  summary: string;
  outcome: string;
  quote: string;
  included: boolean;
  origin: "source";
  source: ResearchSource | null;
  pageCount: number | null;
  ingestionLabel: string;
}

interface ReportCitation {
  id: string;
  claim: string;
  sourceId: string;
  sourceTitle: string;
  sourceContentHash: string | null;
  sourcePageManifestHash: string | null;
  pageIndex: number;
  pageLabel: string | null;
  text: string;
  quoteHash: string;
  extractionMethod: string;
  verified: boolean;
  integrityStatus: ResearchWorkflowResult["integrityStatus"] | "legacy-answer";
  relationship: "supporting" | "contradicting";
}

type CitationIntegrity = "verified" | "source-changed" | "needs-review";

interface DraftCitationReference {
  index: number;
  evidenceId: string;
  quoteHash: string;
  token: string;
}

interface ReportCitationRebaseChange extends ReportCitationRebaseInput {
  index: number;
  sourceTitle: string;
  page: string;
}

type ReportCitationRebasePlan =
  | {
      ok: true;
      citationRebases: ReportCitationRebaseInput[];
      changes: ReportCitationRebaseChange[];
    }
  | { ok: false; issues: string[] };

const SURFACES: Array<{ id: LiteratureSurface; label: string }> = [
  { id: "answer", label: "Answer" },
  { id: "papers", label: "Papers" },
  { id: "screening", label: "Screening" },
  { id: "extraction", label: "Extraction" },
  { id: "reader", label: "Reading" },
  { id: "report", label: "Synthesis" },
];

// eslint-disable-next-line i18next/no-literal-string -- canonical visual tone discriminants
const STATUS_TONES = { neutral: "neutral", ok: "ok", warn: "warn", error: "error" } as const;

// eslint-disable-next-line i18next/no-literal-string -- canonical evidence-direction discriminants
const ANSWER_DIRECTIONS = ["supporting", "mixed", "insufficient"] as const;

// eslint-disable-next-line i18next/no-literal-string -- canonical direction-filter discriminants
const ANSWER_DIRECTION_FILTERS = ["all", ...ANSWER_DIRECTIONS, "unconfirmed"] as const;

function sortPapersByTitle(papers: readonly ResearchPaper[]): ResearchPaper[] {
  return [...papers].sort((left, right) => left.title.localeCompare(right.title));
}

export function sourceIngestionLabel(status: ResearchSource["ingestionStatus"]): string {
  if (status === "pending") return "Importing";
  if (status === "processing") return "Parsing / indexing";
  if (status === "ready") return "Indexed";
  return "Failed";
}

export function sourceToResearchPaper(source: ResearchSource): ResearchPaper {
  const status = sourceIngestionLabel(source.ingestionStatus);
  const pageCount = source.pageCount;
  const summary =
    source.ingestionStatus === "ready"
      ? `Indexed local PDF${pageCount ? ` · ${pageCount} pages` : ""}. Evidence fields remain unreviewed until a verified workflow result is available.`
      : source.ingestionStatus === "failed"
        ? "Local PDF preparation failed. The source remains recorded, but no evidence is available for review."
        : "Local PDF is still being parsed and indexed. Evidence review is not available yet.";
  const year = source.publicationDate?.slice(0, 4) || "—";
  return {
    id: source.id,
    title: source.title,
    authors: source.authors.join(", ") || "Local PDF",
    year,
    journal: "Imported PDF",
    citations: "Local source",
    population: "Not extracted",
    relevance: "Needs review",
    studyType: "Local PDF",
    summary,
    outcome: "Needs review",
    quote: "",
    included: source.ingestionStatus === "ready",
    origin: "source",
    source,
    pageCount,
    ingestionLabel: status,
  };
}

function reportCitations(
  result: ResearchWorkflowResult | null,
  sources: readonly ResearchSource[],
): ReportCitation[] {
  if (result) {
    return result.claims.flatMap((claim) =>
      claim.evidence.map((evidence) => {
        const source = sources.find((candidate) => candidate.id === evidence.sourceId);
        return ({
        id: evidence.evidenceId,
        claim: claim.statement,
        sourceId: evidence.sourceId,
        sourceTitle:
          evidence.sourceTitle ??
          source?.title ??
          evidence.sourceId,
        sourceContentHash: evidence.sourceContentHash,
        sourcePageManifestHash: evidence.sourcePageManifestHash,
        pageIndex: evidence.pageIndex,
        pageLabel: evidence.pageLabel,
        text: evidence.text,
        quoteHash: evidence.quoteHash,
        extractionMethod: evidence.extractionMethod,
        verified: evidence.verified,
        integrityStatus: result.integrityStatus,
        relationship: evidence.relationship,
      }); }),
    );
  }
  return [];
}

function reportEvidenceToken(evidenceId: string, quoteHash: string): string {
  return `[@evidence:${evidenceId}:${quoteHash}]`;
}

/**
 * Legacy same-project answers keep their own evidence spans. They never carry
 * frozen source integrity metadata, so every mapped citation stays in the
 * "legacy-answer" integrity band and renders as needs-review, never verified.
 */
function legacyAnswerCitations(
  answer: ResearchAnswer | null,
  sources: readonly ResearchSource[],
): ReportCitation[] {
  if (!answer) return [];
  return answer.claims.flatMap((claim) =>
    claim.evidence.map((evidence) => {
      const source = sources.find((candidate) => candidate.id === evidence.sourceId);
      return {
        id: evidence.id,
        claim: claim.statement,
        sourceId: evidence.sourceId,
        sourceTitle: source?.title ?? evidence.sourceId,
        sourceContentHash: null,
        sourcePageManifestHash: null,
        pageIndex: evidence.pageIndex,
        pageLabel: evidence.pageLabel,
        text: evidence.text,
        quoteHash: evidence.quoteHash,
        extractionMethod: evidence.extractionMethod,
        verified: evidence.verified,
        integrityStatus: "legacy-answer" as const,
        relationship:
          claim.claimType === "contradiction"
            ? ("contradicting" as const)
            : ("supporting" as const),
      };
    }),
  );
}

function buildReportCitationRebasePlan(
  contentMarkdown: string,
  reportExport: VerifiedReportExport,
): ReportCitationRebasePlan {
  if (!reportExport.ok) {
    return {
      ok: false,
      issues: [
        "The current reviewed report is unavailable. Reload the completed workflow before reviewing this draft.",
      ],
    };
  }

  const issues: string[] = [];
  const references: DraftCitationReference[] = [];
  const referencePattern =
    /^[ \t]*([1-9][0-9]{0,4})\.[^\n]*?<!--[ \t]*(\[@evidence:([A-Za-z0-9_-]+):([0-9a-f]{64})\])[ \t]*-->[ \t]*$/gm;
  for (const match of contentMarkdown.matchAll(referencePattern)) {
    references.push({
      index: Number(match[1]),
      token: match[2],
      evidenceId: match[3],
      quoteHash: match[4],
    });
  }

  const allTokens =
    contentMarkdown.match(
      /\[@evidence:[A-Za-z0-9_-]+:[0-9a-f]{64}\]/g,
    ) ?? [];
  const contentWithoutValidTokens = contentMarkdown.replace(
    /\[@evidence:[A-Za-z0-9_-]+:[0-9a-f]{64}\]/g,
    "",
  );
  const visibleIndexes = new Set(
    [...contentMarkdown.matchAll(/\[([1-9][0-9]{0,4})\]/g)].map(
      (match) => Number(match[1]),
    ),
  );
  const referenceIndexes = new Set(references.map((reference) => reference.index));
  const referenceTokens = new Set(references.map((reference) => reference.token));
  if (
    references.length === 0 ||
    contentWithoutValidTokens.includes("[@evidence:")
  ) {
    issues.push(
      "The saved draft has a missing or malformed evidence binding. Reinsert its citations before review.",
    );
  }
  if (
    referenceIndexes.size !== references.length ||
    referenceTokens.size !== references.length ||
    allTokens.length !== references.length
  ) {
    issues.push(
      "Each saved reference must have one unique numbered evidence binding. Remove duplicate or detached bindings before review.",
    );
  }
  if (
    visibleIndexes.size !== referenceIndexes.size ||
    [...visibleIndexes].some((index) => !referenceIndexes.has(index))
  ) {
    issues.push(
      "Every visible citation must resolve to exactly one saved reference. Reinsert the unresolved citation before review.",
    );
  }

  const currentByIndex = new Map<number, VerifiedCitationExport>();
  const currentByToken = new Map<string, VerifiedCitationExport>();
  const currentByEvidenceId = new Map<string, VerifiedCitationExport[]>();
  for (const citation of reportExport.citations) {
    const token = reportEvidenceToken(citation.evidenceId, citation.quoteHash);
    if (
      !Number.isSafeInteger(citation.index) ||
      citation.index < 1 ||
      currentByIndex.has(citation.index)
    ) {
      issues.push(
        "The current reviewed report has a missing or duplicate reference index. Reload the completed workflow before review.",
      );
      continue;
    }
    if (
      !/^[A-Za-z0-9_-]+$/.test(citation.evidenceId) ||
      !/^[0-9a-f]{64}$/.test(citation.quoteHash) ||
      currentByToken.has(token)
    ) {
      issues.push(
        "The current reviewed report has an ambiguous evidence binding. Reload the completed workflow before review.",
      );
      continue;
    }
    currentByIndex.set(citation.index, citation);
    currentByToken.set(token, citation);
    const identityMatches = currentByEvidenceId.get(citation.evidenceId) ?? [];
    identityMatches.push(citation);
    currentByEvidenceId.set(citation.evidenceId, identityMatches);
  }
  for (let index = 1; index <= reportExport.citations.length; index += 1) {
    if (!currentByIndex.has(index)) {
      issues.push(
        `Current report reference [${index}] is missing or duplicated. Reload the completed workflow before review.`,
      );
    }
  }
  if (reportExport.citations.length === 0) {
    issues.push(
      "The current reviewed report has no evidence references to rebind.",
    );
  }
  if (issues.length > 0) {
    return { ok: false, issues: [...new Set(issues)] };
  }

  const occupiedTargets = new Map<string, number>();
  for (const reference of references) {
    if (currentByToken.has(reference.token)) {
      occupiedTargets.set(reference.token, reference.index);
    }
  }

  const citationRebases: ReportCitationRebaseInput[] = [];
  const changes: ReportCitationRebaseChange[] = [];
  for (const reference of references) {
    if (currentByToken.has(reference.token)) continue;

    const identityMatches = currentByEvidenceId.get(reference.evidenceId) ?? [];
    let current: VerifiedCitationExport | undefined;
    if (identityMatches.length === 1) {
      [current] = identityMatches;
    } else if (identityMatches.length > 1) {
      issues.push(
        `Reference [${reference.index}] matches more than one current citation with evidence ID ${reference.evidenceId}. Reinsert that citation from current evidence.`,
      );
      continue;
    } else {
      current = currentByIndex.get(reference.index);
    }
    if (!current) {
      issues.push(
        `Reference [${reference.index}] has no unique current citation. Reinsert it from current evidence before review.`,
      );
      continue;
    }

    const currentToken = reportEvidenceToken(
      current.evidenceId,
      current.quoteHash,
    );
    const occupiedBy = occupiedTargets.get(currentToken);
    if (occupiedBy !== undefined && occupiedBy !== reference.index) {
      issues.push(
        `References [${occupiedBy}] and [${reference.index}] resolve to the same current evidence. Reinsert one citation before review.`,
      );
      continue;
    }
    occupiedTargets.set(currentToken, reference.index);
    const rebase = {
      previousEvidenceId: reference.evidenceId,
      previousQuoteHash: reference.quoteHash,
      currentEvidenceId: current.evidenceId,
      currentQuoteHash: current.quoteHash,
    };
    citationRebases.push(rebase);
    changes.push({
      ...rebase,
      index: reference.index,
      sourceTitle: current.sourceTitle,
      page: current.pageLabel ?? String(current.pageIndex + 1),
    });
  }

  if (issues.length > 0) {
    return { ok: false, issues: [...new Set(issues)] };
  }
  return { ok: true, citationRebases, changes };
}

function citationIntegrity(
  citation: ReportCitation,
  sources: readonly ResearchSource[],
): CitationIntegrity {
  const source = sources.find((candidate) => candidate.id === citation.sourceId);
  if (
    citation.sourceContentHash &&
    source?.contentHash &&
    citation.sourceContentHash !== source.contentHash
  ) {
    return "source-changed";
  }
  if (
    citation.verified &&
    citation.integrityStatus === "verified-frozen-v2" &&
    citation.sourceContentHash &&
    citation.sourcePageManifestHash &&
    source?.ingestionStatus === "ready" &&
    source.contentHash === citation.sourceContentHash
  ) {
    return "verified";
  }
  return "needs-review";
}

export function workflowDisplayState(
  status: ResearchWorkflowStatus | null,
  events: readonly WorkflowEvent[],
): string | null {
  void events;
  if (!status) return null;
  if (status === "running" || status === "reviewing") return "Running";
  if (status === "failed" || status === "blocked") return "Failed";
  if (status === "completed") return "Completed";
  return status.split("-").join(" ");
}

export function CompetitiveResearchWorkspace({
  projectId,
  projectReady,
  projectTitle,
  projectListLoading = false,
  projectListResolved = true,
  projectListError = null,
  onRetryProjectList = () => undefined,
  sources,
  candidateTriageDecisions,
  candidateTriageLoading,
  candidateTriageError,
  candidateTriageMutationPending = false,
  onUpsertCandidateTriageDecision,
  screeningDecisions,
  screeningDecisionsLoading,
  screeningDecisionsError,
  screeningMutationPending = false,
  evidenceDirections = [],
  evidenceDirectionsLoading = false,
  evidenceDirectionsError = null,
  evidenceDirectionMutationPending = false,
  onUpsertEvidenceDirection,
  workflowStatus: _workflowStatus,
  workflowEvents,
  workflowResult: _workflowResult,
  workflowSnapshot = null,
  discoveryActive = false,
  discoverySnapshot = null,
  discoveryLoading = false,
  discoveryError = null,
  discoveryCreating = false,
  discoveryCreateError = null,
  evidenceCoverage = null,
  evidenceCoverageLoading = false,
  evidenceCoverageError = null,
  onRetryEvidenceCoverage = () => undefined,
  memoryController,
  answer: legacyAnswerProp,
  serviceReady,
  serviceUnavailableReason,
  creatingProject,
  createProjectError = null,
  onCreateProject,
  onStartFirstDiscovery,
  onOpenRuntimeHelp,
  runtimeHelpLabel,
  onUpsertScreeningDecision,
  extractionMatrix = { columns: [], cells: [] },
  extractionLoading = false,
  extractionError = null,
  extractionProjectIdRef,
  extractionGeneration = 0,
  onCreateExtractionColumn = async () => undefined,
  onUpsertExtractionCell = async () => undefined,
  onCreateExactEvidenceSpan = async () => {
    throw new Error("Evidence capture is unavailable");
  },
  citedBriefResult = null,
  onCreateCitedBrief,
  onDeleteExtractionCell = async () => undefined,
  onImportPdfRequest,
  onAttachCandidatePdfRequest = () => undefined,
  onImportCslJsonRequest,
  cslJsonImporting = false,
  readerSourceRequest = null,
  reportOpenRequest = null,
  onReportOpenRequestConsumed,
  onOpenDataset,
  onOpenWorkflow,
  onStartDiscovery,
  onStartSynthesis,
}: CompetitiveResearchWorkspaceProps) {
  const { t } = useTranslation("pages");
  const [surface, setSurface] = useState<LiteratureSurface>("home");
  const [discoveryProvider, setDiscoveryProvider] = useState<
    "crossref" | "openalex" | "crossref-openalex"
  >("crossref-openalex");
  const authoritativeLiteratureGoal =
    workflowSnapshot?.workflow.projectId === projectId &&
    workflowSnapshot.workflow.workflowType === "literature-synthesis" &&
    workflowSnapshot.workflow.mode === "autonomous" &&
    workflowSnapshot.workflow.generationMode === "local-deterministic"
      ? workflowSnapshot.workflow.goal.trim()
      : null;
  const [questionDraft, setQuestionDraft] = useState(() => ({
    projectId,
    value: authoritativeLiteratureGoal ?? "",
    edited: false,
  }));
  const question =
    questionDraft.projectId === projectId
      ? questionDraft.value
      : authoritativeLiteratureGoal ?? "";
  useEffect(() => {
    setQuestionDraft((current) => {
      if (current.projectId !== projectId) {
        const firstQuestion =
          current.projectId === null && current.value.trim()
            ? current.value
            : "";
        return {
          projectId,
          value: authoritativeLiteratureGoal ?? firstQuestion,
          edited: authoritativeLiteratureGoal === null && Boolean(firstQuestion),
        };
      }
      if (
        !current.edited &&
        authoritativeLiteratureGoal !== null &&
        current.value !== authoritativeLiteratureGoal
      ) {
        return { ...current, value: authoritativeLiteratureGoal };
      }
      return current;
    });
  }, [authoritativeLiteratureGoal, projectId]);
  const editQuestion = (value: string) => {
    setQuestionDraft({ projectId, value, edited: true });
  };
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [selectedPaperId, setSelectedPaperId] = useState("");
  const [includedIds, setIncludedIds] = useState(() => new Set<string>());
  const [screeningBusyIds, setScreeningBusyIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [screeningMutationError, setScreeningMutationError] = useState<string | null>(null);
  const screeningProjectIdRef = useRef(projectId);
  screeningProjectIdRef.current = projectId;
  useEffect(() => {
    setScreeningBusyIds(new Set());
    setScreeningMutationError(null);
  }, [projectId]);
  const [selectedCitation, setSelectedCitation] = useState<ReportCitation | null>(null);
  const [readerPageIndex, setReaderPageIndex] = useState(0);
  const [readerEvidence, setReaderEvidence] = useState<ReportCitation | EvidenceSpan | null>(null);
  const [reportMode, setReportMode] = useState<"review" | "generating">(
    workflowSnapshot?.workflow.status === "running" || workflowSnapshot?.workflow.status === "reviewing" ? "generating" : "review",
  );
  const [reportDraft, setReportDraft] = useState<ReportDraftRecord | null>(null);
  const [reportDraftLoading, setReportDraftLoading] = useState(false);
  const [reportDraftMutating, setReportDraftMutating] = useState(false);
  const [reportDraftError, setReportDraftError] = useState<string | null>(null);
  const reportProjectIdRef = useRef(projectId);
  reportProjectIdRef.current = projectId;
  useEffect(() => {
    const status = workflowSnapshot?.workflow.status;
    setReportMode(status === "running" || status === "reviewing" ? "generating" : "review");
  }, [workflowSnapshot?.workflow.status]);
  const realPdfSources = useMemo(
    () => sources.filter((source) => source.sourceKind === "pdf" && source.projectId === projectId),
    [sources, projectId],
  );
  const realSourceMode = realPdfSources.length > 0;
  const discoveryOnly = discoveryActive && !realSourceMode;
  const papers = useMemo(
    () => discoveryOnly ? [] : realSourceMode ? realPdfSources.map((source) => {
      const paper = sourceToResearchPaper(source);
      const pages = source.pageCount
        ? t("research.literature.sourcePresentation.readyPages", { count: source.pageCount })
        : "";
      const ingestionLabel = source.ingestionStatus === "pending"
        ? t("research.literature.sourcePresentation.importing")
        : source.ingestionStatus === "processing"
          ? t("research.literature.sourcePresentation.processing")
          : source.ingestionStatus === "ready"
            ? t("research.literature.sourcePresentation.indexed")
            : t("research.literature.sourcePresentation.failed");
      const summary = source.ingestionStatus === "ready"
        ? t("research.literature.sourcePresentation.readySummary", { pages })
        : source.ingestionStatus === "failed"
          ? t("research.literature.sourcePresentation.failedSummary")
          : t("research.literature.sourcePresentation.processingSummary");
      return {
        ...paper,
        authors: source.authors.join(", ") || t("research.literature.sourcePresentation.localPdf"),
        journal: t("research.literature.sourcePresentation.importedPdf"),
        citations: t("research.literature.sourcePresentation.localSource"),
        population: t("research.literature.sourcePresentation.notExtracted"),
        studyType: t("research.literature.sourcePresentation.localPdf"),
        summary,
        outcome: t("research.literature.sourcePresentation.outcome"),
        ingestionLabel,
      };
    }) : [],
    [discoveryOnly, realPdfSources, realSourceMode, t],
  );
  useEffect(() => {
    if (
      readerSourceRequest?.projectId !== projectId ||
      !realPdfSources.some((source) => source.id === readerSourceRequest.sourceId)
    ) {
      return;
    }
    setSelectedPaperId(readerSourceRequest.sourceId);
    setReaderPageIndex(0);
    setReaderEvidence(null);
    setSurface("reader");
  }, [projectId, readerSourceRequest, realPdfSources]);
  const consumedReportRequestRef = useRef<string | null>(null);
  useEffect(() => {
    if (
      !reportOpenRequest ||
      reportOpenRequest.projectId !== projectId ||
      consumedReportRequestRef.current === reportOpenRequest.requestId ||
      workflowSnapshot?.workflow.id !== reportOpenRequest.workflowId ||
      workflowSnapshot.workflow.projectId !== projectId ||
      workflowSnapshot.workflow.workflowType !== "literature-synthesis" ||
      workflowSnapshot.workflow.status !== "completed" ||
      workflowSnapshot.result === null
    ) {
      return;
    }
    consumedReportRequestRef.current = reportOpenRequest.requestId;
    setSelectedCitation(null);
    setReportMode("review");
    setSurface("report");
    onReportOpenRequestConsumed?.(reportOpenRequest.requestId);
  }, [
    onReportOpenRequestConsumed,
    projectId,
    reportOpenRequest,
    workflowSnapshot,
  ]);
  const canonicalSourceIds = useMemo(() => {
    const decisions = new Map(
      screeningDecisions
        .filter((decision) => decision.projectId === projectId)
        .map((decision) => [decision.sourceId, decision.decision]),
    );
    return realPdfSources
      .filter((source) =>
        source.projectId === projectId
        && source.ingestionStatus === "ready"
        && decisions.get(source.id) === "include"
      )
      .map((source) => source.id)
      .sort();
  }, [projectId, realPdfSources, screeningDecisions]);
  const canonicalSourceIdSet = useMemo(
    () => new Set(canonicalSourceIds),
    [canonicalSourceIds],
  );
  const literatureIdentity = useMemo(
    () => canonicalAutonomousLiteratureIdentity(projectId ?? "", question, canonicalSourceIds),
    [canonicalSourceIds, projectId, question],
  );
  const matchedWorkflow = matchesAutonomousLiteratureIdentity(workflowSnapshot, literatureIdentity);
  // The snapshot is the single workflow truth. Separate display props are
  // intentionally ignored here so an out-of-order parent render cannot bind a
  // different run's result or status to this source identity.
  const matchedResult = matchedWorkflow ? workflowSnapshot?.result ?? null : null;
  const matchedStatus = matchedWorkflow ? workflowSnapshot?.workflow.status ?? null : null;
  const reviewPassed = matchedWorkflow && workflowSnapshot?.latestReview?.verdict === "passed";
  const matchedStatusReason = matchedWorkflow && workflowSnapshot
    ? "statusReason" in workflowSnapshot.workflow
      ? workflowSnapshot.workflow.statusReason?.userMessage ?? null
      : null
    : null;
  // The Answer surface reads the same identity-matched snapshot truth as the
  // report. A same-project legacy ResearchAnswer is only a fallback when no
  // identity-matched autonomous workflow exists at all.
  const legacyAnswer = !matchedWorkflow && legacyAnswerProp?.projectId === projectId
    ? legacyAnswerProp
    : null;
  const answerCitations = useMemo(
    () => reportCitations(matchedResult, realPdfSources),
    [matchedResult, realPdfSources],
  );
  const legacyCitations = useMemo(
    () => legacyAnswerCitations(legacyAnswer, realPdfSources),
    [legacyAnswer, realPdfSources],
  );
  const candidateReportExport = useMemo(
    () => buildVerifiedReportExport(
      matchedStatus,
      matchedResult,
      realPdfSources.filter((source) => canonicalSourceIds.includes(source.id)),
      reviewPassed,
    ),
    [canonicalSourceIds, matchedResult, matchedStatus, realPdfSources, reviewPassed],
  );
  // Final report content and its exports share one all-or-nothing proof gate.
  const finalResult = candidateReportExport.ok ? matchedResult : null;
  const displayResult = finalResult ?? citedBriefResult;
  const citations = useMemo(
    () => reportCitations(displayResult, realPdfSources),
    [displayResult, realPdfSources],
  );
  const verifiedCitations = useMemo(
    () => citations.filter((citation) => citationIntegrity(citation, realPdfSources) === "verified"),
    [citations, realPdfSources],
  );
  const sourceChangedCitations = useMemo(
    () => citations.filter((citation) => citationIntegrity(citation, realPdfSources) === "source-changed"),
    [citations, realPdfSources],
  );
  const needsReviewCitations = useMemo(
    () => citations.filter((citation) => citationIntegrity(citation, realPdfSources) === "needs-review"),
    [citations, realPdfSources],
  );
  const reportExport = candidateReportExport;
  const reportWorkflowId = matchedWorkflow
    ? workflowSnapshot?.workflow.id ?? null
    : null;
  const reportWorkflowIdRef = useRef(reportWorkflowId);
  reportWorkflowIdRef.current = reportWorkflowId;
  const reportDraftLoadGeneration = useRef(0);
  const loadReportDraft = async () => {
    if (
      !projectId ||
      !reportWorkflowId ||
      matchedStatus !== "completed" ||
      !candidateReportExport.ok
    ) {
      setReportDraft(null);
      setReportDraftLoading(false);
      setReportDraftError(null);
      return;
    }
    const generation = ++reportDraftLoadGeneration.current;
    setReportDraftLoading(true);
    setReportDraftError(null);
    try {
      let draft: ReportDraftRecord;
      try {
        draft = await scienceCore.getReportDraft(projectId, reportWorkflowId);
      } catch (error) {
        if (!(error instanceof ScienceCoreApiError) || error.status !== 404) {
          throw error;
        }
        draft = await scienceCore.createReportDraft(
          projectId,
          reportWorkflowId,
          { schemaVersion: "1" },
          { idempotencyKey: `report-draft-create:${reportWorkflowId}:v1` },
        );
      }
      if (generation === reportDraftLoadGeneration.current) {
        setReportDraft(draft);
      }
    } catch (error) {
      if (generation === reportDraftLoadGeneration.current) {
        setReportDraft(null);
        setReportDraftError(
          error instanceof Error ? error.message : String(error),
        );
      }
    } finally {
      if (generation === reportDraftLoadGeneration.current) {
        setReportDraftLoading(false);
      }
    }
  };
  useEffect(() => {
    void loadReportDraft();
    return () => {
      reportDraftLoadGeneration.current += 1;
    };
    // The exact project/workflow identity and verified result gate own this request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidateReportExport.ok, matchedStatus, projectId, reportWorkflowId]);
  const savePersistentReportDraft = async (
    contentMarkdown: string,
  ): Promise<ReportDraftRecord> => {
    if (!projectId || !reportWorkflowId || !reportDraft) {
      throw new Error("The persisted report draft is unavailable.");
    }
    const requestProjectId = projectId;
    const requestWorkflowId = reportWorkflowId;
    setReportDraftMutating(true);
    setReportDraftError(null);
    try {
      const updated = await scienceCore.saveReportDraft(
        projectId,
        reportWorkflowId,
        reportDraft.id,
        {
          expectedRevision: reportDraft.revision,
          expectedContentSha256: reportDraft.contentSha256,
          contentMarkdown,
        },
        {
          idempotencyKey:
            `report-draft-save:${reportDraft.id}:${reportDraft.revision}:` +
            reportDraft.contentSha256.slice(0, 12),
        },
      );
      if (
        reportProjectIdRef.current !== requestProjectId ||
        reportWorkflowIdRef.current !== requestWorkflowId
      ) {
        throw new Error("The selected project changed before the report save completed.");
      }
      setReportDraft(updated);
      return updated;
    } catch (error) {
      if (
        reportProjectIdRef.current === requestProjectId &&
        reportWorkflowIdRef.current === requestWorkflowId
      ) {
        setReportDraftError(error instanceof Error ? error.message : String(error));
      }
      throw error;
    } finally {
      setReportDraftMutating(false);
    }
  };
  const reviewPersistentReportDraft = async (
    citationRebases: ReportCitationRebaseInput[],
  ): Promise<ReportDraftRecord> => {
    if (!projectId || !reportWorkflowId || !reportDraft) {
      throw new Error("The persisted report draft is unavailable.");
    }
    const requestProjectId = projectId;
    const requestWorkflowId = reportWorkflowId;
    setReportDraftMutating(true);
    setReportDraftError(null);
    try {
      const updated = await scienceCore.reviewReportDraft(
        projectId,
        reportWorkflowId,
        reportDraft.id,
        {
          expectedRevision: reportDraft.revision,
          expectedContentSha256: reportDraft.contentSha256,
          citationRebases,
        },
        {
          idempotencyKey:
            `report-draft-review:${reportDraft.id}:${reportDraft.revision}:` +
            reportDraft.contentSha256.slice(0, 12),
        },
      );
      if (
        reportProjectIdRef.current !== requestProjectId ||
        reportWorkflowIdRef.current !== requestWorkflowId
      ) {
        throw new Error("The selected project changed before report review completed.");
      }
      setReportDraft(updated);
      return updated;
    } catch (error) {
      if (
        reportProjectIdRef.current === requestProjectId &&
        reportWorkflowIdRef.current === requestWorkflowId
      ) {
        setReportDraftError(error instanceof Error ? error.message : String(error));
      }
      throw error;
    } finally {
      setReportDraftMutating(false);
    }
  };
  const exportPersistentReportDraft = async (): Promise<ReportDraftExport> => {
    if (!projectId || !reportWorkflowId || !reportDraft) {
      throw new Error("The persisted report draft is unavailable.");
    }
    const requestProjectId = projectId;
    const requestWorkflowId = reportWorkflowId;
    setReportDraftMutating(true);
    setReportDraftError(null);
    try {
      const exported = await scienceCore.exportReportDraft(
        projectId,
        reportWorkflowId,
        reportDraft.id,
        {
          expectedRevision: reportDraft.revision,
          expectedContentSha256: reportDraft.contentSha256,
        },
      );
      if (
        reportProjectIdRef.current !== requestProjectId ||
        reportWorkflowIdRef.current !== requestWorkflowId
      ) {
        throw new Error("The selected project changed before report export completed.");
      }
      return exported;
    } catch (error) {
      const stillCurrent =
        reportProjectIdRef.current === requestProjectId &&
        reportWorkflowIdRef.current === requestWorkflowId;
      if (stillCurrent) {
        setReportDraftError(error instanceof Error ? error.message : String(error));
      }
      if (
        stillCurrent &&
        error instanceof ScienceCoreApiError &&
        error.status === 409
      ) {
        void loadReportDraft();
      }
      throw error;
    } finally {
      setReportDraftMutating(false);
    }
  };
  useEffect(() => {
    setSelectedCitation((current) => {
      if (!current) return null;
      const currentKey = canonicalEvidenceKey(current);
      const updated = citations.find(
        (citation) => canonicalEvidenceKey(citation) === currentKey,
      );
      if (!reportExport.ok) return updated ?? null;
      const remainsExportable = reportExport.citations.some(
        (citation) => citation.canonicalEvidenceKey === currentKey,
      );
      return updated && remainsExportable ? updated : null;
    });
  }, [citations, reportExport]);
  const selectedPaper = papers.find((paper) => paper.id === selectedPaperId) ?? papers[0] ?? null;
  const stateLabel = workflowDisplayState(matchedStatus, workflowEvents);
  const localizedStateLabel = matchedStatus
    ? t(`research.workflowStatus.${matchedStatus}`)
    : null;

  useEffect(() => {
    const nextIds = new Set(papers.map((paper) => paper.id));
    const decisionsBySource = new Map(
      screeningDecisions.map((decision) => [decision.sourceId, decision] as const),
    );
    setSelectedPaperId((current) => nextIds.has(current) ? current : papers[0]?.id ?? "");
    setIncludedIds(() => {
      const next = new Set<string>();
      for (const paper of papers) {
        if (paper.origin === "source") {
          const decision = decisionsBySource.get(paper.id)?.decision;
          if (paper.source?.ingestionStatus === "ready" && decision === "include") {
            next.add(paper.id);
          }
        } else if (paper.included) {
          next.add(paper.id);
        }
      }
      return next;
    });
    setScreeningMutationError(null);
  }, [papers, screeningDecisions]);

  const toggleScreeningDecision = async (paperId: string) => {
    const requestProjectId = projectId;
    const paper = papers.find((candidate) => candidate.id === paperId);
    if (paper?.origin === "source" && paper.source?.ingestionStatus !== "ready") return;
    if (!paper?.source || screeningBusyIds.has(paperId) || screeningDecisionsLoading) return;
    const previousIncluded = includedIds.has(paperId);
    const existing = screeningDecisions.find((decision) => decision.sourceId === paperId);
    setScreeningMutationError(null);
    setScreeningBusyIds((current) => new Set(current).add(paperId));
    setIncludedIds((current) => {
      const next = new Set(current);
      if (previousIncluded) next.delete(paperId);
      else next.add(paperId);
      return next;
    });
    try {
      await onUpsertScreeningDecision(paperId, {
        decision: previousIncluded ? "exclude" : "include",
        reason: existing?.reason ?? null,
        criteriaVersion: existing?.criteriaVersion ?? "screening-v1",
        expectedVersion: existing?.rowVersion ?? 0,
      });
    } catch (error) {
      if (screeningProjectIdRef.current !== requestProjectId) return;
      setIncludedIds((current) => {
        const next = new Set(current);
        if (previousIncluded) next.add(paperId);
        else next.delete(paperId);
        return next;
      });
      setScreeningMutationError(error instanceof Error ? error.message : String(error));
    } finally {
      if (screeningProjectIdRef.current === requestProjectId) {
        setScreeningBusyIds((current) => {
          const next = new Set(current);
          next.delete(paperId);
          return next;
        });
      }
    }
  };

  const submitQuestion = async (event: FormEvent) => {
    event.preventDefault();
    const normalizedQuestion = question.trim();
    if (!normalizedQuestion) return;
    if (realSourceMode) {
      setSurface("papers");
      return;
    }
    if (onStartDiscovery) {
      await onStartDiscovery(normalizedQuestion, discoveryProvider);
      return;
    }
  };

  const findMorePapers = async () => {
    const normalizedQuestion = question.trim();
    if (
      !onStartDiscovery
      || !normalizedQuestion
      || normalizedQuestion.length > 500
    ) return;
    await onStartDiscovery(normalizedQuestion, discoveryProvider);
  };

  const discoveryEnabled = Boolean(onStartDiscovery);
  const discoveryWorkflowId = discoverySnapshot?.workflowId ?? null;
  const openedDiscoveryWorkflowIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!discoveryEnabled || !discoveryOnly) return;
    if (!discoveryWorkflowId) {
      if (openedDiscoveryWorkflowIdRef.current !== null) return;
      openedDiscoveryWorkflowIdRef.current = "pending";
      setSurface("papers");
      return;
    }
    if (openedDiscoveryWorkflowIdRef.current === discoveryWorkflowId) return;
    openedDiscoveryWorkflowIdRef.current = discoveryWorkflowId;
    setSurface("papers");
  }, [discoveryEnabled, discoveryOnly, discoveryWorkflowId]);

  useEffect(() => {
    if (!selectedPaper && surface === "reader") setSurface("home");
  }, [selectedPaper, surface]);

  const synthesisUnavailableReason = !question.trim()
    ? t("research.literature.openWorkflowUnavailable.question")
    : !serviceReady
      ? t("research.literature.openWorkflowUnavailable.service")
      : !onStartSynthesis
        ? t("research.literature.openWorkflowUnavailable.execution")
        : screeningDecisionsLoading
          ? t("research.literature.openWorkflowUnavailable.screeningLoading")
          : screeningDecisionsError
            ? t("research.literature.openWorkflowUnavailable.screeningError")
            : screeningMutationPending || screeningBusyIds.size > 0
              ? t("research.literature.openWorkflowUnavailable.screeningSaving")
              : canonicalSourceIds.length === 0
                ? t("research.literature.openWorkflowUnavailable.noSources")
                : null;
  const startSynthesis = async () => {
    if (
      synthesisUnavailableReason ||
      !realSourceMode ||
      !onStartSynthesis ||
      !literatureIdentity ||
      canonicalSourceIds.length === 0
    ) return;
    // Starting or resuming synthesis lands on Answer, which shows its own calm
    // generating state while the matched workflow runs. Report stays separate.
    // eslint-disable-next-line i18next/no-literal-string -- internal surface discriminant
    setSurface("answer");
    await onStartSynthesis(literatureIdentity.goal, literatureIdentity.sourceIds);
  };
  const openAnswerCitation = (citation: ReportCitation) => {
    setSelectedPaperId(citation.sourceId);
    setReaderPageIndex(citation.pageIndex);
    setReaderEvidence(citation);
    // eslint-disable-next-line i18next/no-literal-string -- internal surface discriminant
    setSurface("reader");
  };
  const openAnswerSource = (sourceId: string) => {
    setSelectedPaperId(sourceId);
    setReaderPageIndex(0);
    setReaderEvidence(null);
    // eslint-disable-next-line i18next/no-literal-string -- internal surface discriminant
    setSurface("reader");
  };
  const screeningExportState = screeningDecisionsLoading
    ? "loading"
    : screeningDecisionsError
      ? "error"
      : screeningMutationPending || screeningBusyIds.size > 0
        ? "saving"
        : "ready";

  return (
    <section
      className="research-repro flex h-full min-h-0 flex-col bg-surface"
      data-testid="competitive-research-workspace"
      data-surface={surface}
    >
      <header className="research-repro-header flex min-h-12 shrink-0 items-center border-b border-border bg-surface px-4">
        <button
          type="button"
          onClick={() => setSurface("home")}
          className="touch-target min-w-0 text-left"
          aria-label={t("research.literature.home")}
        >
          <span className="block truncate text-ui font-semibold text-text">
            {projectTitle || t("research.literature.home")}
          </span>
          <span className="block text-caption text-muted">
            {serviceReady
              ? t("research.literature.serviceConnected")
              : t("research.health.offline")}
          </span>
        </button>
        {surface !== "home" && (
          <nav className="ml-auto flex h-12 items-end overflow-x-auto" aria-label={t("research.literature.workflowNav")}>
            {SURFACES.filter((item) =>
              !discoveryOnly || item.id === "papers",
            ).map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setSurface(item.id)}
                aria-current={surface === item.id ? "page" : undefined}
                className={cn(
                  "touch-target min-h-11 shrink-0 border-b-2 px-3 text-xs font-medium",
                  surface === item.id
                    ? "border-accent text-text"
                    : "border-transparent text-muted hover:text-text",
                )}
              >
                {t(`research.literature.surfaces.${item.id}`, { defaultValue: item.label })}
              </button>
            ))}
          </nav>
        )}
        <div className="ml-3 flex shrink-0 items-center gap-2">
          {/* eslint-disable-next-line i18next/no-literal-string -- canonical state badge tone discriminants */}
          {stateLabel && localizedStateLabel && <StateBadge label={localizedStateLabel} tone={matchedStatus === "failed" || matchedStatus === "blocked" ? "error" : "neutral"} />}
          <button type="button" onClick={onOpenDataset} disabled={!serviceReady || !projectReady} title={!serviceReady ? serviceUnavailableReason ?? undefined : !projectReady ? t("research.literature.createProjectRequired") : undefined} className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-50">
            {t("research.literature.dataAnalysis")}
          </button>
          <button type="button" onClick={onOpenWorkflow} disabled={!serviceReady || !projectReady} title={!serviceReady ? serviceUnavailableReason ?? undefined : !projectReady ? t("research.literature.createProjectRequired") : undefined} className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-50">
            {t("research.literature.workflowDetails")}
          </button>
        </div>
      </header>

      {serviceUnavailableReason && (
        <div
          role="alert"
          className={cn(
            "flex min-h-10 shrink-0 items-center gap-2 border-b px-4 text-caption text-muted",
            serviceReady
              ? "border-warn/25 bg-warn/5"
              : "border-error/25 bg-error/5",
          )}
        >
          <CircleAlert
            size={13}
            className={cn("shrink-0", serviceReady ? "text-warn" : "text-error")}
            aria-hidden={true}
          />
          <span className="font-medium text-text">{t("research.offlineTitle")}</span>
          <span className="min-w-0 truncate" title={serviceUnavailableReason}>{serviceUnavailableReason}</span>
        </div>
      )}

      {surface !== "home" && (
        <div className="flex min-h-8 shrink-0 items-center gap-2 border-b border-border-faint bg-surface-2 px-4 text-caption text-muted">
          <CircleAlert size={12} />
          <span className="truncate">
            {realSourceMode
              ? t("research.literature.importedBoundary")
              : discoveryOnly
                ? t("research.literature.discovery.workspaceBoundary")
              : t("research.literature.emptyBoundaryDetail")}
          </span>
          {realPdfSources.length > 0 && (
            <span className="ml-auto shrink-0 text-ok">
              {t("research.literature.localPdfCount", { count: realPdfSources.length })}
            </span>
          )}
        </div>
      )}

      <div className="min-h-0 flex-1">
        {surface === "home" && (
          <HomeSurface
            question={question}
            onQuestionChange={editQuestion}
            onSubmit={(event) => void submitQuestion(event)}
            onImportPdfRequest={onImportPdfRequest}
            onImportCslJsonRequest={onImportCslJsonRequest}
            cslJsonImporting={cslJsonImporting}
            onOpenDataset={onOpenDataset}
            serviceReady={serviceReady}
            projectReady={projectReady}
            serviceUnavailableReason={serviceUnavailableReason}
            projectListLoading={projectListLoading}
            projectListResolved={projectListResolved}
            projectListError={projectListError}
            onRetryProjectList={onRetryProjectList}
            creatingProject={creatingProject}
            createProjectError={createProjectError}
            onCreateProject={onCreateProject}
            onStartFirstDiscovery={onStartFirstDiscovery}
            onOpenRuntimeHelp={onOpenRuntimeHelp}
            runtimeHelpLabel={runtimeHelpLabel}
            onDraftReport={realSourceMode ? () => void startSynthesis() : undefined}
            onFindMorePapers={
              realSourceMode && onStartDiscovery
                ? () => void findMorePapers()
                : undefined
            }
            draftReportUnavailableReason={synthesisUnavailableReason}
            realSourceCount={realPdfSources.length}
            discoveryEnabled={discoveryEnabled}
            discoveryProvider={discoveryProvider}
            onDiscoveryProviderChange={setDiscoveryProvider}
            discoveryCreating={discoveryCreating}
            discoveryCreateError={discoveryCreateError}
            memoryController={memoryController}
          />
        )}
        {surface === "answer" && (
          <AnswerSurface
            question={question}
            matchedWorkflow={matchedWorkflow}
            status={matchedStatus}
            statusReason={matchedStatusReason}
            result={matchedResult}
            reviewPassed={reviewPassed}
            legacyAnswer={legacyAnswer}
            citations={legacyAnswer ? legacyCitations : answerCitations}
            citationSources={realPdfSources}
            papers={papers}
            canonicalSourceIds={canonicalSourceIdSet}
            realSourceMode={realSourceMode}
            serviceReady={serviceReady}
            serviceUnavailableReason={serviceUnavailableReason}
            synthesisUnavailableReason={synthesisUnavailableReason}
            evidenceDirections={evidenceDirections}
            evidenceDirectionsLoading={evidenceDirectionsLoading}
            evidenceDirectionsError={evidenceDirectionsError}
            evidenceDirectionMutationPending={evidenceDirectionMutationPending}
            onUpsertEvidenceDirection={onUpsertEvidenceDirection}
            onStartSynthesis={onStartSynthesis ? () => void startSynthesis() : undefined}
            onOpenCitation={openAnswerCitation}
            onOpenSource={openAnswerSource}
            onOpenReport={() => {
              // eslint-disable-next-line i18next/no-literal-string -- internal surface discriminant
              setSurface("report");
            }}
            onImportPdf={onImportPdfRequest}
          />
        )}
        {surface === "papers" && (
          <PapersSurface
            question={question}
            papers={papers}
            realSourceMode={realSourceMode}
            selectedPaperId={selectedPaperId}
            includedIds={includedIds}
            filtersOpen={filtersOpen}
            onToggleFilters={() => setFiltersOpen((open) => !open)}
            onCloseFilters={() => setFiltersOpen(false)}
            onSelectPaper={setSelectedPaperId}
            discoverySnapshot={discoverySnapshot}
            discoveryLoading={discoveryLoading}
            discoveryError={discoveryError}
            candidateTriageDecisions={candidateTriageDecisions}
            candidateTriageLoading={candidateTriageLoading}
            candidateTriageError={candidateTriageError}
            candidateTriageMutationPending={candidateTriageMutationPending}
            onUpsertCandidateTriageDecision={onUpsertCandidateTriageDecision}
            discoveryOnly={discoveryOnly}
            onAttachCandidate={onAttachCandidatePdfRequest}
            onImportCslJson={onImportCslJsonRequest}
            cslJsonImporting={cslJsonImporting}
            onOpenAttachedSource={(sourceId) => {
              setSelectedPaperId(sourceId);
              setReaderPageIndex(0);
              setReaderEvidence(null);
              // eslint-disable-next-line i18next/no-literal-string -- internal surface discriminant
              setSurface("reader");
            }}
            // eslint-disable-next-line i18next/no-literal-string -- internal surface discriminant
            onContinue={() => setSurface("screening")}
          />
        )}
        {surface === "screening" && (
          <ScreeningSurface
            papers={papers}
            realSourceMode={realSourceMode}
            selectedPaperId={selectedPaperId}
            includedIds={includedIds}
            screeningDecisions={screeningDecisions}
            busyIds={screeningBusyIds}
            loading={screeningDecisionsLoading}
            persistenceError={screeningMutationError ?? screeningDecisionsError}
            discoveryOnly={discoveryOnly}
            onSelectPaper={setSelectedPaperId}
            onTogglePaper={(paperId) => void toggleScreeningDecision(paperId)}
            // eslint-disable-next-line i18next/no-literal-string -- internal surface discriminant
            onOpenReading={() => setSurface("reader")}
            // eslint-disable-next-line i18next/no-literal-string -- internal surface discriminant
            onContinue={() => setSurface("extraction")}
          />
        )}
        {surface === "extraction" && (
          <ExtractionSurface
            projectId={projectId}
            projectIdRef={extractionProjectIdRef}
            generation={extractionGeneration}
            papers={papers.filter((paper) =>
              includedIds.has(paper.id)
              && paper.source?.ingestionStatus === "ready"
              && canonicalSourceIdSet.has(paper.id)
            )}
            realSourceMode={realSourceMode}
            screeningExportState={screeningExportState}
            selectedPaperId={selectedPaperId}
            onSelectPaper={setSelectedPaperId}
            extractionMatrix={extractionMatrix}
            loading={extractionLoading}
            persistenceError={extractionError}
            evidenceCoverage={evidenceCoverage}
            evidenceCoverageLoading={evidenceCoverageLoading}
            evidenceCoverageError={evidenceCoverageError}
            onRetryEvidenceCoverage={onRetryEvidenceCoverage}
            onCreateColumn={onCreateExtractionColumn}
            onUpsertCell={onUpsertExtractionCell}
            onDeleteCell={onDeleteExtractionCell}
            onCreateCitedBrief={async () => {
              if (!onCreateCitedBrief) return;
              await onCreateCitedBrief();
              // eslint-disable-next-line i18next/no-literal-string -- internal report mode discriminant
              setReportMode("review");
              // eslint-disable-next-line i18next/no-literal-string -- internal surface discriminant
              setSurface("report");
            }}
            // eslint-disable-next-line i18next/no-literal-string -- internal surface discriminant
            onContinue={() => setSurface("reader")}
          />
        )}
        {surface === "reader" && selectedPaper && (
          <ReaderSurface
            paper={selectedPaper}
            sources={papers}
            selectedPaperId={selectedPaperId}
            pageIndex={readerPageIndex}
            evidence={readerEvidence}
            includedInSynthesis={canonicalSourceIdSet.has(selectedPaper.id)}
            synthesisUnavailableReason={synthesisUnavailableReason}
            realSourceMode={realSourceMode}
            extractionMatrix={extractionMatrix}
            onCreateEvidenceSpan={onCreateExactEvidenceSpan}
            onUpsertExtractionCell={onUpsertExtractionCell}
            onEvidenceSaved={setReaderEvidence}
            memoryController={memoryController}
            onPageChange={(nextPageIndex) => {
              setReaderPageIndex(nextPageIndex);
              setReaderEvidence((current) =>
                current?.pageIndex === nextPageIndex ? current : null
              );
            }}
            onSelectSource={(id) => {
              setSelectedPaperId(id);
              setReaderPageIndex(0);
              setReaderEvidence(null);
            }}
            onContinue={() => {
              if (realSourceMode) {
                if (canonicalSourceIdSet.has(selectedPaper.id)) {
                  void startSynthesis();
                } else {
                  // eslint-disable-next-line i18next/no-literal-string -- internal surface discriminant
                  setSurface("screening");
                }
              } else {
                // eslint-disable-next-line i18next/no-literal-string -- internal report mode discriminant
                setReportMode("review");
                // eslint-disable-next-line i18next/no-literal-string -- internal surface discriminant
                setSurface("report");
              }
            }}
          />
        )}
        {surface === "report" && (
          <ReportSurface
            mode={reportMode}
            result={displayResult}
            citations={finalResult ? verifiedCitations : citations}
            reportExport={reportExport}
            persistentReportDraft={Boolean(reportWorkflowId)}
            draft={reportDraft}
            draftLoading={reportDraftLoading}
            draftMutating={reportDraftMutating}
            draftError={reportDraftError}
            sourceChangedCitationCount={sourceChangedCitations.length}
            needsReviewCitationCount={needsReviewCitations.length}
            selectedCitation={selectedCitation}
            realSourceMode={realSourceMode}
            evidenceCoverage={evidenceCoverage}
            evidenceCoverageLoading={evidenceCoverageLoading}
            evidenceCoverageError={evidenceCoverageError}
            onRetryEvidenceCoverage={onRetryEvidenceCoverage}
            onRetryDraft={() => void loadReportDraft()}
            onSaveDraft={savePersistentReportDraft}
            onReviewDraft={reviewPersistentReportDraft}
            onExportDraft={exportPersistentReportDraft}
            onCitationOpen={setSelectedCitation}
            onCitationClose={() => setSelectedCitation(null)}
            onModeChange={setReportMode}
            onOpenPdf={(citation) => {
              setSelectedPaperId(citation.sourceId);
              setReaderPageIndex(citation.pageIndex);
              setReaderEvidence(citation);
              setSelectedCitation(null);
              // eslint-disable-next-line i18next/no-literal-string -- internal surface discriminant
              setSurface("reader");
            }}
          />
        )}
      </div>
    </section>
  );
}

interface AnswerSurfaceProps {
  question: string;
  matchedWorkflow: boolean;
  status: ResearchWorkflowStatus | null;
  statusReason: string | null;
  result: ResearchWorkflowResult | null;
  reviewPassed: boolean;
  legacyAnswer: ResearchAnswer | null;
  citations: ReportCitation[];
  citationSources: readonly ResearchSource[];
  papers: ResearchPaper[];
  canonicalSourceIds: ReadonlySet<string>;
  realSourceMode: boolean;
  serviceReady: boolean;
  serviceUnavailableReason: string | null;
  synthesisUnavailableReason: string | null;
  evidenceDirections: EvidenceDirectionJudgment[];
  evidenceDirectionsLoading: boolean;
  evidenceDirectionsError: string | null;
  evidenceDirectionMutationPending: boolean;
  onUpsertEvidenceDirection?: (
    sourceId: string,
    input: UpsertEvidenceDirectionJudgmentInput,
  ) => Promise<EvidenceDirectionJudgment>;
  onStartSynthesis?: () => void;
  onOpenCitation: (citation: ReportCitation) => void;
  onOpenSource: (sourceId: string) => void;
  onOpenReport: () => void;
  onImportPdf: () => void;
}

/**
 * Consensus-style research answer. The identity-matched autonomous workflow
 * result is the only synthesis truth; a same-project legacy answer is a
 * clearly marked fallback. Evidence direction is counted only from persisted
 * human judgments for the current answer and source.
 */
function AnswerSurface({
  question,
  matchedWorkflow,
  status,
  statusReason,
  result,
  reviewPassed,
  legacyAnswer,
  citations,
  citationSources,
  papers,
  canonicalSourceIds,
  realSourceMode,
  serviceReady,
  serviceUnavailableReason,
  synthesisUnavailableReason,
  evidenceDirections,
  evidenceDirectionsLoading,
  evidenceDirectionsError,
  evidenceDirectionMutationPending,
  onUpsertEvidenceDirection,
  onStartSynthesis,
  onOpenCitation,
  onOpenSource,
  onOpenReport,
  onImportPdf,
}: AnswerSurfaceProps) {
  const { t } = useTranslation("pages");
  const [railOpen, setRailOpen] = useState(true);
  const [scopeOnly, setScopeOnly] = useState(true);
  const [pdfReadyOnly, setPdfReadyOnly] = useState(false);
  const [yearFrom, setYearFrom] = useState("");
  const [yearTo, setYearTo] = useState("");
  const [directionFilter, setDirectionFilter] = useState<
    EvidenceDirectionValue | "all" | "unconfirmed"
  >("all");
  const filtersTriggerRef = useRef<HTMLButtonElement>(null);
  const railCloseRef = useRef<HTMLButtonElement>(null);

  const generating = matchedWorkflow
    && status !== null
    && status !== "completed"
    && status !== "failed"
    && status !== "blocked"
    && status !== "cancelled";
  const failed = matchedWorkflow
    && (status === "failed" || status === "blocked" || status === "cancelled");
  const completedWithoutResult = matchedWorkflow && status === "completed" && !result;
  const frozenResult = Boolean(
    result && reviewPassed && result.integrityStatus === "verified-frozen-v2",
  );
  const showResult = matchedWorkflow && status === "completed" && result;
  const showLegacy = !matchedWorkflow && legacyAnswer;
  const showContent = Boolean(showResult || showLegacy);

  const displayedQuestion =
    (showResult ? question.trim() : legacyAnswer?.question ?? question.trim())
    || t("research.literature.answer.untitledQuestion");
  const summaryText = showResult && result ? result.summary : legacyAnswer?.answer ?? null;
  const displayClaims = useMemo(() => {
    const claims = matchedWorkflow && status === "completed" && result
      ? result.claims
      : legacyAnswer?.claims ?? [];
    return claims.map((claim) => {
      const evidenceIds = new Set(
        claim.evidence.map((evidence) =>
          "evidenceId" in evidence ? evidence.evidenceId : evidence.id,
        ),
      );
      return {
        statement: claim.statement,
        supportStatus: "supportStatus" in claim ? claim.supportStatus : null,
        citations: citations.filter((citation) => evidenceIds.has(citation.id)),
      };
    });
  }, [citations, legacyAnswer, matchedWorkflow, result, status]);
  const uniqueCitations = useMemo(
    () =>
      citations.filter(
        (citation, index, all) =>
          all.findIndex(
            (candidate) =>
              canonicalEvidenceKey(candidate) === canonicalEvidenceKey(citation),
          ) === index,
      ),
    [citations],
  );
  const citationIndex = (citation: ReportCitation) =>
    uniqueCitations.findIndex(
      (candidate) =>
        canonicalEvidenceKey(candidate) === canonicalEvidenceKey(citation),
    ) + 1;
  const passagesBySource = useMemo(() => {
    const grouped = new Map<string, ReportCitation[]>();
    for (const citation of citations) {
      const existing = grouped.get(citation.sourceId) ?? [];
      existing.push(citation);
      grouped.set(citation.sourceId, existing);
    }
    return grouped;
  }, [citations]);
  const directionsBySource = useMemo(
    () => new Map(evidenceDirections.map((judgment) => [judgment.sourceId, judgment])),
    [evidenceDirections],
  );
  const directionCounts = useMemo(() => {
    const scopedSourceIds = new Set(
      papers
        .filter((paper) => canonicalSourceIds.has(paper.id))
        .map((paper) => paper.id),
    );
    const counts: Record<EvidenceDirectionValue, number> = {
      supporting: 0,
      mixed: 0,
      insufficient: 0,
    };
    for (const judgment of evidenceDirections) {
      if (scopedSourceIds.has(judgment.sourceId)) counts[judgment.direction] += 1;
    }
    const confirmed = Object.values(counts).reduce((total, count) => total + count, 0);
    return {
      ...counts,
      confirmed,
      unconfirmed: Math.max(0, scopedSourceIds.size - confirmed),
      total: scopedSourceIds.size,
    };
  }, [canonicalSourceIds, evidenceDirections, papers]);

  const yearBounds = useMemo(() => {
    const years = papers
      .map((paper) => Number(paper.source?.publicationDate?.slice(0, 4)))
      .filter((year) => Number.isFinite(year) && year > 0);
    return years.length > 0
      ? { min: Math.min(...years), max: Math.max(...years) }
      : null;
  }, [papers]);
  const yearFromBound = yearFrom.length === 4 ? Number(yearFrom) : null;
  const yearToBound = yearTo.length === 4 ? Number(yearTo) : null;
  const visiblePapers = useMemo(
    () =>
      papers.filter((paper) => {
        if (scopeOnly && !canonicalSourceIds.has(paper.id)) return false;
        if (pdfReadyOnly && paper.source?.ingestionStatus !== "ready") return false;
        const direction = directionsBySource.get(paper.id)?.direction;
        if (directionFilter === "unconfirmed" && direction) return false;
        if (
          directionFilter !== "all"
          && directionFilter !== "unconfirmed"
          && direction !== directionFilter
        ) return false;
        const year = Number(paper.source?.publicationDate?.slice(0, 4));
        const hasYear = Number.isFinite(year) && year > 0;
        if ((yearFromBound !== null || yearToBound !== null) && !hasYear) return false;
        if (yearFromBound !== null && hasYear && year < yearFromBound) return false;
        if (yearToBound !== null && hasYear && year > yearToBound) return false;
        return true;
      }),
    [
      canonicalSourceIds,
      directionFilter,
      directionsBySource,
      papers,
      pdfReadyOnly,
      scopeOnly,
      yearFromBound,
      yearToBound,
    ],
  );
  const directionEditingEnabled = Boolean(
    showResult && serviceReady && onUpsertEvidenceDirection && !evidenceDirectionsLoading,
  );

  const closeRail = () => {
    setRailOpen(false);
    filtersTriggerRef.current?.focus();
  };
  const toggleRail = () => {
    const next = !railOpen;
    setRailOpen(next);
    if (next) requestAnimationFrame(() => railCloseRef.current?.focus());
  };
  const startAction = onStartSynthesis ? (
    <button
      type="button"
      onClick={onStartSynthesis}
      disabled={Boolean(synthesisUnavailableReason)}
      title={synthesisUnavailableReason ?? undefined}
      className="compact-button primary-button disabled:cursor-not-allowed disabled:opacity-50"
    >
      {failed
        ? t("research.literature.answer.resume")
        : t("research.literature.answer.start")}
    </button>
  ) : null;
  const sourcesSection = papers.length > 0 && (
    <section aria-label={t("research.literature.answer.sourcesAria")}>
      <h2 className="answer-section-heading">
        {t("research.literature.answer.sourcesHeading", { count: visiblePapers.length })}
      </h2>
      {visiblePapers.length === 0 ? (
        <p role="status" className="answer-state-note">
          {t("research.literature.answer.noSourcesMatch")}
        </p>
      ) : (
        <ol className="answer-source-list">
          {visiblePapers.map((paper) => {
            const passages = passagesBySource.get(paper.id) ?? [];
            const inScope = canonicalSourceIds.has(paper.id);
            const judgment = directionsBySource.get(paper.id);
            return (
              <li key={paper.id} className="answer-source-row">
                <div className="answer-source-head">
                  <div className="min-w-0 flex-1">
                    <strong className="answer-source-title">{paper.title}</strong>
                    <p className="answer-source-meta">
                      {paper.authors} · {paper.year}
                      {paper.pageCount
                        ? ` · ${t("research.pageCount", { count: paper.pageCount })}`
                        : ""}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => onOpenSource(paper.id)}
                    className="compact-button secondary-button"
                  >
                    {t("research.literature.answer.openSource")}
                  </button>
                </div>
                <div className="answer-source-badges">
                  <StateBadge
                    label={paper.ingestionLabel}
                    tone={
                      paper.source?.ingestionStatus === "failed"
                        ? STATUS_TONES.error
                        : paper.source?.ingestionStatus === "ready"
                          ? STATUS_TONES.ok
                          : STATUS_TONES.neutral
                    }
                  />
                  <StateBadge
                    label={
                      inScope
                        ? t("research.literature.answer.inScope")
                        : t("research.literature.answer.outOfScope")
                    }
                    tone={inScope ? STATUS_TONES.ok : STATUS_TONES.neutral}
                  />
                  {showResult && (
                    <StateBadge
                      label={
                        judgment
                          ? t(`research.literature.answer.direction.${judgment.direction}`)
                          : t("research.literature.answer.direction.pending")
                      }
                      tone={judgment ? STATUS_TONES.ok : STATUS_TONES.neutral}
                    />
                  )}
                </div>
                {showResult && inScope && (
                  <div
                    className="answer-source-direction"
                    role="group"
                    aria-label={t("research.literature.answer.direction.sourceAria", {
                      title: paper.title,
                    })}
                  >
                    <span>{t("research.literature.answer.direction.sourcePrompt")}</span>
                    <div className="answer-direction-controls">
                      {ANSWER_DIRECTIONS.map((direction) => (
                        <button
                          key={direction}
                          type="button"
                          disabled={!directionEditingEnabled || evidenceDirectionMutationPending}
                          aria-pressed={judgment?.direction === direction}
                          onClick={() => {
                            if (!onUpsertEvidenceDirection) return;
                            void onUpsertEvidenceDirection(paper.id, {
                              direction,
                              expectedVersion: judgment?.rowVersion ?? 0,
                            }).catch(() => undefined);
                          }}
                          className={cn(
                            "compact-button",
                            judgment?.direction === direction
                              ? "primary-button"
                              : "secondary-button",
                            "disabled:cursor-not-allowed disabled:opacity-50",
                          )}
                        >
                          {t(`research.literature.answer.direction.${direction}`)}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {passages.length > 0 && (
                  <div className="answer-passages">
                    <p className="answer-passages-count">
                      {t("research.literature.answer.recordedPassages", {
                        count: passages.length,
                      })}
                    </p>
                    {passages.map((citation) => {
                      const integrity = citationIntegrity(citation, citationSources);
                      return (
                        <button
                          key={canonicalEvidenceKey(citation)}
                          type="button"
                          onClick={() => onOpenCitation(citation)}
                          aria-label={t("research.literature.answer.openPassage", {
                            page: citation.pageLabel ?? citation.pageIndex + 1,
                          })}
                          className="answer-passage"
                        >
                          <span className="answer-passage-meta">
                            {t("research.literature.report.pageReference", {
                              page: citation.pageLabel ?? citation.pageIndex + 1,
                            })}
                            {` · ${citation.extractionMethod} · `}
                            <StateBadge
                              label={
                                integrity === "verified"
                                  ? t("research.literature.citation.verifiedPassage")
                                  : integrity === "source-changed"
                                    ? t("research.literature.report.sourceChanged")
                                    : t("research.literature.report.needsReview")
                              }
                              tone={
                                integrity === "verified"
                                  ? STATUS_TONES.ok
                                  : integrity === "source-changed"
                                    ? STATUS_TONES.error
                                    : STATUS_TONES.warn
                              }
                            />
                          </span>
                          <span className="answer-passage-text">{citation.text}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );

  return (
    <div className={cn("answer-layout h-full min-h-0", !railOpen && "answer-layout-no-rail")}>
      <div className="answer-main">
        <div className="answer-main-inner">
          <header className="answer-header">
            <div className="min-w-0 flex-1">
              <h1 className="answer-question">{displayedQuestion}</h1>
              <div className="answer-meta">
                {status && (
                  <StateBadge
                    label={t(`research.workflowStatus.${status}`)}
                    tone={
                      status === "failed" || status === "blocked"
                        ? STATUS_TONES.error
                        : STATUS_TONES.neutral
                    }
                  />
                )}
                {showResult && (
                  <StateBadge
                    label={
                      frozenResult
                        ? t("research.literature.answer.frozenBadge")
                        : t("research.literature.answer.unfrozenBadge")
                    }
                    tone={frozenResult ? STATUS_TONES.ok : STATUS_TONES.warn}
                  />
                )}
                {showLegacy && (
                  <StateBadge
                    label={t("research.literature.answer.legacyBadge")}
                    tone={STATUS_TONES.warn}
                  />
                )}
              </div>
            </div>
            <div className="answer-actions">
              {papers.length > 0 && (
                <button
                  ref={filtersTriggerRef}
                  type="button"
                  onClick={toggleRail}
                  aria-expanded={railOpen}
                  className="compact-button secondary-button"
                >
                  <Filter size={13} /> {t("research.literature.filters")}
                </button>
              )}
              {realSourceMode && (
                <button
                  type="button"
                  onClick={onOpenReport}
                  className="compact-button secondary-button"
                >
                  {t("research.literature.answer.openReport")}
                </button>
              )}
            </div>
          </header>

          {!realSourceMode && !showLegacy && (
            <section className="answer-state" role="status">
              <h2>{t("research.literature.answer.emptyTitle")}</h2>
              <p>{t("research.literature.emptyLibrary")}</p>
              {!serviceReady && (
                <p className="answer-state-note">
                  {serviceUnavailableReason ?? t("research.offlineTitle")}
                </p>
              )}
              <div>
                <button
                  type="button"
                  onClick={onImportPdf}
                  className="compact-button primary-button"
                >
                  {t("research.importPdf")}
                </button>
              </div>
            </section>
          )}
          {realSourceMode && !matchedWorkflow && !showLegacy && (
            <section className="answer-state" role="status">
              <h2>{t("research.literature.answer.noWorkflowTitle")}</h2>
              <p>{t("research.literature.answer.noWorkflowBody")}</p>
              {!serviceReady && (
                <p className="answer-state-note">
                  {serviceUnavailableReason ?? t("research.offlineTitle")}
                </p>
              )}
              {startAction && <div>{startAction}</div>}
            </section>
          )}
          {generating && (
            <section
              className="answer-state"
              role="status"
              aria-busy="true"
              aria-label={t("research.literature.answer.generatingAria")}
            >
              <h2>{t("research.literature.answer.generatingTitle")}</h2>
              <p>
                {t("research.literature.answer.generatingBody", {
                  status: status ? t(`research.workflowStatus.${status}`) : "",
                })}
              </p>
              <div className="answer-skeleton" aria-hidden="true">
                <div className="h-4 w-2/3 animate-pulse rounded-input bg-surface-2" />
                <div className="h-4 w-full animate-pulse rounded-input bg-surface-2" />
                <div className="h-4 w-5/6 animate-pulse rounded-input bg-surface-2" />
              </div>
            </section>
          )}
          {failed && (
            <section className="answer-state" role="alert">
              <h2>{t("research.literature.answer.failedTitle")}</h2>
              <p>{statusReason ?? t("research.literature.answer.failedNoReason")}</p>
              {startAction && <div>{startAction}</div>}
            </section>
          )}
          {completedWithoutResult && (
            <section className="answer-state" role="status">
              <h2>{t("research.literature.answer.completedNoResultTitle")}</h2>
              <p>{t("research.literature.answer.completedNoResultBody")}</p>
            </section>
          )}

          {showContent && (
            <>
              {showLegacy && (
                <p role="status" className="answer-note">
                  {t("research.literature.answer.legacyNote")}
                </p>
              )}
              {showResult && !frozenResult && (
                <p role="status" className="answer-note">
                  {t("research.literature.answer.unfrozenNote")}
                </p>
              )}
              {summaryText && (
                <section aria-label={t("research.literature.report.summary")}>
                  <h2 className="answer-section-heading">
                    {t("research.literature.report.summary")}
                  </h2>
                  <p className="answer-prose">{summaryText}</p>
                </section>
              )}
              {displayClaims.length > 0 && (
                <section aria-label={t("research.literature.report.findings")}>
                  <h2 className="answer-section-heading">
                    {t("research.literature.report.findings")}
                  </h2>
                  <div className="answer-claims">
                    {displayClaims.map((claim) => (
                      <article key={claim.statement} className="answer-claim">
                        <p className="answer-claim-text">
                          {claim.statement}{" "}
                          {claim.citations.map((citation, occurrence) => {
                            const index = citationIndex(citation);
                            return (
                              <button
                                key={`${canonicalEvidenceKey(citation)}-${occurrence}`}
                                type="button"
                                onClick={() => onOpenCitation(citation)}
                                aria-label={t("research.literature.answer.openCitation", { index })}
                                className="citation-link"
                              >
                                [{index}]
                              </button>
                            );
                          })}
                        </p>
                        <div className="answer-claim-meta">
                          {claim.supportStatus && (
                            <StateBadge
                              label={t(`research.literature.answer.claimStatus.${claim.supportStatus}`)}
                              tone={STATUS_TONES.neutral}
                            />
                          )}
                        </div>
                      </article>
                    ))}
                  </div>
                </section>
              )}
              <section
                className="answer-direction"
                aria-label={t("research.literature.answer.direction.heading")}
              >
                <div className="answer-direction-head">
                  <h2 className="answer-section-heading">
                    {t("research.literature.answer.direction.heading")}
                  </h2>
                  <StateBadge
                    label={t("research.literature.answer.direction.confirmedCount", {
                      confirmed: directionCounts.confirmed,
                      total: directionCounts.total,
                    })}
                    tone={
                      directionCounts.confirmed === directionCounts.total
                        ? STATUS_TONES.ok
                        : STATUS_TONES.neutral
                    }
                  />
                </div>
                <p className="answer-direction-copy">
                  {evidenceDirectionsLoading
                    ? t("research.literature.answer.direction.loading")
                    : t("research.literature.answer.direction.boundary")}
                </p>
                {evidenceDirectionsError && (
                  <p role="alert" className="answer-state-note">
                    {t("research.literature.answer.direction.error", {
                      error: evidenceDirectionsError,
                    })}
                  </p>
                )}
                <div
                  className="answer-direction-controls"
                  role="group"
                  aria-label={t("research.literature.answer.direction.heading")}
                >
                  {ANSWER_DIRECTIONS.map((direction) => (
                    <span
                      key={direction}
                      className="answer-direction-count"
                    >
                      {t(`research.literature.answer.direction.${direction}`)}
                      <strong>{directionCounts[direction]}</strong>
                    </span>
                  ))}
                  <span className="answer-direction-count">
                    {t("research.literature.answer.direction.pending")}
                    <strong>{directionCounts.unconfirmed}</strong>
                  </span>
                </div>
              </section>
            </>
          )}
          {sourcesSection}
        </div>
      </div>
      {railOpen && papers.length > 0 && (
        <aside
          className="answer-filter-rail"
          aria-label={t("research.literature.filters")}
          onKeyDown={(event) => {
            if (event.key === "Escape") closeRail();
          }}
        >
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <strong>{t("research.literature.filters")}</strong>
            <button
              ref={railCloseRef}
              type="button"
              onClick={closeRail}
              className="icon-button"
              aria-label={t("research.literature.closeFilters")}
            >
              <X size={14} />
            </button>
          </div>
          <FilterSection label={t("research.literature.answer.scopeFilter")}>
            <label className="filter-check">
              <input
                type="checkbox"
                checked={scopeOnly}
                onChange={(event) => setScopeOnly(event.target.checked)}
              />
              {t("research.literature.answer.scopeOnly")}
            </label>
          </FilterSection>
          <FilterSection label={t("research.literature.answer.pdfFilter")}>
            <label className="filter-check">
              <input
                type="checkbox"
                checked={pdfReadyOnly}
                onChange={(event) => setPdfReadyOnly(event.target.checked)}
              />
              {t("research.literature.answer.pdfReadyOnly")}
            </label>
          </FilterSection>
          {showResult && (
            <FilterSection label={t("research.literature.answer.direction.heading")}>
              <div
                className="answer-direction-filter"
                role="radiogroup"
                aria-label={t("research.literature.answer.direction.filterAria")}
              >
                {ANSWER_DIRECTION_FILTERS.map((direction) => (
                  <label key={direction} className="filter-check">
                    <input
                      type="radio"
                      name="answer-direction-filter"
                      value={direction}
                      checked={directionFilter === direction}
                      onChange={() => setDirectionFilter(direction)}
                    />
                    {direction === "all"
                      ? t("research.literature.answer.direction.all")
                      : direction === "unconfirmed"
                        ? t("research.literature.answer.direction.pending")
                        : t(`research.literature.answer.direction.${direction}`)}
                  </label>
                ))}
              </div>
            </FilterSection>
          )}
          <FilterSection label={t("research.literature.publicationYear")}>
            {yearBounds && (
              <p className="text-caption text-muted">
                {t("research.literature.answer.yearBounds", {
                  min: yearBounds.min,
                  max: yearBounds.max,
                })}
              </p>
            )}
            <div className="answer-year-inputs">
              <input
                value={yearFrom}
                inputMode="numeric"
                aria-label={t("research.literature.answer.yearFrom")}
                placeholder={t("research.literature.answer.yearFrom")}
                onChange={(event) =>
                  setYearFrom(event.target.value.replace(/\D/g, "").slice(0, 4))
                }
                className="h-8 w-full rounded-input border border-border bg-surface px-2 text-caption text-text placeholder:text-muted focus:border-accent focus:outline-none"
              />
              <input
                value={yearTo}
                inputMode="numeric"
                aria-label={t("research.literature.answer.yearTo")}
                placeholder={t("research.literature.answer.yearTo")}
                onChange={(event) =>
                  setYearTo(event.target.value.replace(/\D/g, "").slice(0, 4))
                }
                className="h-8 w-full rounded-input border border-border bg-surface px-2 text-caption text-text placeholder:text-muted focus:border-accent focus:outline-none"
              />
            </div>
          </FilterSection>
        </aside>
      )}
    </div>
  );
}

function HomeSurface({
  question,
  onQuestionChange,
  onSubmit,
  onImportPdfRequest,
  onImportCslJsonRequest,
  cslJsonImporting,
  onOpenDataset,
  serviceReady,
  projectReady,
  serviceUnavailableReason,
  projectListLoading,
  projectListResolved,
  projectListError,
  onRetryProjectList,
  creatingProject,
  createProjectError,
  onCreateProject,
  onStartFirstDiscovery,
  onOpenRuntimeHelp,
  runtimeHelpLabel,
  onDraftReport,
  onFindMorePapers,
  draftReportUnavailableReason,
  realSourceCount,
  discoveryEnabled,
  discoveryProvider,
  onDiscoveryProviderChange,
  discoveryCreating,
  discoveryCreateError,
  memoryController,
}: {
  question: string;
  onQuestionChange: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
  onImportPdfRequest: () => void;
  onImportCslJsonRequest?: () => void;
  cslJsonImporting: boolean;
  onOpenDataset: () => void;
  serviceReady: boolean;
  projectReady: boolean;
  serviceUnavailableReason: string | null;
  projectListLoading: boolean;
  projectListResolved: boolean;
  projectListError: string | null;
  onRetryProjectList: () => void;
  creatingProject: boolean;
  createProjectError: string | null;
  onCreateProject: (title: string) => Promise<boolean>;
  onStartFirstDiscovery?: (
    question: string,
    provider: "crossref" | "openalex" | "crossref-openalex",
  ) => Promise<boolean>;
  onOpenRuntimeHelp?: () => void;
  runtimeHelpLabel?: string;
  onDraftReport?: () => void;
  onFindMorePapers?: () => void;
  draftReportUnavailableReason: string | null;
  realSourceCount: number;
  discoveryEnabled: boolean;
  discoveryProvider: "crossref" | "openalex" | "crossref-openalex";
  onDiscoveryProviderChange: (
    provider: "crossref" | "openalex" | "crossref-openalex",
  ) => void;
  discoveryCreating: boolean;
  discoveryCreateError: string | null;
  memoryController?: ResearchMemoryPanelController;
}) {
  const { t } = useTranslation("pages");
  const [newProjectTitle, setNewProjectTitle] = useState("");
  const projectActionReady = serviceReady && projectReady;
  const projectActionReason = !serviceReady
    ? serviceUnavailableReason ?? undefined
    : !projectReady
      ? t("research.literature.createProjectRequired")
      : undefined;
  const discoveryQueryLength = question.trim().length;
  const discoveryQueryTooLong =
    discoveryEnabled
    && discoveryQueryLength > 500;
  const submitEnabled = Boolean(
    question.trim()
    && (
      realSourceCount > 0
      || (discoveryEnabled && !discoveryQueryTooLong)
    ),
  );

  if (projectListLoading && !projectListResolved && !onStartFirstDiscovery) {
    return (
      <div className="flex h-full min-h-0 items-start justify-center bg-bg px-6 pb-10 pt-[15vh]" aria-busy="true">
        <div className="w-full max-w-[670px] space-y-3" aria-label={t("research.literature.loadingProjects")}>
          <div className="h-5 w-40 animate-pulse rounded bg-surface-2" />
          <div className="h-24 animate-pulse rounded-card bg-surface-2" />
          <div className="h-10 w-64 animate-pulse rounded-input bg-surface-2" />
        </div>
      </div>
    );
  }

  if (!projectListResolved && projectListError && !onStartFirstDiscovery) {
    return (
      <div className="flex h-full min-h-0 items-start justify-center bg-bg px-6 pb-10 pt-[15vh]">
        <div className="w-full max-w-[520px] rounded-card border border-error/25 bg-surface p-6" role="alert">
          <h1 className="text-base font-semibold text-text">{t("research.literature.projectListFailed")}</h1>
          <p className="mt-2 text-sm leading-6 text-muted">{t("research.literature.projectListFailedBody")}</p>
          {projectListError !== serviceUnavailableReason && (
            <p className="mt-2 break-words text-xs text-error">{projectListError}</p>
          )}
          <button type="button" onClick={onRetryProjectList} className="mt-4 compact-button primary-button">
            {t("research.retry")}
          </button>
        </div>
      </div>
    );
  }

  if (!projectReady) {
    if (onStartFirstDiscovery) {
      const firstQuestion = question.trim();
      const firstQuestionTooLong = firstQuestion.length > 500;
      const firstSubmitEnabled =
        serviceReady
        && projectListResolved
        && !projectListError
        && Boolean(firstQuestion)
        && !firstQuestionTooLong
        && !creatingProject;
      return (
        <div className="flex h-full min-h-0 items-start justify-center overflow-y-auto bg-bg px-6 pb-10 pt-[12vh]">
          <div className="w-full max-w-[670px]">
            <div className="mb-5 text-center">
              <p className="text-caption font-medium uppercase tracking-[0.14em] text-muted">
                {t("research.literature.firstUseEyebrow")}
              </p>
              <h1 className="mt-2 text-2xl font-semibold tracking-[-0.025em] text-text">
                {t("research.literature.firstUseTitle")}
              </h1>
              <p className="mx-auto mt-2 max-w-[58ch] text-sm leading-6 text-muted">
                {t("research.literature.firstUseBody")}
              </p>
            </div>

            <div
              role={projectListError ? "alert" : "status"}
              aria-live="polite"
              className={cn(
                "mb-3 flex min-h-10 items-center gap-2 rounded-input border px-3 text-xs",
                projectListError
                  ? "border-error/25 bg-error/5 text-text"
                  : "border-border bg-surface text-muted",
              )}
            >
              {projectListLoading ? (
                <Loader2 size={14} className="shrink-0 animate-spin text-accent" />
              ) : (
                <CircleAlert
                  size={14}
                  className={cn("shrink-0", projectListError ? "text-error" : "text-ok")}
                />
              )}
              <span className="min-w-0 flex-1">
                {projectListLoading
                  ? t("research.literature.startingLocalService")
                  : projectListError
                    ? serviceUnavailableReason ?? t("research.literature.projectListFailedBody")
                    : t("research.literature.firstQuestionReady")}
              </span>
              {projectListError && onOpenRuntimeHelp && runtimeHelpLabel && (
                <button
                  type="button"
                  onClick={onOpenRuntimeHelp}
                  className="shrink-0 font-medium text-link hover:underline"
                >
                  {runtimeHelpLabel}
                </button>
              )}
              {projectListError && (
                <button
                  type="button"
                  onClick={onRetryProjectList}
                  disabled={projectListLoading}
                  className="shrink-0 font-medium text-link hover:underline disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {t("research.retry")}
                </button>
              )}
            </div>

            <form
              onSubmit={(event) => {
                event.preventDefault();
                if (firstSubmitEnabled) {
                  void onStartFirstDiscovery(firstQuestion, discoveryProvider);
                }
              }}
              className="overflow-hidden rounded-[14px] border border-accent bg-surface shadow-sm"
            >
              <div className="flex h-12 items-center gap-2 bg-accent px-4 text-ui font-medium text-accent-fg">
                <FileSearch size={15} />
                {t("research.literature.researchAgent")}
                <ChevronDown size={13} />
              </div>
              <label htmlFor="first-research-question" className="sr-only">
                {t("research.literature.researchQuestion")}
              </label>
              <textarea
                id="first-research-question"
                autoFocus
                value={question}
                onChange={(event) => onQuestionChange(event.target.value)}
                placeholder={t("research.literature.questionPlaceholder")}
                className="h-32 w-full resize-none bg-surface px-4 py-4 text-ui leading-5 text-text placeholder:text-muted focus:outline-none"
                aria-invalid={firstQuestionTooLong || undefined}
                aria-describedby={firstQuestionTooLong ? "first-question-length" : undefined}
              />
              <div className="flex min-h-12 items-center gap-3 border-t border-border-faint px-3">
                <label className="flex items-center gap-2 text-caption text-muted">
                  <span>{t("research.literature.discoveryProvider")}</span>
                  <select
                    value={discoveryProvider}
                    onChange={(event) =>
                      onDiscoveryProviderChange(
                        event.target.value as
                          | "crossref"
                          | "openalex"
                          | "crossref-openalex",
                      )
                    }
                    disabled={creatingProject}
                    className="h-8 rounded-input border border-border bg-surface px-2 text-caption font-medium text-text focus:border-accent focus:outline-none"
                    aria-label={t("research.literature.discoveryProvider")}
                  >
                    {/* eslint-disable-next-line i18next/no-literal-string -- provider proper nouns */}
                    <option value="crossref-openalex">Crossref + OpenAlex</option>
                    {/* eslint-disable-next-line i18next/no-literal-string -- provider proper noun */}
                    <option value="openalex">OpenAlex</option>
                    {/* eslint-disable-next-line i18next/no-literal-string -- provider proper noun */}
                    <option value="crossref">Crossref</option>
                  </select>
                </label>
                <button
                  type="submit"
                  disabled={!firstSubmitEnabled}
                  className="icon-button primary-icon-button ml-auto disabled:cursor-not-allowed disabled:opacity-50"
                  aria-label={t("research.literature.createAndReviewPlan")}
                >
                  {creatingProject ? (
                    <Loader2 size={15} className="animate-spin" />
                  ) : (
                    <ArrowRight size={15} />
                  )}
                </button>
              </div>
            </form>
            <p
              id={firstQuestionTooLong ? "first-question-length" : undefined}
              className={cn(
                "mt-2 text-right text-caption",
                firstQuestionTooLong ? "text-error" : "text-muted",
              )}
            >
              {firstQuestionTooLong
                ? t("research.literature.discoveryQueryTooLong", {
                    count: firstQuestion.length,
                    max: 500,
                  })
                : t("research.literature.firstQuestionBoundary")}
            </p>
            {(createProjectError || discoveryCreateError) && (
              <p className="mt-2 text-center text-caption text-error" role="alert">
                {createProjectError ?? discoveryCreateError}
              </p>
            )}

            <details className="mx-auto mt-5 max-w-[420px] text-center text-caption text-muted">
              <summary className="cursor-pointer list-none text-link hover:underline">
                {t("research.literature.createEmptyProjectInstead")}
              </summary>
              <form
                className="mt-3 flex gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  void onCreateProject(newProjectTitle).then((created) => {
                    if (created) setNewProjectTitle("");
                  });
                }}
              >
                <label htmlFor="competitive-project-title" className="sr-only">
                  {t("research.projectNameLabel")}
                </label>
                <input
                  id="competitive-project-title"
                  value={newProjectTitle}
                  onChange={(event) => setNewProjectTitle(event.target.value)}
                  placeholder={t("research.projectNamePlaceholder")}
                  disabled={!serviceReady || creatingProject}
                  className="min-w-0 flex-1 rounded-input border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-muted focus:border-accent focus:outline-none"
                />
                <button
                  type="submit"
                  disabled={!serviceReady || creatingProject || !newProjectTitle.trim()}
                  className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {t("research.createProject")}
                </button>
              </form>
            </details>
          </div>
        </div>
      );
    }
    return (
      <div className="flex h-full min-h-0 items-start justify-center overflow-y-auto bg-bg px-6 pb-10 pt-[15vh]">
        <div className="w-full max-w-[560px] rounded-card border border-border bg-surface p-6">
          <div className="flex items-start gap-3">
            <FileSearch size={18} className="mt-0.5 shrink-0 text-accent" aria-hidden={true} />
            <div>
              <h1 className="text-lg font-semibold text-text">{t("research.literature.firstUseTitle")}</h1>
              <p className="mt-2 max-w-[62ch] text-sm leading-6 text-muted">{t("research.literature.firstUseBody")}</p>
            </div>
          </div>
          <form
            className="mt-6 flex flex-col gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void onCreateProject(newProjectTitle).then((created) => {
                if (created) setNewProjectTitle("");
              });
            }}
          >
            <label htmlFor="competitive-project-title" className="text-xs font-medium text-text">
              {t("research.projectNameLabel")}
            </label>
            <div className="flex gap-2">
              <input
                id="competitive-project-title"
                value={newProjectTitle}
                onChange={(event) => setNewProjectTitle(event.target.value)}
                placeholder={t("research.projectNamePlaceholder")}
                disabled={!serviceReady || creatingProject}
                className="min-w-0 flex-1 rounded-input border border-border bg-surface px-3 py-2 text-sm text-text placeholder:text-muted focus:border-accent focus:outline-none"
              />
              <button
                type="submit"
                disabled={!serviceReady || creatingProject || !newProjectTitle.trim()}
                title={!serviceReady ? serviceUnavailableReason ?? undefined : undefined}
                className="compact-button primary-button disabled:cursor-not-allowed disabled:opacity-50"
              >
                {creatingProject ? t("research.creatingProject") : t("research.createProject")}
              </button>
            </div>
          </form>
          {createProjectError && (
            <p className="mt-3 text-xs leading-5 text-error" role="alert">{createProjectError}</p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 items-start justify-center overflow-y-auto bg-bg px-6 pb-10 pt-[15vh]">
      <div className="w-full max-w-[670px]">
        <form onSubmit={onSubmit} className="overflow-hidden rounded-[14px] border border-accent bg-surface">
          <div className="flex h-12 items-center gap-2 bg-accent px-4 text-ui font-medium text-accent-fg">
            <FileSearch size={15} />
            {t("research.literature.researchAgent")}
            <ChevronDown size={13} />
          </div>
          <label htmlFor="research-question" className="sr-only">{t("research.literature.researchQuestion")}</label>
          <textarea
            id="research-question"
            value={question}
            onChange={(event) => onQuestionChange(event.target.value)}
            placeholder={t("research.literature.questionPlaceholder")}
            className="h-28 w-full resize-none bg-surface px-4 py-4 text-ui leading-5 text-text placeholder:text-muted"
            disabled={discoveryCreating}
            aria-invalid={discoveryQueryTooLong || undefined}
            aria-describedby={discoveryQueryTooLong ? "discovery-query-length-error" : undefined}
          />
          <div className="flex min-h-12 items-center gap-2 border-t border-border-faint px-3">
            <button type="button" onClick={onImportPdfRequest} disabled={!projectActionReady} title={projectActionReason} className="icon-button disabled:cursor-not-allowed disabled:opacity-50" aria-label={t("research.literature.addLocalSource")}>
              <Plus size={14} />
            </button>
            <button type="button" onClick={onImportPdfRequest} disabled={!projectActionReady} title={projectActionReason} className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-50">
              <FolderOpen size={13} /> {t("research.literature.importPdf")}
            </button>
            {onImportCslJsonRequest && (
              <button
                type="button"
                onClick={onImportCslJsonRequest}
                disabled={!projectActionReady || cslJsonImporting}
                title={projectActionReason}
                className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-50"
              >
                {cslJsonImporting ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <FileUp size={13} />
                )}
                {t("research.literature.importReferences")}
              </button>
            )}
            {discoveryEnabled && (
              <label className="ml-auto flex items-center gap-2 text-caption text-muted">
                <span>{t("research.literature.discoveryProvider")}</span>
                <select
                  value={discoveryProvider}
                  onChange={(event) =>
                    onDiscoveryProviderChange(
                      event.target.value as
                        | "crossref"
                        | "openalex"
                        | "crossref-openalex",
                    )
                  }
                  disabled={discoveryCreating}
                  className="h-8 rounded-input border border-border bg-surface px-2 text-caption font-medium text-text focus:border-accent focus:outline-none"
                  aria-label={t("research.literature.discoveryProvider")}
                >
                  {/* eslint-disable-next-line i18next/no-literal-string -- provider proper nouns */}
                  <option value="crossref-openalex">Crossref + OpenAlex</option>
                  {/* eslint-disable-next-line i18next/no-literal-string -- provider proper noun */}
                  <option value="openalex">OpenAlex</option>
                  {/* eslint-disable-next-line i18next/no-literal-string -- provider proper noun */}
                  <option value="crossref">Crossref</option>
                </select>
              </label>
            )}
            <button
              type="submit"
              disabled={!projectActionReady || !submitEnabled || discoveryCreating}
              className={cn(
                "icon-button primary-icon-button disabled:cursor-not-allowed disabled:opacity-50",
                (!discoveryEnabled || realSourceCount > 0) && "ml-auto",
              )}
              aria-label={realSourceCount > 0 ? t("research.literature.submitSourceReview") : t("research.literature.submitDiscovery")}
            >
              {discoveryCreating ? <Loader2 size={15} className="animate-spin" /> : <ArrowRight size={15} />}
            </button>
          </div>
        </form>
        {discoveryEnabled && (
          <p
            id={discoveryQueryTooLong ? "discovery-query-length-error" : undefined}
            className={cn(
              "mt-2 text-right text-caption",
              discoveryQueryTooLong ? "text-error" : "text-muted",
            )}
          >
            {discoveryQueryTooLong
              ? t("research.literature.discoveryQueryTooLong", {
                  count: discoveryQueryLength,
                  max: 500,
                })
              : t("research.literature.discoveryQueryLength", {
                  count: discoveryQueryLength,
                  max: 500,
                })}
          </p>
        )}
        {discoveryCreateError && (
          <p className="mt-2 text-center text-caption text-error" role="alert">
            {t("research.literature.discovery.createFailed", { error: discoveryCreateError })}
          </p>
        )}
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          <button
            type="button"
            onClick={() => onSubmit({ preventDefault: () => undefined } as FormEvent)}
            disabled={!projectActionReady || !submitEnabled || discoveryCreating}
            className="quick-action disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Table2 size={13} /> {realSourceCount > 0 ? t("research.literature.reviewImported") : t("research.literature.searchPublicPapers")}
          </button>
          {onFindMorePapers && (
            <button
              type="button"
              onClick={onFindMorePapers}
              disabled={!projectActionReady || !question.trim() || discoveryQueryTooLong || discoveryCreating}
              className="quick-action disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Search size={13} /> {t("research.literature.findMorePapers")}
            </button>
          )}
          {onDraftReport && realSourceCount > 0 && (
            <>
              <button
                type="button"
                onClick={() => {
                  if (!draftReportUnavailableReason) onDraftReport();
                }}
                aria-disabled={Boolean(draftReportUnavailableReason)}
                aria-describedby={
                  draftReportUnavailableReason
                    ? "open-workflow-unavailable-reason"
                    : undefined
                }
                className={cn(
                  "quick-action",
                  draftReportUnavailableReason && "cursor-not-allowed opacity-50",
                )}
              >
                <FileText size={13} /> {t("research.literature.openWorkflow")}
              </button>
              {draftReportUnavailableReason && (
                <span id="open-workflow-unavailable-reason" className="sr-only">
                  {draftReportUnavailableReason}
                </span>
              )}
            </>
          )}
          <button type="button" onClick={onImportPdfRequest} disabled={!projectActionReady} title={projectActionReason} className="quick-action disabled:cursor-not-allowed disabled:opacity-50">
            <BookOpen size={13} /> {t("research.literature.readPaper")}
          </button>
          <button
            type="button"
            onClick={onOpenDataset}
            disabled={!projectActionReady}
            title={projectActionReason}
            className="quick-action disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Table2 size={13} /> {t("research.literature.importDataset")}
          </button>
        </div>
        <p className="mt-4 text-center text-caption text-muted">
          {!projectReady
            ? t("research.literature.createProjectBeforeImport")
            : realSourceCount > 0
            ? t("research.literature.importedAvailable", { count: realSourceCount })
            : t(
                discoveryEnabled
                  ? "research.literature.discoveryProposalBoundary"
                  : "research.literature.emptyLibrary",
                discoveryEnabled
                  ? {
                      provider: discoveryProvider === "crossref-openalex"
                        ? "Crossref + OpenAlex"
                        : discoveryProvider === "openalex"
                          ? "OpenAlex"
                          : "Crossref",
                      operations: discoveryProvider === "crossref-openalex" ? 2 : 1,
                      budget: discoveryProvider === "crossref-openalex" ? 10 : 5,
                    }
                  : undefined,
              )}
        </p>
        {realSourceCount > 0 && <p className="mt-1 text-center text-caption text-muted">{t("research.literature.draftReportRequiresWorkflow")}</p>}
        {memoryController && (
          <ResearchMemoryPanel
            density={RESEARCH_MEMORY_PANEL_DENSITY.compact}
            controller={memoryController}
          />
        )}
      </div>
    </div>
  );
}

function PapersSurface({
  question,
  papers,
  realSourceMode,
  selectedPaperId,
  includedIds,
  filtersOpen,
  onToggleFilters,
  onCloseFilters,
  onSelectPaper,
  discoverySnapshot,
  discoveryLoading,
  discoveryError,
  candidateTriageDecisions,
  candidateTriageLoading,
  candidateTriageError,
  candidateTriageMutationPending,
  onUpsertCandidateTriageDecision,
  discoveryOnly,
  onAttachCandidate,
  onImportCslJson,
  cslJsonImporting,
  onOpenAttachedSource,
  onContinue,
}: {
  question: string;
  papers: ResearchPaper[];
  realSourceMode: boolean;
  selectedPaperId: string;
  includedIds: Set<string>;
  filtersOpen: boolean;
  onToggleFilters: () => void;
  onCloseFilters: () => void;
  onSelectPaper: (id: string) => void;
  discoverySnapshot: WorkflowDiscoverySnapshot | null;
  discoveryLoading: boolean;
  discoveryError: string | null;
  candidateTriageDecisions: CandidateTriageDecision[];
  candidateTriageLoading: boolean;
  candidateTriageError: string | null;
  candidateTriageMutationPending: boolean;
  onUpsertCandidateTriageDecision: (
    candidateId: string,
    input: UpsertCandidateTriageDecisionInput,
  ) => Promise<CandidateTriageDecision>;
  discoveryOnly: boolean;
  onAttachCandidate: (candidate: DiscoveryCandidate, workflowId: string) => void;
  onImportCslJson?: () => void;
  cslJsonImporting: boolean;
  onOpenAttachedSource: (sourceId: string) => void;
  onContinue: () => void;
}) {
  const { t } = useTranslation("pages");
  const activityTitle = discoveryOnly
    ? t("research.literature.discovery.candidates")
    : realSourceMode
    ? t("research.literature.activityImportedPdfs")
    : t("research.literature.activityCuratedPapers");
  const [query, setQuery] = useState("");
  const [sorted, setSorted] = useState(false);
  const [readyOnly, setReadyOnly] = useState(false);
  const [yearFrom, setYearFrom] = useState("1990");
  const [studyTypes, setStudyTypes] = useState<Set<string>>(() => new Set());
  const filtersTriggerRef = useRef<HTMLButtonElement>(null);
  const visiblePapers = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    const paperSet = discoveryOnly ? [] : papers;
    const filtered = (normalized
      ? paperSet.filter((paper) => `${paper.title} ${paper.authors} ${paper.journal}`.toLocaleLowerCase().includes(normalized))
      : paperSet).filter((paper) => {
        const hasReadyPdf = paper.source?.ingestionStatus === "ready";
        const yearMatches = paper.year === "—" || Number(paper.year) >= Number(yearFrom);
        const studyMatches = studyTypes.size === 0 || [...studyTypes].some((type) => paper.studyType.toLocaleLowerCase().includes(type));
        return (!readyOnly || hasReadyPdf) && yearMatches && studyMatches;
      });
    return sorted ? sortPapersByTitle(filtered) : filtered;
  }, [discoveryOnly, papers, query, readyOnly, sorted, studyTypes, yearFrom]);
  return (
    <div className="research-repro-split h-full min-h-0">
      <ActivityColumn
        question={question}
        artifactTitle={activityTitle}
        realSourceMode={realSourceMode}
        sourceCount={discoveryOnly ? discoverySnapshot?.candidates.total ?? 0 : papers.length}
        discoveryMode={discoveryOnly}
      />
      <section className="relative flex min-w-0 flex-col bg-surface" aria-label={t("research.literature.findPapersResults")}>
        <div className="research-toolbar">
          {!discoveryOnly && <><label className="search-field">
              <Search size={13} />
              <span className="sr-only">{t("research.literature.searchPapers")}</span>
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("research.literature.searchPapers", { defaultValue: "Search papers" })} />
            </label>
            <button type="button" onClick={() => setSorted((value) => !value)} aria-pressed={sorted} className="compact-button selected-button"><ListFilter size={13} /> {sorted ? t("research.literature.sortAz", { defaultValue: "Sort: Title (A–Z)" }) : t("research.literature.sortRelevant", { defaultValue: "Sort: Most relevant" })}</button>
            <button ref={filtersTriggerRef} type="button" onClick={onToggleFilters} aria-expanded={filtersOpen} className="compact-button secondary-button">
              <Filter size={13} /> {t("research.literature.filters")}
            </button></>}
          {discoveryOnly && <span className="text-caption text-muted">{t("research.literature.discovery.triageBoundary")}</span>}
          {!discoveryOnly && <button type="button" onClick={onContinue} className="compact-button secondary-button ml-auto">{t("research.literature.screenPapers")}</button>}
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <DiscoveryCandidateSection
            snapshot={discoverySnapshot}
            loading={discoveryLoading}
            error={discoveryError}
            decisions={candidateTriageDecisions}
            decisionsLoading={candidateTriageLoading}
            decisionsError={candidateTriageError}
            mutationPending={candidateTriageMutationPending}
            onUpsertDecision={onUpsertCandidateTriageDecision}
            onAttachCandidate={onAttachCandidate}
            onOpenAttachedSource={onOpenAttachedSource}
            onImportCslJson={onImportCslJson}
            cslJsonImporting={cslJsonImporting}
          />
          {!discoveryOnly && <table className="evidence-table min-w-[1120px]">
            <thead><tr><th>{t("research.literature.sourceCount", { count: visiblePapers.length })}</th><th>{t("research.literature.importStatus")}</th><th>{t("research.literature.sessionInclude")}</th><th>{t("research.literature.pages")}</th><th>{t("research.literature.population")}</th><th>{t("research.literature.relevance")}</th><th>{t("research.literature.studyType")}</th><th>{t("research.literature.summary")}</th></tr></thead>
            <tbody>
              {visiblePapers.map((paper) => (
                <tr
                  key={paper.id}
                  className={cn(selectedPaperId === paper.id && "selected-row")}
                  onClick={() => onSelectPaper(paper.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onSelectPaper(paper.id);
                    }
                  }}
                  tabIndex={0}
                >
                  <td><PaperIdentity paper={paper} /></td>
                  {/* eslint-disable-next-line i18next/no-literal-string -- canonical ingestion status and badge tone discriminants */}
                  <td><StateBadge label={paper.ingestionLabel} tone={paper.source?.ingestionStatus === "failed" ? "error" : paper.source?.ingestionStatus === "ready" ? "ok" : "neutral"} /></td>
                  {/* eslint-disable-next-line i18next/no-literal-string -- semantic badge tone discriminants */}
                  <td><StateBadge label={includedIds.has(paper.id) ? t("research.literature.included") : t("research.literature.review")} tone={includedIds.has(paper.id) ? "ok" : "neutral"} /></td>
                  {/* eslint-disable-next-line i18next/no-literal-string -- unavailable page-count marker */}
                  <td>{paper.pageCount ?? "—"}</td>
                  <td>{paper.population}</td><td><StateBadge label={paper.origin === "source" ? t("research.literature.sourcePresentation.needsReview") : paper.relevance} tone={STATUS_TONES.warn} /></td><td>{paper.studyType}</td><td>{paper.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>}
        </div>
        {!discoveryOnly && filtersOpen && <FiltersPanel readyOnly={readyOnly} yearFrom={yearFrom} studyTypes={studyTypes} onReadyOnlyChange={setReadyOnly} onYearFromChange={setYearFrom} onStudyTypeChange={(type, checked) => setStudyTypes((current) => { const next = new Set(current); if (checked) next.add(type); else next.delete(type); return next; })} onClose={() => { onCloseFilters(); filtersTriggerRef.current?.focus(); }} />}
      </section>
    </div>
  );
}

function ActivityColumn({ question, artifactTitle, realSourceMode = false, sourceCount = 0, discoveryMode = false }: { question: string; artifactTitle: string; realSourceMode?: boolean; sourceCount?: number; discoveryMode?: boolean }) {
  const { t } = useTranslation("pages");
  return (
    <aside className="research-activity-column">
      <div className="question-bubble">{question}</div>
      {discoveryMode ? (
        <>
          <ActivityLine icon={<Search size={13} />} text={t("research.literature.discovery.candidateRecords", { count: sourceCount })} />
          <ActivityLine icon={<ListFilter size={13} />} text={t("research.literature.discovery.noSourceBoundary")} />
        </>
      ) : realSourceMode ? (
        <>
          <ActivityLine icon={<FolderOpen size={13} />} text={t("research.literature.loadedLocalPdfs", { count: sourceCount })} />
          <ActivityLine icon={<ListFilter size={13} />} text={t("research.literature.canonicalImportStates")} />
          <ActivityLine icon={<Table2 size={13} />} text={t("research.literature.screeningSessionOnly")} />
        </>
      ) : (
        <ActivityLine icon={<FolderOpen size={13} />} text={t("research.literature.emptyLibrary")} />
      )}
      {!discoveryMode && <div className="artifact-preview" aria-label={t("research.literature.artifactSummaryAria", { title: artifactTitle })}>
        <Table2 size={15} />
        <span className="min-w-0 flex-1"><strong>{artifactTitle}</strong><small>{t("research.literature.artifactSummary")}</small></span>
      </div>}
      {!discoveryMode && <div className="activity-composer" role="note">{t("research.literature.activityComposer")}</div>}
    </aside>
  );
}

function DiscoveryCandidateSection({
  snapshot,
  loading,
  error,
  decisions = [],
  decisionsLoading = false,
  decisionsError = null,
  mutationPending = false,
  onUpsertDecision,
  compact = false,
  onAttachCandidate,
  onOpenAttachedSource,
  onImportCslJson,
  cslJsonImporting = false,
}: {
  snapshot: WorkflowDiscoverySnapshot | null;
  loading: boolean;
  error: string | null;
  decisions?: CandidateTriageDecision[];
  decisionsLoading?: boolean;
  decisionsError?: string | null;
  mutationPending?: boolean;
  onUpsertDecision?: (
    candidateId: string,
    input: UpsertCandidateTriageDecisionInput,
  ) => Promise<CandidateTriageDecision>;
  compact?: boolean;
  onAttachCandidate?: (candidate: DiscoveryCandidate, workflowId: string) => void;
  onOpenAttachedSource?: (sourceId: string) => void;
  onImportCslJson?: () => void;
  cslJsonImporting?: boolean;
}) {
  const { t } = useTranslation("pages");
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null);
  const [busyIds, setBusyIds] = useState<Set<string>>(() => new Set());
  const [localMutationError, setLocalMutationError] = useState<string | null>(null);
  useEffect(() => {
    setBusyIds(new Set());
    setLocalMutationError(null);
  }, [snapshot?.workflowId]);
  if (!snapshot && !loading && !error) return null;
  const candidates = snapshot?.candidates.items ?? [];
  const decisionsByCandidate = new Map(
    decisions.map((decision) => [decision.candidateId, decision] as const),
  );
  const setDecision = async (
    candidateId: string,
    decision: CandidateTriageDecisionValue,
  ) => {
    if (!onUpsertDecision || busyIds.has(candidateId) || decisionsLoading) return;
    const current = decisionsByCandidate.get(candidateId);
    setBusyIds((ids) => new Set(ids).add(candidateId));
    setLocalMutationError(null);
    try {
      await onUpsertDecision(candidateId, {
        decision,
        reason: current?.reason ?? null,
        criteriaVersion: current?.criteriaVersion ?? "candidate-triage-v1",
        expectedVersion: current?.rowVersion ?? 0,
      });
    } catch (error) {
      setLocalMutationError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyIds((ids) => {
        const next = new Set(ids);
        next.delete(candidateId);
        return next;
      });
    }
  };
  const completedOperations = snapshot
    ? snapshot.summary.succeededOperations + snapshot.summary.failedOperations
      + snapshot.summary.outcomeUnknownOperations + snapshot.summary.cancelledOperations
    : 0;
  const queriesById = new Map(
    snapshot?.exactScope.queries.map((query) => [query.id, query]) ?? [],
  );
  const latestSelection = snapshot?.latestAgentSelection ?? null;
  const selectedOperation = latestSelection
    ? snapshot?.operations.find(
        (operation) => operation.operationKey === latestSelection.selectedOperationKey,
      ) ?? null
    : null;
  const latestObservedOperation = selectedOperation
    ?? [...(snapshot?.operations ?? [])]
      .reverse()
      .find((operation) => operation.status !== "not-started")
    ?? null;
  const selectedQuery = latestSelection
    ? queriesById.get(latestSelection.queryId)?.query ?? latestSelection.queryId
    : null;
  const stopReasonLabel = snapshot?.stopReason
    ? t(`research.literature.discovery.stopReasons.${snapshot.stopReason}`, {
        defaultValue: snapshot.stopReason,
      })
    : null;
  const loopInterrupted = Boolean(
    snapshot
    && !snapshot.stopReason
    && ["blocked", "failed", "cancelled"].includes(snapshot.workflowStatus),
  );
  return (
    <section className={cn("discovery-candidates", compact && "discovery-candidates-compact")} aria-label={t("research.literature.discovery.candidateSection")}>
      {snapshot?.discoverySpecStatus === "approved" && (
        <section className="agent-loop-panel" aria-label={t("research.literature.discovery.agentLoop.label")}>
          <div className="agent-loop-heading">
            <span>{t("research.literature.discovery.agentLoop.label")}</span>
            <small>{t("research.literature.discovery.agentLoop.observation", {
              complete: completedOperations,
              candidates: snapshot.summary.uniqueCandidateCount,
            })}</small>
          </div>
          <ol className="agent-loop-steps">
            <li className="agent-loop-step">
              <span>{t("research.literature.discovery.agentLoop.observe")}</span>
              <strong>{t("research.literature.discovery.agentLoop.observation", {
                complete: completedOperations,
                candidates: snapshot.summary.uniqueCandidateCount,
              })}</strong>
            </li>
            <li className="agent-loop-step">
              <span>{t("research.literature.discovery.agentLoop.decide")}</span>
              <strong title={selectedQuery ?? undefined}>
                {latestSelection && selectedQuery
                  ? t("research.literature.discovery.agentLoop.selectedAction", {
                      query: selectedQuery,
                      provider: latestSelection.provider,
                    })
                  : stopReasonLabel
                    ? t("research.literature.discovery.agentLoop.noFurtherAction")
                  : loopInterrupted
                    ? t("research.literature.discovery.agentLoop.decisionUnavailable")
                  : t("research.literature.discovery.agentLoop.awaitingDecision")}
              </strong>
              {latestSelection && (
                <small>{t(
                  `research.literature.discovery.agentLoop.reason.${latestSelection.reasonCode}`,
                )}</small>
              )}
            </li>
            <li className="agent-loop-step">
              <span>{t("research.literature.discovery.agentLoop.act")}</span>
              <strong>
                {latestObservedOperation
                  ? t("research.literature.discovery.agentLoop.actionResult", {
                      status: t(
                        `research.literature.discovery.operationStatus.${latestObservedOperation.status}`,
                      ),
                      novel: latestObservedOperation.novelCandidateCount,
                      duplicates: latestObservedOperation.duplicateCount,
                    })
                  : t("research.literature.discovery.agentLoop.awaitingDecision")}
              </strong>
            </li>
            <li className="agent-loop-step">
              <span>{t("research.literature.discovery.agentLoop.assess")}</span>
              <strong>
                {stopReasonLabel
                  ? t("research.literature.discovery.agentLoop.stopped")
                  : loopInterrupted
                    ? t("research.literature.discovery.agentLoop.interrupted")
                  : t("research.literature.discovery.agentLoop.continuing", {
                      count: snapshot.summary.notStartedOperations,
                    })}
              </strong>
              {stopReasonLabel && <small>{stopReasonLabel}</small>}
            </li>
          </ol>
        </section>
      )}
      <div className="discovery-progress-strip" role="status">
        <StateBadge label={t("research.literature.discovery.candidates")} tone={STATUS_TONES.warn} />
        <span>{loading ? t("research.literature.discovery.loading") : snapshot ? t("research.literature.discovery.progress", { complete: completedOperations, total: snapshot.summary.totalOperations, candidates: snapshot.summary.uniqueCandidateCount }) : t("research.literature.discovery.unavailable")}</span>
        {snapshot && (
          <small>
            {snapshot.discoverySpecStatus === "approved"
              ? loopInterrupted
                ? t("research.literature.discovery.pausedRemaining", {
                    count: snapshot.summary.notStartedOperations,
                  })
                : t("research.literature.discovery.remainingOperations", {
                    count: snapshot.summary.notStartedOperations,
                  })
              : t("research.literature.discovery.awaitingApproval", {
                  count: snapshot.summary.notStartedOperations,
                })}
          </small>
        )}
        {stopReasonLabel && <small>{t("research.literature.discovery.stopReason", { code: stopReasonLabel })}</small>}
        {!compact && snapshot && onImportCslJson && (
          <button
            type="button"
            className="compact-button secondary-button ml-auto"
            onClick={onImportCslJson}
            disabled={cslJsonImporting}
          >
            {cslJsonImporting ? <Loader2 size={13} className="animate-spin" /> : <FileUp size={13} />}
            {cslJsonImporting
              ? t("research.literature.discovery.importingCslJson")
              : t("research.literature.discovery.importCslJson")}
          </button>
        )}
      </div>
      {snapshot && (
        <div className="discovery-ledger-wrap">
          <table className="discovery-ledger" aria-label={t("research.literature.discovery.ledger")}>
            <thead>
              <tr>
                <th>{t("research.literature.discovery.queryProvider")}</th>
                <th>{t("research.literature.discovery.status")}</th>
                <th>{t("research.literature.discovery.counts")}</th>
                <th>{t("research.literature.discovery.resultBudget")}</th>
                <th>{t("research.literature.discovery.retryState")}</th>
              </tr>
            </thead>
            <tbody>
              {snapshot.operations.map((operation) => {
                const query = queriesById.get(operation.queryId);
                const statusTone = operation.status === "succeeded"
                  ? STATUS_TONES.ok
                  : operation.status === "failed" || operation.status === "outcome-unknown"
                    ? STATUS_TONES.error
                    : operation.status === "pending" || operation.status === "prepared"
                      ? STATUS_TONES.warn
                      : STATUS_TONES.neutral;
                return (
                  <tr key={operation.operationKey}>
                    <td>
                      <strong>{query?.query ?? operation.queryId}</strong>
                      <small>{operation.provider}</small>
                    </td>
                    <td>
                      <StateBadge
                        label={t(`research.literature.discovery.operationStatus.${operation.status}`)}
                        tone={statusTone}
                      />
                    </td>
                    <td>{t("research.literature.discovery.operationCounts", {
                      returned: operation.returnedCount,
                      novel: operation.novelCandidateCount,
                      duplicates: operation.duplicateCount,
                    })}</td>
                    <td>{t("research.literature.discovery.approvedResultBudget", {
                      returned: operation.returnedCount,
                      approved: query?.maxResultsPerProvider ?? 0,
                      remaining: Math.max(
                        (query?.maxResultsPerProvider ?? 0) - operation.returnedCount,
                        0,
                      ),
                    })}</td>
                    <td>
                      <span>{operation.retryClassification
                        ? t(`research.literature.discovery.retryClassification.${operation.retryClassification}`)
                        : t("research.literature.discovery.noRetryClassification")}</span>
                      {operation.errorCode && <small className="block text-error">{t("research.literature.discovery.errorCode", { code: operation.errorCode })}</small>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="discovery-boundary">{t("research.literature.discovery.boundary")}</p>
      {error && <p className="discovery-error" role="alert">{t("research.literature.discovery.readFailed")}</p>}
      {(decisionsError || localMutationError) && (
        <p className="discovery-error" role="alert">
          {t("research.literature.discovery.triageSaveFailed", {
            error: localMutationError ?? decisionsError ?? "",
          })}
        </p>
      )}
      {!loading && !error && candidates.length === 0 && <p className="discovery-empty">{t("research.literature.discovery.empty")}</p>}
      {candidates.length > 0 && (
        <div className="discovery-candidate-table-wrap">
          <table className="evidence-table discovery-candidate-table min-w-[1040px]">
            <thead><tr><th>{t("research.literature.discovery.candidate")}</th><th>{t("research.literature.discovery.metadata")}</th><th>{t("research.literature.discovery.verification")}</th><th>{t("research.literature.discovery.triage")}</th><th>{t("research.literature.discovery.availability")}</th></tr></thead>
            <tbody>{candidates.map((candidate) => {
              const triage = decisionsByCandidate.get(candidate.id);
              const kept = triage?.decision === "keep";
              const busy = busyIds.has(candidate.id);
              const decisionDisabled = (
                !onUpsertDecision
                || decisionsLoading
                || mutationPending
                || busy
              );
              return (
              <tr key={candidate.id} tabIndex={0} aria-selected={selectedCandidateId === candidate.id} className={cn(selectedCandidateId === candidate.id && "selected-row")} onClick={() => setSelectedCandidateId(candidate.id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelectedCandidateId(candidate.id); } }}>
                <td>
                  <span className="paper-identity">
                    <FileSearch size={14} />
                    <span>
                      <strong>{candidate.title}</strong>
                      <small className="candidate-authors">
                        {candidate.authors.join(", ") || t("research.literature.discovery.unknownAuthor")}
                      </small>
                      <small className="candidate-meta-line">
                        {candidate.publicationDate?.slice(0, 10) ?? "—"} · {candidate.provider}
                      </small>
                    </span>
                  </span>
                </td>
                <td><span className="candidate-abstract">{candidate.abstract ?? t("research.literature.discovery.noAbstract")}</span></td>
                <td><span className="candidate-badges"><StateBadge label={t("research.literature.discovery.untrustedMetadata")} tone={STATUS_TONES.warn} /><StateBadge label={t("research.literature.discovery.notVerified")} tone={STATUS_TONES.neutral} /></span></td>
                <td>
                  <span className="candidate-triage">
                    <span className="candidate-triage-actions" role="group" aria-label={`${t("research.literature.discovery.triage")}: ${candidate.title}`}>
                      {/* eslint-disable-next-line i18next/no-literal-string -- canonical candidate-triage decision discriminants */}
                      {(["keep", "reject", "uncertain"] as const).map((value) => (
                        <button
                          key={value}
                          type="button"
                          aria-pressed={triage?.decision === value}
                          aria-busy={busy}
                          disabled={decisionDisabled}
                          className="candidate-triage-action"
                          onClick={(event) => {
                            event.stopPropagation();
                            void setDecision(candidate.id, value);
                          }}
                        >
                          {t(`research.literature.discovery.triageDecision.${value}`)}
                        </button>
                      ))}
                    </span>
                    <small>{t("research.literature.discovery.notEvidence")}</small>
                  </span>
                </td>
                <td>
                  <span className="candidate-badges">
                    <StateBadge
                      label={candidate.attachedSourceId
                        ? t("research.literature.discovery.verifiedLocalSource")
                        : t("research.literature.discovery.manualPdf")}
                      tone={candidate.attachedSourceId ? STATUS_TONES.ok : STATUS_TONES.neutral}
                    />
                    {!compact && candidate.attachedSourceId && onOpenAttachedSource && (
                      <button
                        type="button"
                        className="compact-button secondary-button"
                        onClick={(event) => {
                          event.stopPropagation();
                          onOpenAttachedSource(candidate.attachedSourceId!);
                        }}
                      >
                        {t("research.literature.discovery.openReader")}
                      </button>
                    )}
                    {!compact && !candidate.attachedSourceId && onAttachCandidate && snapshot && (
                      <button
                        type="button"
                        className={cn(
                          "compact-button",
                          kept ? "primary-button" : "secondary-button",
                        )}
                        aria-label={`${kept
                          ? t("research.literature.discovery.attachLocalPdf")
                          : t("research.literature.discovery.keepBeforeAttach")}: ${candidate.title}`}
                        disabled={!kept || decisionsLoading || mutationPending || busy}
                        onClick={(event) => {
                          event.stopPropagation();
                          onAttachCandidate(candidate, snapshot.workflowId);
                        }}
                      >
                        {kept
                          ? t("research.literature.discovery.attachLocalPdf")
                          : t("research.literature.discovery.keepBeforeAttach")}
                      </button>
                    )}
                  </span>
                </td>
              </tr>
            );})}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function EvidenceCoverageSummary({
  coverage,
  loading,
  error,
  onRetry,
}: {
  coverage: WorkflowEvidenceCoverage | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  const { t } = useTranslation("pages");
  const facetCounts = { complete: 0, partial: 0, unverified: 0, missing: 0 };
  for (const facet of coverage?.facets ?? []) facetCounts[facet.state] += 1;
  if (loading) {
    return <div className="evidence-coverage-summary" aria-busy="true"><StateBadge label={t("research.literature.coverage.heading")} tone={STATUS_TONES.neutral} /><span className="evidence-coverage-skeleton">{t("research.literature.coverage.loading")}</span></div>;
  }
  if (error) {
    return <div className="evidence-coverage-summary" role="alert"><StateBadge label={t("research.literature.coverage.heading")} tone={STATUS_TONES.error} /><span>{t("research.literature.coverage.error")}</span><button type="button" onClick={onRetry} className="compact-button secondary-button">{t("research.retry")}</button></div>;
  }
  if (!coverage || coverage.state === "not-ready") {
    return <div className="evidence-coverage-summary"><StateBadge label={t("research.literature.coverage.notReady")} tone={STATUS_TONES.warn} /><span>{t("research.literature.coverage.notReadyDetail")}</span></div>;
  }
  const hasExtractionFacets = coverage.facets.length > 0;
  const hasOnlyMissingExtractionFacets =
    hasExtractionFacets &&
    coverage.facets.every((facet) => facet.state === "missing");
  return (
    <div className="evidence-coverage-summary">
      <StateBadge label={t("research.literature.coverage.heading")} tone={STATUS_TONES.ok} />
      {!hasExtractionFacets ? (
        <span>{t("research.literature.coverage.noExtractionFacets")}</span>
      ) : hasOnlyMissingExtractionFacets ? (
        <span>
          {t("research.literature.coverage.emptyExtractionFacets", {
            count: coverage.facets.length,
          })}
        </span>
      ) : (
        <>
          <span>{t("research.literature.coverage.sourceBreadth", { frozen: coverage.sourceBreadth.frozenSourceCount, covered: coverage.sourceBreadth.sourcesWithCoveredEvidenceCount, spans: coverage.sourceBreadth.verifiedReferencedSpanCount })}</span>
          <span>{t("research.literature.coverage.facets", facetCounts)}</span>
        </>
      )}
      <span className="evidence-coverage-boundary">
        {coverage.claimCoverage.state === "verified-frozen"
          ? t("research.literature.coverage.claimsVerifiedFrozen", {
              linked: coverage.claimCoverage.evidenceLinkedClaimCount,
              total: coverage.claimCoverage.totalClaimCount,
              unresolved: coverage.claimCoverage.unresolvedQuestionCount,
            })
          : coverage.claimCoverage.state === "not-verified"
            ? t("research.literature.coverage.claimsNotVerified", {
                total: coverage.claimCoverage.totalClaimCount,
                unresolved: coverage.claimCoverage.unresolvedQuestionCount,
              })
            : t("research.literature.coverage.claimsNotGenerated", {
                unresolved: coverage.claimCoverage.unresolvedQuestionCount,
              })}
      </span>
    </div>
  );
}

function ActivityLine({ icon, text }: { icon: ReactNode; text: string }) {
  return <div className="activity-line"><span className="text-accent">{icon}</span><span>{text}</span></div>;
}

function FiltersPanel({ readyOnly, yearFrom, studyTypes, onReadyOnlyChange, onYearFromChange, onStudyTypeChange, onClose }: { readyOnly: boolean; yearFrom: string; studyTypes: Set<string>; onReadyOnlyChange: (checked: boolean) => void; onYearFromChange: (value: string) => void; onStudyTypeChange: (type: string, checked: boolean) => void; onClose: () => void }) {
  const { t } = useTranslation("pages");
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { closeRef.current?.focus(); }, []);
  return (
    <aside className="filters-panel" aria-label={t("research.literature.paperFilters")} onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}>
      <div className="flex items-center justify-between border-b border-border px-4 py-3"><strong>{t("research.literature.filters")}</strong><button ref={closeRef} type="button" onClick={onClose} className="icon-button" aria-label={t("research.literature.closeFilters")}><X size={14} /></button></div>
      <FilterSection label={t("research.literature.sourceHasPdf")}><label className="filter-check"><input type="checkbox" aria-label={t("research.literature.sourceHasPdf")} checked={readyOnly} onChange={(event) => onReadyOnlyChange(event.target.checked)} /> {t("research.literature.fullTextAvailable")}</label></FilterSection>
      <FilterSection label={t("research.literature.publicationYear")}><div className="flex justify-between text-caption text-muted"><span>{yearFrom}</span><span>2026</span></div><input aria-label={t("research.literature.publicationYearFrom")} className="w-full accent-[var(--accent)]" type="range" min="1990" max="2026" value={yearFrom} onChange={(event) => onYearFromChange(event.target.value)} /></FilterSection>
      {/* eslint-disable-next-line i18next/no-literal-string -- canonical study-type filter values */}
      <FilterSection label={t("research.literature.studyType")}>{[["review", t("research.literature.filterReview")], ["meta-analysis", t("research.literature.filterMetaAnalysis")], ["systematic review", t("research.literature.filterSystematicReview")], ["randomized", t("research.literature.filterRandomized")]].map(([value, label]) => <label key={value} className="filter-check"><input type="checkbox" checked={studyTypes.has(value)} onChange={(event) => onStudyTypeChange(value, event.target.checked)} /> {label}</label>)}</FilterSection>
    </aside>
  );
}

function FilterSection({ label, children }: { label: string; children: ReactNode }) {
  return <div className="border-b border-border-faint px-4 py-4"><div className="mb-3 text-xs font-semibold text-text">{label}</div><div className="space-y-2">{children}</div></div>;
}

function ScreeningSurface({ papers, realSourceMode: _realSourceMode, selectedPaperId, includedIds, screeningDecisions, busyIds, loading, persistenceError, discoveryOnly, onSelectPaper, onTogglePaper, onOpenReading, onContinue }: { papers: ResearchPaper[]; realSourceMode: boolean; selectedPaperId: string; includedIds: Set<string>; screeningDecisions: ScreeningDecision[]; busyIds: Set<string>; loading: boolean; persistenceError: string | null; discoveryOnly: boolean; onSelectPaper: (id: string) => void; onTogglePaper: (id: string) => void; onOpenReading: () => void; onContinue: () => void }) {
  const { t } = useTranslation("pages");
  const [query, setQuery] = useState("");
  const [sorted, setSorted] = useState(false);
  const visiblePapers = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    const filtered = normalized ? papers.filter((paper) => `${paper.title} ${paper.authors}`.toLocaleLowerCase().includes(normalized)) : papers;
    return sorted ? sortPapersByTitle(filtered) : filtered;
  }, [papers, query, sorted]);
  const decisionBySource = useMemo(
    () => new Map(screeningDecisions.map((decision) => [decision.sourceId, decision.decision])),
    [screeningDecisions],
  );
  const excludedCount = papers.filter(
    (paper) => !includedIds.has(paper.id) && decisionBySource.get(paper.id) === "exclude",
  ).length;
  const awaitingDecisionCount = papers.filter(
    (paper) => !includedIds.has(paper.id) && decisionBySource.get(paper.id) !== "exclude",
  ).length;
  const selected = visiblePapers.find((paper) => paper.id === selectedPaperId) ?? papers.find((paper) => paper.id === selectedPaperId) ?? visiblePapers[0] ?? papers[0];
  return (
    <div className="research-matrix-layout h-full min-h-0">
      <section className="flex min-w-0 flex-col bg-surface">
        <div className="research-toolbar">{!discoveryOnly && <><button type="button" onClick={onOpenReading} className="compact-button secondary-button">{t("research.literature.readingMode")}</button><button type="button" onClick={() => setSorted((value) => !value)} aria-pressed={sorted} className="compact-button secondary-button">{sorted ? t("research.literature.sortAz") : t("research.literature.sortRelevant")}</button><label className="search-field"><Search size={13} /><span className="sr-only">{t("research.literature.searchScreened")}</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("research.literature.search")} /></label><span className="ml-auto text-caption text-muted">{t("research.literature.screenedIncluded", { screened: visiblePapers.length, included: includedIds.size })}</span><button type="button" onClick={onContinue} className="compact-button primary-button">{t("research.literature.defineExtraction")}</button></>}{discoveryOnly && <span className="text-caption text-muted">{t("research.literature.discovery.screeningBoundary")}</span>}</div>
        {persistenceError && <div role="alert" className="border-b border-error/25 bg-error/5 px-4 py-2 text-caption text-error">{t("research.literature.screeningSaveFailed", { error: persistenceError })}</div>}
        <div className="screening-grid-header"><span>{t("research.literature.paper")}</span><span>{t("research.literature.screeningJudgment")}</span></div>
        <div className="min-h-0 flex-1 overflow-auto">
          {visiblePapers.map((paper) => {
            const canInclude = paper.source?.ingestionStatus === "ready";
            const included = canInclude && includedIds.has(paper.id);
            const decision = decisionBySource.get(paper.id);
            const decisionLabel = included
              ? t("research.literature.include")
              : decision === "exclude"
                ? t("research.literature.exclude")
                : t("research.literature.review");
            /* eslint-disable i18next/no-literal-string -- StateBadge tone discriminants */
            const decisionTone = included
              ? "ok"
              : decision === "exclude"
                ? "error"
                : "neutral";
            /* eslint-enable i18next/no-literal-string */
            return (
              <div key={paper.id} role="row" className={cn("screening-row", selectedPaperId === paper.id && "selected-row")}>
                <PaperIdentity paper={paper} />
                {/* eslint-disable-next-line i18next/no-literal-string -- canonical source status and badge tone discriminants */}
                <span className="screening-judgment"><span className="flex items-center justify-between"><StateBadge label={canInclude ? decisionLabel : t("research.literature.unavailable")} tone={canInclude ? decisionTone : "neutral"} /><StateBadge label={paper.ingestionLabel} tone={paper.source?.ingestionStatus === "failed" ? "error" : paper.source?.ingestionStatus === "ready" ? "ok" : "neutral"} /></span><button type="button" data-screening-paper={paper.id} className="screening-summary" onClick={() => onSelectPaper(paper.id)} onKeyDown={(event) => { const direction = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0; if (!direction) return; event.preventDefault(); const index = visiblePapers.findIndex((item) => item.id === paper.id); const target = visiblePapers[Math.max(0, Math.min(visiblePapers.length - 1, index + direction))]; onSelectPaper(target.id); document.querySelector<HTMLButtonElement>(`[data-screening-paper="${target.id}"]`)?.focus(); }}>{paper.summary}</button><small>{paper.pageCount ?? t("research.literature.unknown")} {t("research.literature.pages")} · {paper.studyType} · {t("research.literature.persistedJudgment")}</small><button type="button" onClick={() => onTogglePaper(paper.id)} className="inline-toggle" role="checkbox" aria-checked={included} aria-busy={busyIds.has(paper.id)} disabled={!canInclude || loading || busyIds.has(paper.id)}>{busyIds.has(paper.id) ? t("research.literature.savingDecision") : canInclude ? (included ? t("research.literature.included") : decision === "exclude" ? t("research.literature.excluded") : t("research.literature.review")) : t("research.literature.notReady")}</button></span>
              </div>
            );
          })}
        </div>
      </section>
      <aside className="matrix-inspector">
        <h2>{t("research.literature.screeningResults")}</h2>
        {discoveryOnly ? (
          <p className="text-muted">{t("research.literature.discovery.screeningBoundary")}</p>
        ) : (
          <>
            <p className="text-muted">{t("research.literature.overview")}</p>
            <p>{t("research.literature.localPdfsReviewed", { count: papers.length })}</p>
            <p className="text-ok">{t("research.literature.includedPersisted", { count: includedIds.size })}</p>
            <p className="text-error">{t("research.literature.excludedPersisted", { count: excludedCount })}</p>
            <p className="text-muted">{t("research.literature.awaitingScreeningPersisted", { count: awaitingDecisionCount })}</p>
            {selected && (
              <>
                <hr />
                <p className="text-muted">{t("research.literature.selectedPaper")}</p>
                <strong>{selected.title}</strong>
                <p
                  className={
                    includedIds.has(selected.id)
                      ? "text-ok"
                      : decisionBySource.get(selected.id) === "exclude"
                        ? "text-error"
                        : "text-muted"
                  }
                >
                  {includedIds.has(selected.id)
                    ? t("research.literature.includedForProject")
                    : decisionBySource.get(selected.id) === "exclude"
                      ? t("research.literature.excludedForProject")
                      : t("research.literature.awaitingScreeningForProject")}
                </p>
                <p>{selected.summary}</p>
                <small>{t("research.literature.screeningKeyboardHint")}</small>
              </>
            )}
          </>
        )}
      </aside>
    </div>
  );
}

function ExtractionSurface({ projectId, projectIdRef, generation, papers, realSourceMode, screeningExportState, selectedPaperId, onSelectPaper, extractionMatrix, loading, persistenceError, evidenceCoverage, evidenceCoverageLoading, evidenceCoverageError, onRetryEvidenceCoverage, onCreateColumn, onUpsertCell, onDeleteCell, onCreateCitedBrief, onContinue }: { projectId: string | null; projectIdRef?: { current: string | null }; generation: number; papers: ResearchPaper[]; realSourceMode: boolean; screeningExportState: ScreeningExportState; selectedPaperId: string; onSelectPaper: (id: string) => void; extractionMatrix: ExtractionMatrix; loading: boolean; persistenceError: string | null; evidenceCoverage: WorkflowEvidenceCoverage | null; evidenceCoverageLoading: boolean; evidenceCoverageError: string | null; onRetryEvidenceCoverage: () => void; onCreateColumn: (input: CreateExtractionColumnInput) => Promise<unknown>; onUpsertCell: (sourceId: string, columnId: string, input: UpsertExtractionCellInput) => Promise<unknown>; onDeleteCell: (sourceId: string, columnId: string, expectedVersion: number) => Promise<void>; onCreateCitedBrief: () => Promise<void>; onContinue: () => void }) {
  const { t } = useTranslation("pages");
  const [query, setQuery] = useState("");
  const [sorted, setSorted] = useState(false);
  const [newColumnName, setNewColumnName] = useState("");
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [briefBusy, setBriefBusy] = useState(false);
  const [briefError, setBriefError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [reviewGaps, setReviewGaps] = useState(false);
  const requestGenerationRef = useRef(generation);
  const mountedProjectRef = useRef(projectId);
  const columnHeaderRefs = useRef<Record<string, HTMLTableCellElement | null>>({});
  const coverageIdentity = [
    projectId,
    evidenceCoverage?.projectId,
    evidenceCoverage?.workflowId,
    evidenceCoverage?.planId,
    evidenceCoverage?.planVersion,
    evidenceCoverage?.planSha256,
    evidenceCoverage?.sourceSetSha256,
  ].join("\u0000");
  useEffect(() => {
    requestGenerationRef.current = generation;
    mountedProjectRef.current = projectId;
    setBusyKey(null);
    setLocalError(null);
    setBriefError(null);
    setBriefBusy(false);
    setExporting(false);
    setDrafts({});
  }, [projectId, generation]);
  useEffect(() => {
    setReviewGaps(false);
  }, [coverageIdentity]);
  const rows = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    const filtered = normalized ? papers.filter((paper) => `${paper.title} ${paper.authors}`.toLocaleLowerCase().includes(normalized)) : papers;
    return sorted ? sortPapersByTitle(filtered) : filtered;
  }, [papers, query, sorted]);
  const facetByColumnId = useMemo(
    () => new Map((evidenceCoverage?.facets ?? []).map((facet) => [facet.columnId, facet])),
    [evidenceCoverage?.facets],
  );
  if (papers.length === 0) {
    return <div className="flex h-full items-center justify-center bg-bg p-8"><div className="max-w-md text-center"><h2 className="text-ui font-semibold">{t("research.literature.noSourcesForExtraction")}</h2><p className="mt-2 text-xs text-muted">{t("research.literature.noSourcesForExtractionBody", { sourceType: realSourceMode ? t("research.literature.readyLocalPdf") : t("research.literature.demoPaper") })}</p></div></div>;
  }
  const selected = rows.find((paper) => paper.id === selectedPaperId) ?? papers.find((paper) => paper.id === selectedPaperId) ?? rows[0] ?? papers[0];
  const columns = realSourceMode ? extractionMatrix.columns : [
    { id: "summary", name: t("research.literature.summary") },
    { id: "population", name: t("research.literature.population") },
    { id: "outcome", name: t("research.literature.outcome") },
  ];
  const gapColumns = columns.filter((column) => {
    const facet = facetByColumnId.get(column.id);
    return facet !== undefined && facet.state !== "complete";
  });
  const canReviewGaps = realSourceMode
    && !evidenceCoverageLoading
    && !evidenceCoverageError
    && (evidenceCoverage?.state === "available" || evidenceCoverage?.state === "reviewed")
    && gapColumns.length > 0;
  const gapModeActive = reviewGaps && canReviewGaps;
  const visibleColumns = gapModeActive
    ? gapColumns
    : columns;
  const cells = new Map<string, ExtractionMatrix["cells"][number]>(
    extractionMatrix.cells.map((cell) => [`${cell.sourceId}:${cell.columnId}`, cell]),
  );
  const eligibleBriefCellCount = extractionMatrix.cells.filter(
    (cell) => cell.reviewStatus === "confirmed" && cell.evidenceIds.length > 0,
  ).length;
  const extractionExportPapers: ExtractionExportPaper[] = papers.map((paper) => ({
    sourceId: paper.id,
    title: paper.title,
    authors: paper.authors,
    publicationYear: paper.source?.publicationDate?.slice(0, 4) ?? "",
  }));
  const hasPendingDrafts = Object.entries(drafts).some(
    ([key, value]) => value.trim() !== (cells.get(key)?.value ?? ""),
  );
  const screeningExportReason = screeningExportState === "loading"
    ? t("research.literature.exportExtractionCsvScreeningLoadingTitle")
    : screeningExportState === "error"
      ? t("research.literature.exportExtractionCsvScreeningErrorTitle")
      : screeningExportState === "saving"
        ? t("research.literature.exportExtractionCsvScreeningSavingTitle")
        : undefined;
  const exportDisabledReason = screeningExportReason
    ?? (loading
      ? t("research.literature.exportExtractionCsvLoadingTitle")
      : extractionMatrix.columns.length === 0
      ? t("research.literature.exportExtractionCsvNoColumnsTitle")
      : busyKey !== null || hasPendingDrafts
        ? t("research.literature.exportExtractionCsvSaveDraftTitle")
        : exporting
          ? t("research.literature.exportExtractionCsvExporting")
          : undefined);
  const exportDisabled = exportDisabledReason !== undefined;
  const exportExtractionCsv = async () => {
    if (!realSourceMode || screeningExportState !== "ready" || extractionMatrix.columns.length === 0 || loading || busyKey !== null || hasPendingDrafts || exporting) return;
    setExporting(true);
    try {
      let csv: string;
      try {
        csv = buildExtractionCsv(extractionExportPapers, extractionMatrix);
      } catch (error) {
        toast.error(t("research.literature.report.saveFeedback.failed", {
          filename: "spark-extraction-matrix.csv",
          error: error instanceof Error ? error.message : String(error),
        }));
        return;
      }
      await saveTextWithFeedback(
        "spark-extraction-matrix.csv",
        csv,
        "text/csv;charset=utf-8",
        {
          saved: (path) => t("research.literature.report.saveFeedback.saved", { path }),
          downloaded: (filename) => t("research.literature.report.saveFeedback.downloaded", { filename }),
          canceled: (filename) => t("research.literature.report.saveFeedback.canceled", { filename }),
          failed: (filename, error) => t("research.literature.report.saveFeedback.failed", { filename, error }),
        },
      );
    } finally {
      if (isCurrentProject()) setExporting(false);
    }
  };
  const createCitedBrief = async () => {
    if (!realSourceMode || eligibleBriefCellCount === 0 || briefBusy) return;
    setBriefBusy(true);
    setBriefError(null);
    try {
      await onCreateCitedBrief();
    } catch (error) {
      if (isCurrentProject()) {
        setBriefError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (isCurrentProject()) setBriefBusy(false);
    }
  };
  const isCurrentProject = () => mountedProjectRef.current === projectId
    && (!projectIdRef || projectIdRef.current === projectId)
    && requestGenerationRef.current === generation;
  const createColumn = async () => {
    const name = newColumnName.trim();
    if (!name || !realSourceMode || busyKey) return;
    setBusyKey("column"); setLocalError(null);
    try { await onCreateColumn({ name }); if (isCurrentProject()) setNewColumnName(""); }
    catch (error) { if (isCurrentProject()) setLocalError(error instanceof Error ? error.message : String(error)); }
    finally { if (isCurrentProject()) setBusyKey(null); }
  };
  const saveCell = async (paper: ResearchPaper, columnId: string, value: string) => {
    if (!realSourceMode || !paper.source || busyKey) return;
    const key = `${paper.id}:${columnId}`;
    const current = cells.get(key);
    const normalized = value.trim();
    if (!normalized) {
      if (!current) {
        setDrafts((currentDrafts) => { const next = { ...currentDrafts }; delete next[key]; return next; });
        return;
      }
      setBusyKey(key); setLocalError(null);
      try { await onDeleteCell(paper.id, columnId, current.rowVersion); if (isCurrentProject()) setDrafts((currentDrafts) => { const next = { ...currentDrafts }; delete next[key]; return next; }); }
      catch (error) { if (isCurrentProject()) { setDrafts((currentDrafts) => { const next = { ...currentDrafts }; delete next[key]; return next; }); setLocalError(error instanceof Error ? error.message : String(error)); } }
      finally { if (isCurrentProject()) setBusyKey(null); }
      return;
    }
    if (current?.value === normalized) {
      setDrafts((currentDrafts) => { const next = { ...currentDrafts }; delete next[key]; return next; });
      return;
    }
    setBusyKey(key); setLocalError(null);
    try { await onUpsertCell(paper.id, columnId, { value: normalized, reviewStatus: current?.reviewStatus ?? "unreviewed", evidenceIds: current?.evidenceIds ?? [], expectedVersion: current?.rowVersion ?? 0 }); if (isCurrentProject()) setDrafts((currentDrafts) => { const next = { ...currentDrafts }; delete next[key]; return next; }); }
    catch (error) { if (isCurrentProject()) { setDrafts((currentDrafts) => { const next = { ...currentDrafts }; delete next[key]; return next; }); setLocalError(error instanceof Error ? error.message : String(error)); } }
    finally { if (isCurrentProject()) setBusyKey(null); }
  };
  const confirmCell = async (paper: ResearchPaper, columnId: string) => {
    const current = cells.get(`${paper.id}:${columnId}`);
    if (!realSourceMode || !paper.source || !current || busyKey) return;
    const key = `${paper.id}:${columnId}`;
    setBusyKey(key); setLocalError(null);
    try { await onUpsertCell(paper.id, columnId, { value: current.value, reviewStatus: "confirmed", evidenceIds: current.evidenceIds, expectedVersion: current.rowVersion }); }
    catch (error) { if (isCurrentProject()) setLocalError(error instanceof Error ? error.message : String(error)); }
    finally { if (isCurrentProject()) setBusyKey(null); }
  };
  const toggleReviewGaps = () => {
    if (gapModeActive) {
      setReviewGaps(false);
      return;
    }
    const firstIncompleteColumnId = gapColumns[0]?.id;
    setReviewGaps(true);
    if (firstIncompleteColumnId) {
      requestAnimationFrame(() => columnHeaderRefs.current[firstIncompleteColumnId]?.focus());
    }
  };
  return (
    <div className="research-matrix-layout h-full min-h-0">
      <section className="flex min-w-0 flex-col bg-surface">
        <div className="research-toolbar"><button type="button" onClick={() => setSorted((value) => !value)} aria-pressed={sorted} className="compact-button secondary-button">{sorted ? t("research.literature.sortAz") : t("research.literature.sortRelevant")}</button><label className="search-field"><Search size={13} /><span className="sr-only">{t("research.literature.searchExtracted")}</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("research.literature.search")} /></label>{realSourceMode ? <form className="flex items-center gap-1" onSubmit={(event) => { event.preventDefault(); void createColumn(); }}><label className="sr-only" htmlFor="extraction-column-name">{t("research.literature.extractionColumnName")}</label><input id="extraction-column-name" className="h-7 w-28 rounded-input border border-border bg-surface px-2 text-caption" value={newColumnName} onChange={(event) => setNewColumnName(event.target.value)} placeholder={t("research.literature.extractionColumnName")} disabled={loading || busyKey === "column"} /><button type="submit" disabled={!newColumnName.trim() || loading || busyKey === "column"} className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"><Plus size={13} /> {t("research.literature.addColumn")}</button></form> : <button type="button" disabled title={t("research.literature.addColumnUnavailableTitle")} className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"><Plus size={13} /> {t("research.literature.addColumnUnavailable")}</button>}{canReviewGaps && <button type="button" onClick={toggleReviewGaps} aria-pressed={gapModeActive} className="compact-button secondary-button">{gapModeActive ? t("research.literature.showAllExtractionColumns") : t("research.literature.reviewExtractionGaps")}</button>}<span className="ml-auto text-caption text-muted">{t("research.literature.includedPapersColumns", { papers: rows.length, columns: visibleColumns.length })}</span>{realSourceMode && <><button type="button" onClick={() => void exportExtractionCsv()} aria-disabled={exportDisabled || undefined} aria-describedby={exportDisabled ? "extraction-export-reason" : undefined} className={cn("compact-button secondary-button", exportDisabled && "cursor-not-allowed opacity-40")}>{exporting ? t("research.literature.exportExtractionCsvExporting") : t("research.literature.exportExtractionCsv")}</button>{exportDisabled && <span id="extraction-export-reason" className="sr-only">{exportDisabledReason}</span>}</>}{realSourceMode && <button type="button" onClick={() => void createCitedBrief()} disabled={eligibleBriefCellCount === 0 || briefBusy} title={eligibleBriefCellCount === 0 ? t("research.literature.citedBriefEmpty") : undefined} className="compact-button primary-button disabled:cursor-not-allowed disabled:opacity-40">{briefBusy ? t("research.literature.citedBriefGenerating") : t("research.literature.generateCitedBrief")}</button>}<button type="button" onClick={onContinue} className="compact-button secondary-button">{t("research.literature.readSource")}</button></div>
        {realSourceMode && <EvidenceCoverageSummary coverage={evidenceCoverage} loading={evidenceCoverageLoading} error={evidenceCoverageError} onRetry={onRetryEvidenceCoverage} />}
        {(persistenceError || localError) && <div role="alert" className="border-b border-error/25 bg-error/5 px-4 py-2 text-caption text-error">{t("research.literature.extractionSaveFailed", { error: localError ?? persistenceError ?? "" })}</div>}
        {briefError && <div role="alert" className="border-b border-error/25 bg-error/5 px-4 py-2 text-caption text-error">{t("research.literature.citedBriefFailed", { error: briefError })}</div>}
        {realSourceMode && eligibleBriefCellCount === 0 && <div role="status" className="border-b border-border-faint bg-surface-2 px-4 py-2 text-caption text-muted">{t("research.literature.citedBriefEmpty")}</div>}
        <div className="min-h-0 flex-1 overflow-auto">
          {/* eslint-disable-next-line i18next/no-literal-string -- canonical extraction badge tone discriminant */}
          <table className="extraction-table min-w-[920px]"><thead><tr><th>{t("research.literature.paper")}</th>{visibleColumns.map((column) => { const facet = facetByColumnId.get(column.id); return <th ref={(node) => { columnHeaderRefs.current[column.id] = node; }} key={column.id} tabIndex={-1} className="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30">{column.name}{facet && facet.state !== "complete" && <span className="mt-1 block font-normal text-muted">{t("research.literature.extractionGapCounts", { missing: facet.missingSourceCount, awaiting: facet.awaitingConfirmationSourceCount, unverified: facet.unverifiedSourceCount })}</span>}</th>; })}</tr></thead><tbody>{rows.map((paper) => { const rowCells = columns.map((column) => cells.get(`${paper.id}:${column.id}`)).filter((cell): cell is NonNullable<typeof cell> => cell !== undefined); const rowBadge = rowCells.length === 0 ? { label: t("research.literature.notExtracted"), tone: "warn" as const } : rowCells.length === columns.length && rowCells.every((cell) => cell.reviewStatus === "confirmed") ? { label: t("research.literature.humanConfirmed"), tone: "ok" as const } : { label: t("research.literature.unreviewed"), tone: "warn" as const }; return <tr key={paper.id} tabIndex={0} className={cn(selected.id === paper.id && "selected-row")} onClick={() => onSelectPaper(paper.id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelectPaper(paper.id); } }}><td><PaperIdentity paper={paper} /><StateBadge label={realSourceMode ? rowBadge.label : t("research.literature.extracted")} tone={realSourceMode ? rowBadge.tone : "ok"} /></td>{visibleColumns.map((column) => { const cell = cells.get(`${paper.id}:${column.id}`); const fallback = column.id === "summary" ? paper.summary : column.id === "population" ? paper.population : paper.outcome; const key = `${paper.id}:${column.id}`; return <td key={column.id}>{realSourceMode ? <div className="min-w-[160px]"><label className="sr-only" htmlFor={`extraction-${paper.id}-${column.id}`}>{`${paper.title}: ${column.name}`}</label><textarea id={`extraction-${paper.id}-${column.id}`} value={drafts[key] ?? cell?.value ?? ""} onChange={(event) => setDrafts((currentDrafts) => ({ ...currentDrafts, [key]: event.target.value }))} onBlur={(event) => void saveCell(paper, column.id, event.target.value)} disabled={loading || busyKey === key} className="min-h-12 w-full resize-y rounded-input border border-transparent bg-transparent px-1 py-1 text-xs focus:border-accent focus:bg-surface focus:outline-none disabled:opacity-50" placeholder={t("research.literature.notExtracted")} /><div className="mt-1 flex items-center gap-1"><StateBadge label={cell?.reviewStatus === "confirmed" ? t("research.literature.confirmed") : t("research.literature.unreviewed")} tone={cell?.reviewStatus === "confirmed" ? "ok" : "warn"} />{cell && <button type="button" onClick={() => void confirmCell(paper, column.id)} disabled={cell.reviewStatus === "confirmed" || busyKey === key} className="text-caption text-accent disabled:opacity-50">{t("research.literature.confirm")}</button>}</div></div> : fallback}</td>; })}</tr>; })}</tbody></table>
        </div>
        <div className="border-t border-border px-4 py-2 text-caption text-muted">{t("research.literature.horizontalScrollHint")}</div>
      </section>
      <aside className="matrix-inspector"><p className="text-muted">{t("research.literature.outcome")}</p><h2>{selected.outcome}</h2><hr /><strong>{t("research.literature.evidenceStatus")}</strong>{selected.quote ? <blockquote>{selected.quote}<small>{t("research.literature.resultsSupportingQuote")}</small></blockquote> : <div className="unverified-quote">{t("research.literature.noVerifiedExtractionEvidence")}</div>}<strong>{t("research.literature.whyThisAnswer")}</strong><p>{t("research.literature.importedExtractionBoundary")}</p><strong>{t("research.literature.source")}</strong><p className="text-muted">{selected.authors} · {selected.year}</p></aside>
    </div>
  );
}

function ReaderSurface({
  paper,
  sources,
  selectedPaperId,
  pageIndex,
  evidence,
  includedInSynthesis,
  synthesisUnavailableReason,
  realSourceMode,
  extractionMatrix,
  onCreateEvidenceSpan,
  onUpsertExtractionCell,
  onEvidenceSaved,
  memoryController,
  onPageChange,
  onSelectSource,
  onContinue,
}: {
  paper: ResearchPaper;
  sources: ResearchPaper[];
  selectedPaperId: string;
  pageIndex: number;
  evidence: ReportCitation | EvidenceSpan | null;
  includedInSynthesis: boolean;
  synthesisUnavailableReason: string | null;
  realSourceMode: boolean;
  extractionMatrix: ExtractionMatrix;
  onCreateEvidenceSpan: (
    sourceId: string,
    input: CreateExactEvidenceSpanInput,
  ) => Promise<EvidenceSpan>;
  onUpsertExtractionCell: (
    sourceId: string,
    columnId: string,
    input: UpsertExtractionCellInput,
  ) => Promise<unknown>;
  onEvidenceSaved: (evidence: EvidenceSpan) => void;
  memoryController?: ResearchMemoryPanelController;
  onPageChange: (pageIndex: number) => void;
  onSelectSource: (id: string) => void;
  onContinue: () => void;
}) {
  const { t } = useTranslation("pages");
  const [documentTab, setDocumentTab] = useState<"pdf" | "summary">("pdf");
  const [inspectorTab, setInspectorTab] = useState<"ask" | "sources">("ask");
  const [quoteText, setQuoteText] = useState("");
  const [columnId, setColumnId] = useState("");
  const [savingQuote, setSavingQuote] = useState(false);
  const [quoteError, setQuoteError] = useState<string | null>(null);
  const [quoteSaved, setQuoteSaved] = useState(false);
  const [rememberingEvidence, setRememberingEvidence] = useState(false);
  const [rememberEvidenceState, setRememberEvidenceState] = useState<
    "candidate" | "already-remembered" | null
  >(null);
  const [rememberEvidenceError, setRememberEvidenceError] = useState<string | null>(null);
  const rememberControllerRef = useRef<AbortController | null>(null);
  const memoryIdentity = `${memoryController?.projectId ?? ""}:${memoryController?.workflowId ?? ""}`;
  const memoryIdentityRef = useRef(memoryIdentity);
  memoryIdentityRef.current = memoryIdentity;
  useEffect(() => {
    rememberControllerRef.current?.abort();
    setRememberingEvidence(false);
    setRememberEvidenceState(null);
    setRememberEvidenceError(null);
    return () => rememberControllerRef.current?.abort();
  }, [evidence?.id, memoryIdentity]);
  const moveDocumentTab = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    // eslint-disable-next-line i18next/no-literal-string -- internal reader tab discriminants
    const next = documentTab === "pdf" ? "summary" : "pdf";
    setDocumentTab(next);
    requestAnimationFrame(() => document.getElementById(`reader-${next}-tab`)?.focus());
  };
  const moveInspectorTab = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    // eslint-disable-next-line i18next/no-literal-string -- internal reader tab discriminants
    const next = inspectorTab === "ask" ? "sources" : "ask";
    setInspectorTab(next);
    requestAnimationFrame(() => document.getElementById(`reader-${next}-tab`)?.focus());
  };
  // eslint-disable-next-line i18next/no-literal-string -- canonical ingestion status discriminant
  const readySourceId = paper.source?.ingestionStatus === "ready" ? paper.source.id : null;
  const pdf = useSourcePdfBlob(readySourceId, pageIndex);
  const pageCount = Math.max(1, paper.pageCount ?? 1);
  const pageLabel = String(pageIndex + 1);
  const changePage = (nextPageIndex: number) => {
    onPageChange(Math.min(pageCount - 1, Math.max(0, nextPageIndex)));
  };
  const source = paper.source;
  const canSaveEvidence =
    source?.ingestionStatus === "ready"
    && Boolean(source.pageManifestHash)
    && quoteText.trim().length >= 12
    && (extractionMatrix.columns.length === 0 || Boolean(columnId))
    && !savingQuote;
  const saveExactQuote = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!source || source.ingestionStatus !== "ready" || !source.pageManifestHash) return;
    setSavingQuote(true);
    setQuoteError(null);
    setQuoteSaved(false);
    try {
      const savedEvidence = await onCreateEvidenceSpan(source.id, {
        pageIndex,
        quoteText,
        expectedSourceContentHash: source.contentHash,
        expectedPageManifestHash: source.pageManifestHash,
      });
      onEvidenceSaved(savedEvidence);
      if (columnId) {
        const currentCell = extractionMatrix.cells.find(
          (cell) => cell.sourceId === source.id && cell.columnId === columnId,
        );
        await onUpsertExtractionCell(source.id, columnId, {
          value: savedEvidence.text,
          evidenceIds: [savedEvidence.id],
          reviewStatus: "confirmed",
          expectedVersion: currentCell?.rowVersion ?? 0,
        });
      }
      setQuoteText("");
      setQuoteSaved(true);
    } catch (error) {
      setQuoteError(error instanceof Error ? error.message : String(error));
    } finally {
      setSavingQuote(false);
    }
  };
  const evidenceClaim =
    evidence && "claim" in evidence
      ? evidence.claim
      : t("research.literature.reader.saveExactQuote");
  const evidenceRelationship =
    evidence && "relationship" in evidence
      ? t(`research.evidenceRelationship.${evidence.relationship}`)
      : t("research.literature.reader.exactQuote");
  const canRememberEvidence = Boolean(
    memoryController?.projectId
      && memoryController.workflowId
      && source?.ingestionStatus === "ready"
      && source.contentHash
      && evidence?.verified
      && evidence.sourceId === source.id
      && evidence.quoteHash,
  );
  const rememberEvidence = async () => {
    if (
      !canRememberEvidence
      || !memoryController?.projectId
      || !memoryController.workflowId
      || !source
      || !evidence
    ) return;
    rememberControllerRef.current?.abort();
    const controller = new AbortController();
    rememberControllerRef.current = controller;
    const requestIdentity = memoryIdentity;
    setRememberingEvidence(true);
    setRememberEvidenceError(null);
    setRememberEvidenceState(null);
    try {
      const response = await scienceCore.createEvidenceMemoryCandidate(
        memoryController.projectId,
        memoryController.workflowId,
        {
          evidenceId: evidence.id,
          expectedSourceContentHash: source.contentHash,
          expectedQuoteHash: evidence.quoteHash,
        },
        {
          idempotencyKey: `remember-evidence-${evidence.id}-${evidence.quoteHash}`,
          signal: controller.signal,
        },
      );
      if (!controller.signal.aborted && memoryIdentityRef.current === requestIdentity) {
        setRememberEvidenceState(
          response.outcome === "already-remembered"
            ? "already-remembered"
            : "candidate",
        );
        await memoryController.refresh();
      }
    } catch (error) {
      if (!controller.signal.aborted && memoryIdentityRef.current === requestIdentity) {
        setRememberEvidenceError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (
        rememberControllerRef.current === controller
        && memoryIdentityRef.current === requestIdentity
      ) {
        rememberControllerRef.current = null;
        setRememberingEvidence(false);
      }
    }
  };
  return (
    <div className="pdf-reader-layout h-full min-h-0">
      <section className="flex min-w-0 flex-col bg-surface-2">
        <div className="reader-toolbar">
          <div className="reader-tabs" role="tablist" aria-label={t("research.literature.reader.contentAria")}>
            <button id="reader-pdf-tab" type="button" tabIndex={documentTab === "pdf" ? 0 : -1} className={cn(documentTab === "pdf" && "active")} role="tab" aria-selected={documentTab === "pdf"} aria-controls="reader-pdf-panel" onClick={() => setDocumentTab("pdf")} onKeyDown={moveDocumentTab}>{t("research.literature.reader.pdfFile")}</button>
            <button id="reader-summary-tab" type="button" tabIndex={documentTab === "summary" ? 0 : -1} className={cn(documentTab === "summary" && "active")} role="tab" aria-selected={documentTab === "summary"} aria-controls="reader-summary-panel" onClick={() => setDocumentTab("summary")} onKeyDown={moveDocumentTab}>{t("research.literature.reader.summary")}</button>
          </div>
          <button type="button" disabled title={t("research.literature.reader.pdfSearchTitle")} className="icon-button disabled:cursor-not-allowed disabled:opacity-40" aria-label={t("research.literature.reader.pdfSearchAria")}><Search size={14} /></button>
          <button
            type="button"
            className="icon-button disabled:cursor-not-allowed disabled:opacity-40"
            aria-label={t("research.literature.reader.previousPage")}
            disabled={pageIndex <= 0}
            onClick={() => changePage(pageIndex - 1)}
          >
            <ChevronLeft size={14} />
          </button>
          <label className="sr-only" htmlFor="reader-page-number">
            {t("research.literature.reader.pageNumber")}
          </label>
          <input
            id="reader-page-number"
            className="page-field"
            type="number"
            min={1}
            max={pageCount}
            value={pageIndex + 1}
            onChange={(event) => changePage(Number(event.target.value) - 1)}
          />
          <span className="text-caption text-muted">/ {paper.pageCount ?? t("research.literature.unknown")}</span>
          <button
            type="button"
            className="icon-button disabled:cursor-not-allowed disabled:opacity-40"
            aria-label={t("research.literature.reader.nextPage")}
            disabled={pageIndex >= pageCount - 1}
            onClick={() => changePage(pageIndex + 1)}
          >
            <ChevronRight size={14} />
          </button>
          <button type="button" disabled title={t("research.literature.reader.zoomTitle")} className="icon-button disabled:cursor-not-allowed disabled:opacity-40" aria-label={t("research.literature.reader.zoomOutAria")}><ZoomOut size={14} /></button>
          <span className="text-caption">125%</span>
          <button type="button" disabled title={t("research.literature.reader.zoomTitle")} className="icon-button disabled:cursor-not-allowed disabled:opacity-40" aria-label={t("research.literature.reader.zoomInAria")}><ZoomIn size={14} /></button>
          <button type="button" disabled title={t("research.literature.reader.fullScreenTitle")} className="icon-button disabled:cursor-not-allowed disabled:opacity-40" aria-label={t("research.literature.reader.fullScreenAria")}><Maximize2 size={14} /></button>
        </div>
        <div className="reader-body">
          {documentTab === "pdf" ? <>
            <aside className="thumbnail-rail" aria-label={t("research.literature.reader.currentPageAria")}><div className="selected-thumbnail"><span></span><small>{pageLabel}</small></div></aside>
            <div id="reader-pdf-panel" role="tabpanel" aria-labelledby="reader-pdf-tab" className="real-pdf-stage">
              {pdf.loading && <div role="status" className="reader-document-state"><Loader2 size={18} className="animate-spin text-accent" /> {t("research.literature.reader.loadingPdf")}</div>}
              {pdf.error && <div role="alert" className="reader-document-state text-error">{t("research.literature.reader.pdfError", { error: pdf.error })}</div>}
              {paper.source?.ingestionStatus !== "ready" && <div className="reader-document-state"><CircleAlert size={18} /><strong>{paper.ingestionLabel}</strong><span>{t("research.literature.reader.sourceNotReady")}</span></div>}
              {pdf.url && <iframe className="real-pdf-frame" src={pdf.url} title={t("research.literature.reader.pdfTitle", { title: paper.title, page: pageLabel })} />}
            </div>
          </> : (
            <article id="reader-summary-panel" role="tabpanel" aria-labelledby="reader-summary-tab" className="paper-page"><h1>{paper.title}</h1><p className="paper-authors">{paper.authors} · {paper.journal} · {paper.year}</p><h2>{t("research.literature.reader.currentSourceSummary")}</h2><p>{paper.summary}</p><h2>{t("research.literature.reader.outcome")}</h2><p>{paper.outcome}</p>{paper.quote && <blockquote>{paper.quote}</blockquote>}</article>
          )}
        </div>
      </section>
      <aside className="reader-inspector">
        <div className="reader-tabs" role="tablist" aria-label={t("research.literature.reader.inspectorAria")}>
          <button id="reader-ask-tab" type="button" tabIndex={inspectorTab === "ask" ? 0 : -1} className={cn(inspectorTab === "ask" && "active")} role="tab" aria-selected={inspectorTab === "ask"} aria-controls="reader-ask-panel" onClick={() => setInspectorTab("ask")} onKeyDown={moveInspectorTab}>{t("research.literature.reader.ask")}</button>
          <button id="reader-sources-tab" type="button" tabIndex={inspectorTab === "sources" ? 0 : -1} className={cn(inspectorTab === "sources" && "active")} role="tab" aria-selected={inspectorTab === "sources"} aria-controls="reader-sources-panel" onClick={() => setInspectorTab("sources")} onKeyDown={moveInspectorTab}>{t("research.literature.reader.sources", { count: sources.length })}</button>
          <button type="button" disabled title={t("research.literature.reader.notesUnavailableTitle")} role="tab" aria-disabled="true">{t("research.literature.reader.notesUnavailable")}</button>
        </div>
        {inspectorTab === "ask" ? (
          <div id="reader-ask-panel" role="tabpanel" aria-labelledby="reader-ask-tab">
            <h2>{t("research.literature.reader.reviewImported")}</h2>
            {realSourceMode && source?.ingestionStatus === "ready" && (
              <form className="reader-evidence-form" onSubmit={(event) => void saveExactQuote(event)}>
                <label htmlFor="reader-exact-quote">{t("research.literature.reader.saveExactQuote")}</label>
                <p className="text-caption text-muted">
                  {t("research.literature.reader.pasteExactQuoteHint", { page: pageIndex + 1 })}
                </p>
                <textarea
                  id="reader-exact-quote"
                  value={quoteText}
                  onChange={(event) => {
                    setQuoteText(event.target.value);
                    setQuoteError(null);
                    setQuoteSaved(false);
                  }}
                  disabled={savingQuote}
                  placeholder={t("research.literature.reader.exactQuotePlaceholder")}
                  className="min-h-20 w-full resize-y rounded-input border border-border bg-surface px-2 py-2 text-xs focus:border-accent focus:outline-none disabled:opacity-50"
                />
                {extractionMatrix.columns.length > 0 ? (
                  <>
                    <label htmlFor="reader-extraction-column">
                      {t("research.literature.reader.confirmInColumn")}
                    </label>
                    <select
                      id="reader-extraction-column"
                      value={columnId}
                      onChange={(event) => setColumnId(event.target.value)}
                      disabled={savingQuote}
                      className="h-8 w-full rounded-input border border-border bg-surface px-2 text-xs focus:border-accent focus:outline-none disabled:opacity-50"
                    >
                      <option value="">{t("research.literature.reader.chooseColumn")}</option>
                      {extractionMatrix.columns.map((column) => (
                        <option key={column.id} value={column.id}>{column.name}</option>
                      ))}
                    </select>
                  </>
                ) : (
                  <p className="text-caption text-muted">
                    {t("research.literature.reader.createColumnFirst")}
                  </p>
                )}
                {quoteError && <div role="alert" className="text-caption text-error">{t("research.literature.reader.saveQuoteFailed", { error: quoteError })}</div>}
                {quoteSaved && <div role="status" className="text-caption text-accent">{t("research.literature.reader.quoteSaved")}</div>}
                <button type="submit" disabled={!canSaveEvidence} className="compact-button primary-button disabled:cursor-not-allowed disabled:opacity-40">
                  {savingQuote ? t("research.literature.reader.savingQuote") : t("research.literature.reader.saveAndConfirm")}
                </button>
              </form>
            )}
            {realSourceMode ? (evidence && evidence.sourceId === paper.id ? <>
              <div className="reader-question">{evidenceClaim}</div>
              <div className="reader-answer">
                <StateBadge label={t("research.literature.reader.locatedLocally")} tone={STATUS_TONES.ok} />
                <blockquote>{evidence.text}</blockquote>
                <p className="text-caption text-muted">{t("research.literature.reader.evidenceMetadata", { page: evidence.pageLabel ?? evidence.pageIndex + 1, relationship: evidenceRelationship, method: evidence.extractionMethod })}</p>
                {memoryController && (
                  <div className="mt-2 border-t border-border-faint pt-2">
                    <button
                      type="button"
                      onClick={() => void rememberEvidence()}
                      disabled={!canRememberEvidence || rememberingEvidence}
                      className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {rememberingEvidence
                        ? t("research.literature.reader.rememberingEvidence")
                        : t("research.literature.reader.rememberEvidence")}
                    </button>
                    {rememberEvidenceState === "candidate" && (
                      <p role="status" className="mt-1 text-caption text-accent">
                        {t("research.literature.reader.rememberEvidenceCandidate")}
                      </p>
                    )}
                    {rememberEvidenceState === "already-remembered" && (
                      <p role="status" className="mt-1 text-caption text-muted">
                        {t("research.literature.reader.evidenceAlreadyRemembered")}
                      </p>
                    )}
                    {rememberEvidenceError && (
                      <p role="alert" className="mt-1 break-words text-caption text-error">
                        {t("research.literature.reader.rememberEvidenceFailed", {
                          error: rememberEvidenceError,
                        })}
                      </p>
                    )}
                  </div>
                )}
              </div>
            </> : <div className="unverified-quote">{t("research.literature.reader.noEvidenceSelected")}</div>) : null}
          </div>
        ) : (
          <div id="reader-sources-panel" role="tabpanel" aria-labelledby="reader-sources-tab" className="source-list">
            {sources.map((source) => <button key={source.id} type="button" onClick={() => onSelectSource(source.id)} className={cn("source-row", source.id === selectedPaperId && "selected-source")} title={source.title}><FileText size={15} /><span><strong>{source.title}</strong><small>{t("research.literature.reader.sourceMetadata", { authors: source.authors, pages: source.pageCount ?? t("research.literature.reader.unknown"), id: source.id })}</small></span><StateBadge label={source.ingestionLabel} tone={source.source?.ingestionStatus === "failed" ? STATUS_TONES.error : source.source?.ingestionStatus === "ready" ? STATUS_TONES.ok : STATUS_TONES.neutral} /><span className="locate-button">{source.source?.ingestionStatus === "ready" ? t("research.literature.reader.locateInPdf") : t("research.literature.reader.unavailable")}</span></button>)}
          </div>
        )}
        <div className="reader-composer">
          <span className="min-w-0">
            <span className="block">{t("research.literature.reader.localReviewBoundary")}</span>
            {includedInSynthesis && synthesisUnavailableReason && (
              <span id="reader-synthesis-unavailable" className="mt-1 block text-caption text-muted">
                {synthesisUnavailableReason}
              </span>
            )}
          </span>
          <button
            type="button"
            onClick={onContinue}
            disabled={includedInSynthesis && Boolean(synthesisUnavailableReason)}
            aria-describedby={
              includedInSynthesis && synthesisUnavailableReason
                ? "reader-synthesis-unavailable"
                : undefined
            }
            title={
              includedInSynthesis
                ? synthesisUnavailableReason ?? undefined
                : undefined
            }
            className="compact-button primary-button disabled:cursor-not-allowed disabled:opacity-40"
          >
            {includedInSynthesis
              ? t("research.literature.reader.startSynthesis")
              : t("research.literature.reader.reviewEligibility")}
          </button>
        </div>
      </aside>
    </div>
  );
}

/* eslint-disable i18next/no-literal-string -- persisted report controls are canonical English contract labels pending the existing research locale refresh */
interface ReportSurfaceProps {
  mode: "review" | "generating";
  result: ResearchWorkflowResult | null;
  citations: ReportCitation[];
  reportExport: VerifiedReportExport;
  persistentReportDraft: boolean;
  draft: ReportDraftRecord | null;
  draftLoading: boolean;
  draftMutating: boolean;
  draftError: string | null;
  sourceChangedCitationCount: number;
  needsReviewCitationCount: number;
  selectedCitation: ReportCitation | null;
  realSourceMode: boolean;
  evidenceCoverage: WorkflowEvidenceCoverage | null;
  evidenceCoverageLoading: boolean;
  evidenceCoverageError: string | null;
  onRetryEvidenceCoverage: () => void;
  onRetryDraft: () => void;
  onSaveDraft: (contentMarkdown: string) => Promise<ReportDraftRecord>;
  onReviewDraft: (
    citationRebases: ReportCitationRebaseInput[],
  ) => Promise<ReportDraftRecord>;
  onExportDraft: () => Promise<ReportDraftExport>;
  onCitationOpen: (citation: ReportCitation) => void;
  onCitationClose: () => void;
  onModeChange: (mode: "review" | "generating") => void;
  onOpenPdf: (citation: ReportCitation) => void;
}

function ReportSurface(props: ReportSurfaceProps) {
  const {
    mode,
    citations,
    reportExport,
    persistentReportDraft,
    draft,
    draftLoading,
    draftMutating,
    draftError,
    sourceChangedCitationCount,
    needsReviewCitationCount,
    selectedCitation,
    realSourceMode,
    evidenceCoverage,
    evidenceCoverageLoading,
    evidenceCoverageError,
    onRetryEvidenceCoverage,
    onRetryDraft,
    onSaveDraft,
    onReviewDraft,
    onExportDraft,
    onCitationOpen,
    onCitationClose,
    onModeChange,
    onOpenPdf,
  } = props;
  const { t } = useTranslation("pages");
  const [editing, setEditing] = useState(false);
  const [editorValue, setEditorValue] = useState("");
  const [editorError, setEditorError] = useState<string | null>(null);
  const [reportFormat, setReportFormat] =
    useState<ReportDocumentFormat>("markdown");
  const [citationFormat, setCitationFormat] = useState<"csv" | "json" | "bibtex">("csv");
  const citationTriggerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!editing) setEditorValue(draft?.contentMarkdown ?? "");
  }, [draft?.contentMarkdown, draft?.id, draft?.revision, editing]);

  const citationRebasePlan = useMemo(
    () =>
      draft
        ? buildReportCitationRebasePlan(draft.contentMarkdown, reportExport)
        : null,
    [draft, reportExport],
  );

  if (!realSourceMode || !persistentReportDraft) {
    return <LegacyReportSurface {...props} />;
  }
  if (mode === "generating") {
    return (
      <GeneratingReport
        realSourceMode
        onShowReview={() => onModeChange("review")}
      />
    );
  }

  const dirty = draft != null && editorValue !== draft.contentMarkdown;
  const exportBlocked =
    !draft ||
    draft.status === "needs-review" ||
    dirty ||
    draftMutating ||
    !reportExport.ok;
  const exportBlockReason = !draft
    ? "The persisted report draft is unavailable."
    : draft.status === "needs-review"
      ? "The source or evidence base changed. Review changes before exporting."
      : dirty
        ? "Save or cancel the current edits before exporting."
        : !reportExport.ok
          ? t(`research.literature.report.exportBlocked.${reportExport.reason}`)
          : null;
  const uniqueCitations = citations.filter(
    (citation, index, all) =>
      all.findIndex(
        (candidate) =>
          canonicalEvidenceKey(candidate) === canonicalEvidenceKey(citation),
      ) === index,
  );
  const openCitation = (citation: ReportCitation, trigger: HTMLElement) => {
    citationTriggerRef.current = trigger;
    onCitationOpen(citation);
  };
  const closeCitation = () => {
    citationTriggerRef.current?.focus();
    onCitationClose();
  };
  const saveEdit = async () => {
    if (!draft || !dirty || draftMutating) return;
    setEditorError(null);
    try {
      const updated = await onSaveDraft(editorValue);
      setEditorValue(updated.contentMarkdown);
      setEditing(false);
    } catch (error) {
      setEditorError(error instanceof Error ? error.message : String(error));
    }
  };
  const reviewChanges = async () => {
    if (!citationRebasePlan?.ok || draftMutating) return;
    setEditorError(null);
    try {
      await onReviewDraft(citationRebasePlan.citationRebases);
    } catch (error) {
      setEditorError(error instanceof Error ? error.message : String(error));
    }
  };
  const copyReport = async () => {
    try {
      const exported = await onExportDraft();
      await copyText(exported.contentMarkdown);
      toast.success(t("research.literature.report.reportCopied"));
    } catch (error) {
      toast.error(
        t("research.literature.report.copyFailed", {
          error: error instanceof Error ? error.message : String(error),
        }),
      );
    }
  };
  const saveReport = async () => {
    try {
      const exported = await onExportDraft();
      const outcome = await exportReportDocument(
        reportFormat,
        exported.contentMarkdown,
        {
          saved: (path) =>
            t("research.literature.report.saveFeedback.saved", { path }),
          downloaded: (filename) =>
            t("research.literature.report.saveFeedback.downloaded", { filename }),
          canceled: (filename) =>
            t("research.literature.report.saveFeedback.canceled", { filename }),
          failed: (filename, error) =>
            t("research.literature.report.saveFeedback.failed", {
              filename,
              error,
            }),
        },
      );
      if (outcome === "print-opened") {
        toast.info(t("research.literature.report.printOpened"));
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  };
  const saveCitations = async () => {
    if (!reportExport.ok) return;
    try {
      await onExportDraft();
      const exports = {
        csv: {
          filename: "spark-verified-citations.csv",
          text: reportExport.citationsCsv,
          mime: "text/csv",
        },
        json: {
          filename: "spark-verified-citations.json",
          text: reportExport.citationsJson,
          mime: "application/json",
        },
        bibtex: {
          filename: "spark-verified-citations.bib",
          text: reportExport.citationsBibtex,
          mime: "application/x-bibtex",
        },
      };
      const selected = exports[citationFormat];
      await saveTextWithFeedback(selected.filename, selected.text, selected.mime, {
        saved: (path) =>
          t("research.literature.report.saveFeedback.saved", { path }),
        downloaded: (filename) =>
          t("research.literature.report.saveFeedback.downloaded", { filename }),
        canceled: (filename) =>
          t("research.literature.report.saveFeedback.canceled", { filename }),
        failed: (filename, error) =>
          t("research.literature.report.saveFeedback.failed", {
            filename,
            error,
          }),
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <div className="report-layout h-full min-h-0">
      <section className="relative flex min-w-0 flex-col bg-surface">
        <div className="research-toolbar flex-wrap">
          {!editing ? (
            <button
              type="button"
              onClick={() => {
                setEditorError(null);
                setEditorValue(draft?.contentMarkdown ?? "");
                setEditing(true);
              }}
              disabled={!draft || draftLoading || draftMutating}
              className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"
            >
              {t("research.literature.report.editorEdit")}
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={() => void saveEdit()}
                disabled={!dirty || draftMutating}
                className="compact-button primary-button disabled:cursor-not-allowed disabled:opacity-40"
              >
                {draftMutating
                  ? t("research.literature.report.editorSaving")
                  : t("research.literature.report.editorSave")}
              </button>
              <button
                type="button"
                onClick={() => {
                  setEditorValue(draft?.contentMarkdown ?? "");
                  setEditorError(null);
                  setEditing(false);
                }}
                disabled={draftMutating}
                className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"
              >
                {t("research.literature.report.editorCancel")}
              </button>
              <span role="status" className="text-caption text-muted">
                {dirty
                  ? t("research.literature.report.editorUnsaved")
                  : t("research.literature.report.editorNoChanges")}
              </span>
            </>
          )}
          {draft?.status === "needs-review" && !editing && (
            <button
              type="button"
              onClick={() => void reviewChanges()}
              disabled={draftMutating || !citationRebasePlan?.ok}
              title={
                citationRebasePlan && !citationRebasePlan.ok
                  ? citationRebasePlan.issues.join(" ")
                  : undefined
              }
              className="compact-button primary-button disabled:cursor-not-allowed disabled:opacity-40"
            >
              {draftMutating
                ? t("research.literature.report.editorReviewing")
                : t("research.literature.report.editorReviewChanges")}
            </button>
          )}
          <button
            type="button"
            onClick={() => void copyReport()}
            disabled={exportBlocked}
            title={exportBlockReason ?? undefined}
            className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"
          >
            {t("research.literature.report.copyReport")}
          </button>
          <div
            className="report-export-group"
            role="group"
            aria-label={t("research.literature.report.documentExportGroup")}
          >
            <span className="report-export-group-label" aria-hidden="true">
              {t("research.literature.report.documentExportLabel")}
            </span>
            <label className="sr-only" htmlFor="persistent-report-export-format">
              {t("research.literature.report.reportFormat")}
            </label>
            <select
              id="persistent-report-export-format"
              value={reportFormat}
              onChange={(event) =>
                setReportFormat(event.target.value as ReportDocumentFormat)
              }
              disabled={exportBlocked}
              title={exportBlockReason ?? undefined}
              className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"
            >
              <option value="markdown">
                {t("research.literature.report.reportFormats.markdown")}
              </option>
              <option value="docx">
                {t("research.literature.report.reportFormats.docx")}
              </option>
              <option value="pdf">
                {t("research.literature.report.reportFormats.pdf")}
              </option>
            </select>
            <button
              type="button"
              onClick={() => void saveReport()}
              disabled={exportBlocked}
              title={exportBlockReason ?? undefined}
              className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"
            >
              {t("research.literature.report.exportReport")}
            </button>
          </div>
          <div
            className="report-export-group"
            role="group"
            aria-label={t("research.literature.report.citationExportGroup")}
          >
            <span className="report-export-group-label" aria-hidden="true">
              {t("research.literature.report.citationExportLabel")}
            </span>
            <label className="sr-only" htmlFor="persistent-citation-export-format">
              {t("research.literature.report.citationFormat")}
            </label>
            <select
              id="persistent-citation-export-format"
              value={citationFormat}
              onChange={(event) =>
                setCitationFormat(event.target.value as "csv" | "json" | "bibtex")
              }
              disabled={exportBlocked}
              title={exportBlockReason ?? undefined}
              className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"
            >
              <option value="csv">
                {t("research.literature.report.formats.csv")}
              </option>
              <option value="json">
                {t("research.literature.report.formats.json")}
              </option>
              <option value="bibtex">
                {t("research.literature.report.formats.bibtex")}
              </option>
            </select>
            <button
              type="button"
              onClick={() => void saveCitations()}
              disabled={exportBlocked}
              title={exportBlockReason ?? undefined}
              className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"
            >
              {t("research.literature.report.exportCitations")}
            </button>
          </div>
          {draft && (
            <span className="ml-auto text-caption text-muted">
              {t("research.literature.report.revisionStatus", {
                revision: draft.revision,
                status:
                  draft.status === "needs-review"
                    ? t("research.literature.report.status.needsReview")
                    : draft.status === "reviewed"
                      ? t("research.literature.report.status.reviewed")
                      : t("research.literature.report.status.draft"),
              })}
            </span>
          )}
        </div>
        {(draftError || editorError) && (
          <div
            role="alert"
            className="flex items-center gap-3 border-b border-error/25 bg-error/5 px-4 py-2 text-caption text-text"
          >
            <span className="min-w-0 flex-1">
              {editorError ?? draftError}
            </span>
            <button
              type="button"
              onClick={onRetryDraft}
              className="compact-button secondary-button"
            >
              {t("research.literature.report.reload")}
            </button>
          </div>
        )}
        {draft?.status === "needs-review" && (
          <div
            role="status"
            className="border-b border-warn/25 bg-warn/5 px-4 py-3 text-caption text-text"
          >
            <p>
              The saved source or evidence snapshot changed. Export is blocked until
              you review and rebase this draft.
            </p>
            {citationRebasePlan?.ok ? (
              citationRebasePlan.changes.length > 0 ? (
                <>
                  <p className="mt-2 font-medium">
                    Confirm {citationRebasePlan.changes.length} explicit citation{" "}
                    {citationRebasePlan.changes.length === 1 ? "change" : "changes"}:
                  </p>
                  <ul
                    aria-label="Citation changes to review"
                    className="mt-1 space-y-1"
                  >
                    {citationRebasePlan.changes.map((change) => (
                      <li
                        key={`${change.index}:${change.previousEvidenceId}:${change.previousQuoteHash}`}
                        className="flex min-w-0 flex-wrap items-center gap-x-2"
                      >
                        <strong>[{change.index}]</strong>
                        <code
                          title={reportEvidenceToken(
                            change.previousEvidenceId,
                            change.previousQuoteHash,
                          )}
                          className="max-w-48 truncate"
                        >
                          {change.previousEvidenceId}:{change.previousQuoteHash.slice(0, 8)}
                        </code>
                        <ArrowRight aria-hidden="true" size={12} />
                        <code
                          title={reportEvidenceToken(
                            change.currentEvidenceId,
                            change.currentQuoteHash,
                          )}
                          className="max-w-48 truncate"
                        >
                          {change.currentEvidenceId}:{change.currentQuoteHash.slice(0, 8)}
                        </code>
                        <span className="min-w-0 truncate text-muted">
                          {change.sourceTitle} · page {change.page}
                        </span>
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <p className="mt-2">
                  Every cited evidence token is still current. Confirm to bind this
                  draft to the latest reviewed snapshot without changing its text.
                </p>
              )
            ) : citationRebasePlan ? (
              <>
                <p className="mt-2 font-medium">
                  Spark cannot safely match every saved citation.
                </p>
                <ul
                  aria-label="Citation rebase issues"
                  className="mt-1 list-disc space-y-1 pl-5"
                >
                  {citationRebasePlan.issues.map((issue) => (
                    <li key={issue}>{issue}</li>
                  ))}
                </ul>
              </>
            ) : null}
          </div>
        )}
        {!reportExport.ok && (
          <div
            role="status"
            className="border-b border-warn/25 bg-warn/5 px-4 py-2 text-caption text-text"
          >
            {t(`research.literature.report.exportBlocked.${reportExport.reason}`)}
          </div>
        )}
        {(sourceChangedCitationCount > 0 || needsReviewCitationCount > 0) && (
          <div
            role="status"
            className="border-b border-warn/25 bg-warn/5 px-4 py-2 text-caption text-text"
          >
            {sourceChangedCitationCount > 0
              ? `${sourceChangedCitationCount} source-linked citation(s) changed.`
              : `${needsReviewCitationCount} citation(s) need review.`}
          </div>
        )}
        <EvidenceCoverageSummary
          coverage={evidenceCoverage}
          loading={evidenceCoverageLoading}
          error={evidenceCoverageError}
          onRetry={onRetryEvidenceCoverage}
        />
        <div
          className={cn(
            "report-reading-shell min-h-0 flex-1",
            selectedCitation && "has-inspector",
          )}
        >
          <div className="report-reading-layout min-h-0">
            <nav
              className="report-outline"
              aria-label={t("research.literature.report.outlineAria")}
            >
            <strong>{t("research.literature.report.outline")}</strong>
            <small>
              {draft
                ? t("research.literature.report.editorSavedRevision", {
                    revision: draft.revision,
                  })
                : t("research.literature.report.editorLoading")}
            </small>
            <a href="#persistent-report" className="active">
              {t("research.literature.report.outlineReport")}
            </a>
            <a href="#persistent-evidence">
              {t("research.literature.report.outlineEvidence")}
            </a>
            {reportExport.ok && (
              <ol className="mt-2 space-y-1">
                {uniqueCitations.map((citation, index) => (
                  <li key={canonicalEvidenceKey(citation)}>
                    <button
                      type="button"
                      onClick={(event) =>
                        openCitation(citation, event.currentTarget)
                      }
                      className="w-full truncate rounded-input px-1 py-1 text-left text-xs text-link hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30"
                      aria-label={t(
                        "research.literature.report.openVerifiedCitation",
                        { index: index + 1 },
                      )}
                      title={citation.sourceTitle}
                    >
                      [{index + 1}] {citation.sourceTitle}
                    </button>
                  </li>
                ))}
              </ol>
            )}
            <StateBadge
              label={
                draft?.status === "needs-review"
                  ? t("research.literature.report.status.needsReview")
                  : draft
                    ? t("research.literature.report.status.saved")
                    : t("research.literature.report.status.loading")
              }
              tone={
                draft?.status === "needs-review"
                  ? STATUS_TONES.warn
                  : draft
                    ? STATUS_TONES.ok
                    : STATUS_TONES.neutral
              }
            />
            </nav>
            <article
              id="persistent-report"
              className="report-document overflow-y-auto"
            >
            {draftLoading && !draft ? (
              <div
                role="status"
                className="space-y-3"
                aria-label={t("research.literature.report.loadingAria")}
              >
                <div className="h-7 w-2/3 animate-pulse rounded-input bg-surface-2" />
                <div className="h-4 w-full animate-pulse rounded-input bg-surface-2" />
                <div className="h-4 w-5/6 animate-pulse rounded-input bg-surface-2" />
              </div>
            ) : !draft ? (
              <div className="py-12 text-center">
                <h2 className="text-base font-semibold text-text">
                  {t("research.literature.report.unavailableTitle")}
                </h2>
                <p className="mt-2 text-sm text-muted">
                  {t("research.literature.report.unavailableBody")}
                </p>
                <button
                  type="button"
                  onClick={onRetryDraft}
                  className="compact-button secondary-button mt-4"
                >
                  {t("research.literature.report.retry")}
                </button>
              </div>
            ) : editing ? (
              <div>
                <label
                  htmlFor="persistent-report-editor"
                  className="mb-2 block text-xs font-medium text-text"
                >
                  {t("research.literature.report.markdownLabel")}
                </label>
                <textarea
                  id="persistent-report-editor"
                  value={editorValue}
                  onChange={(event) => setEditorValue(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape" && !draftMutating) {
                      setEditorValue(draft.contentMarkdown);
                      setEditorError(null);
                      setEditing(false);
                    }
                    if (
                      event.key === "Enter" &&
                      (event.metaKey || event.ctrlKey) &&
                      dirty &&
                      !draftMutating
                    ) {
                      event.preventDefault();
                      void saveEdit();
                    }
                  }}
                  disabled={draftMutating}
                  spellCheck
                  className="min-h-[32rem] w-full resize-y rounded-input border border-border bg-bg px-4 py-3 font-mono text-sm leading-6 text-text outline-none focus:border-accent focus:ring-2 focus:ring-accent/20 disabled:opacity-60"
                />
                <p className="mt-2 text-caption text-muted">
                  {t("research.literature.report.editorHint")}
                </p>
              </div>
            ) : (
              <>
                <MarkdownViewer
                  variant="document"
                  citationIndices={uniqueCitations.map((_, index) => index + 1)}
                  onCitationClick={(index, trigger) => {
                    const citation = uniqueCitations[index - 1];
                    if (citation) openCitation(citation, trigger);
                  }}
                  citationAriaLabel={(index) =>
                    t("research.literature.report.openVerifiedCitation", {
                      index,
                    })
                  }
                >
                  {draft.contentMarkdown}
                </MarkdownViewer>
                <section id="persistent-evidence" className="mt-10 border-t border-border pt-6">
                  <h2 className="text-document-heading font-semibold text-text">
                    {t("research.literature.report.evidenceCitations")}
                  </h2>
                  <p className="mt-2 text-sm text-muted">
                    {t("research.literature.report.evidenceCitationsHint")}
                  </p>
                  <ol className="mt-4 space-y-2">
                    {uniqueCitations.map((citation, index) => (
                      <li key={canonicalEvidenceKey(citation)}>
                        <span className="block px-2 py-2 text-sm text-text">
                          [{index + 1}] {citation.sourceTitle} ·{" "}
                          {t("research.literature.report.citationPage", {
                            page: citation.pageLabel ?? citation.pageIndex + 1,
                          })}
                        </span>
                      </li>
                    ))}
                  </ol>
                </section>
              </>
            )}
            </article>
          </div>
          {selectedCitation && (
            <CitationDrawer
              citation={selectedCitation}
              exportCitation={
                reportExport.ok
                  ? reportExport.citations.find(
                      (citation) =>
                        citation.canonicalEvidenceKey ===
                        canonicalEvidenceKey(selectedCitation),
                    ) ?? null
                  : null
              }
              copyGateKey={
                draft
                  ? `${draft.id}:${draft.revision}:${draft.contentSha256}:${draft.status}`
                  : "report-draft-unavailable"
              }
              copyBlocked={exportBlocked}
              copyBlockedReason={exportBlockReason}
              onBeforeCopy={onExportDraft}
              onReload={onRetryDraft}
              onClose={closeCitation}
              onOpenPdf={() => onOpenPdf(selectedCitation)}
            />
          )}
        </div>
      </section>
    </div>
  );
}

function LegacyReportSurface({ mode, result, citations, reportExport, sourceChangedCitationCount, needsReviewCitationCount, selectedCitation, realSourceMode, evidenceCoverage, evidenceCoverageLoading, evidenceCoverageError, onRetryEvidenceCoverage, onCitationOpen, onCitationClose, onModeChange, onOpenPdf }: ReportSurfaceProps) {
  /* eslint-enable i18next/no-literal-string */
  const { t } = useTranslation("pages");
  const [reportFormat, setReportFormat] =
    useState<ReportDocumentFormat>("markdown");
  const [citationFormat, setCitationFormat] = useState<"csv" | "json" | "bibtex">("csv");
  const citationTriggerRef = useRef<HTMLElement | null>(null);
  const openCitation = (citation: ReportCitation, trigger: HTMLElement) => {
    citationTriggerRef.current = trigger;
    onCitationOpen(citation);
  };
  const closeCitation = () => {
    citationTriggerRef.current?.focus();
    onCitationClose();
  };
  // eslint-disable-next-line i18next/no-literal-string -- native scroll options are API constants
  const scrollToReferences = (targetId: string) => document.getElementById(targetId)?.scrollIntoView({ block: "start", behavior: "smooth" });
  const exportBlockReason = reportExport.ok
    ? null
    : t(`research.literature.report.exportBlocked.${reportExport.reason}`);
  const copyReport = async () => {
    if (!reportExport.ok) return;
    try {
      await copyText(reportExport.markdown);
      toast.success(t("research.literature.report.reportCopied"));
    } catch (error) {
      toast.error(t("research.literature.report.copyFailed", { error: error instanceof Error ? error.message : String(error) }));
    }
  };
  const saveReport = async () => {
    if (!reportExport.ok) return;
    try {
      const outcome = await exportReportDocument(
        reportFormat,
        reportExport.markdown,
        {
          saved: (path) =>
            t("research.literature.report.saveFeedback.saved", { path }),
          downloaded: (filename) =>
            t("research.literature.report.saveFeedback.downloaded", { filename }),
          canceled: (filename) =>
            t("research.literature.report.saveFeedback.canceled", { filename }),
          failed: (filename, error) =>
            t("research.literature.report.saveFeedback.failed", {
              filename,
              error,
            }),
        },
      );
      if (outcome === "print-opened") {
        toast.info(t("research.literature.report.printOpened"));
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  };
  const saveCitations = () => {
    if (!reportExport.ok) return;
    const exports = {
      csv: { filename: "spark-verified-citations.csv", text: reportExport.citationsCsv, mime: "text/csv" },
      json: { filename: "spark-verified-citations.json", text: reportExport.citationsJson, mime: "application/json" },
      bibtex: { filename: "spark-verified-citations.bib", text: reportExport.citationsBibtex, mime: "application/x-bibtex" },
    };
    const selected = exports[citationFormat];
    void saveTextWithFeedback(selected.filename, selected.text, selected.mime, {
      saved: (path) => t("research.literature.report.saveFeedback.saved", { path }),
      downloaded: (filename) => t("research.literature.report.saveFeedback.downloaded", { filename }),
      canceled: (filename) => t("research.literature.report.saveFeedback.canceled", { filename }),
      failed: (filename, error) => t("research.literature.report.saveFeedback.failed", { filename, error }),
    });
  };
  // eslint-disable-next-line i18next/no-literal-string -- internal report mode discriminants
  if (mode === "generating") return <GeneratingReport realSourceMode={realSourceMode} onShowReview={() => onModeChange("review")} />;
  if (realSourceMode) {
    const summary = result?.summary ?? null;
    const claimGroups = citations.reduce<Array<{ claim: string; citations: ReportCitation[] }>>((groups, citation) => {
      const existing = groups.find((group) => group.claim === citation.claim);
      if (existing) existing.citations.push(citation);
      else groups.push({ claim: citation.claim, citations: [citation] });
      return groups;
    }, []);
    const uniqueCitations = citations.filter(
      (citation, index, all) =>
        all.findIndex((candidate) => canonicalEvidenceKey(candidate) === canonicalEvidenceKey(citation)) === index,
    );
    const citationIndex = (citation: ReportCitation) =>
      uniqueCitations.findIndex(
        (candidate) => canonicalEvidenceKey(candidate) === canonicalEvidenceKey(citation),
      ) + 1;
    return (
      <div className="report-layout h-full min-h-0">
        <section className="relative flex min-w-0 flex-col bg-surface">
          <div className="research-toolbar"><label className="search-field" title={t("research.literature.report.searchUnavailableTitle")}><Search size={13} /><input disabled placeholder={t("research.literature.report.findUnavailable")} /></label><button type="button" onClick={() => scrollToReferences("real-references")} className="compact-button selected-button">{t("research.literature.report.references")}</button><button type="button" onClick={() => void copyReport()} disabled={!reportExport.ok} title={exportBlockReason ?? undefined} className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40">{t("research.literature.report.copyReport")}</button><label className="sr-only" htmlFor="report-export-format">{t("research.literature.report.reportFormat")}</label><select id="report-export-format" value={reportFormat} onChange={(event) => setReportFormat(event.target.value as ReportDocumentFormat)} disabled={!reportExport.ok} title={exportBlockReason ?? undefined} className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"><option value="markdown">{t("research.literature.report.reportFormats.markdown")}</option><option value="docx">{t("research.literature.report.reportFormats.docx")}</option><option value="pdf">{t("research.literature.report.reportFormats.pdf")}</option></select><button type="button" onClick={() => void saveReport()} disabled={!reportExport.ok} title={exportBlockReason ?? undefined} className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40">{t("research.literature.report.exportReport")}</button><label className="sr-only" htmlFor="citation-export-format">{t("research.literature.report.citationFormat")}</label><select id="citation-export-format" value={citationFormat} onChange={(event) => setCitationFormat(event.target.value as "csv" | "json" | "bibtex")} disabled={!reportExport.ok} title={exportBlockReason ?? undefined} className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"><option value="csv">{t("research.literature.report.formats.csv")}</option><option value="json">{t("research.literature.report.formats.json")}</option><option value="bibtex">{t("research.literature.report.formats.bibtex")}</option></select><button type="button" onClick={saveCitations} disabled={!reportExport.ok} title={exportBlockReason ?? undefined} className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40">{t("research.literature.report.exportCitations")}</button><button type="button" disabled title={t("research.literature.report.shareUnavailableTitle")} className="compact-button secondary-button ml-auto disabled:cursor-not-allowed disabled:opacity-40">{t("research.literature.report.shareUnavailable")}</button></div>
          {!reportExport.ok && <div role="status" className="border-b border-warn/25 bg-warn/5 px-4 py-2 text-caption text-text">{exportBlockReason}</div>}
          <EvidenceCoverageSummary coverage={evidenceCoverage} loading={evidenceCoverageLoading} error={evidenceCoverageError} onRetry={onRetryEvidenceCoverage} />
          <div className={cn("report-reading-shell min-h-0 flex-1", selectedCitation && "has-inspector")}>
            <div className="report-reading-layout min-h-0">
              <nav className="report-outline" aria-label={t("research.literature.report.outlineAria")}><strong>{t("research.literature.report.outline")}</strong><small>{t("research.literature.report.localOutlineMeta")}</small><a href="#real-summary" className="active">{t("research.literature.report.summary")}</a><a href="#real-findings">{t("research.literature.report.findings")}</a><a href="#real-limitations">{t("research.literature.report.reviewStatus")}</a><StateBadge label={citations.length > 0 ? t("research.literature.report.verifiedPassages") : t("research.literature.report.needsReview")} tone={citations.length > 0 ? STATUS_TONES.ok : STATUS_TONES.warn} /></nav>
              <article className="report-document"><h1>{result ? t("research.literature.report.localResultTitle") : t("research.literature.report.importedReviewTitle")}</h1><p className="report-meta">{t("research.literature.report.sessionMeta")}</p><h2 id="real-summary">{citations.length > 0 ? t("research.literature.report.sourceLinkedSummary") : t("research.literature.report.unverifiedSummary")}</h2>{summary ? <p>{summary}</p> : <p>{t("research.literature.report.noSummary")}</p>}{sourceChangedCitationCount > 0 && <aside className="evidence-limitation"><strong>{t("research.literature.report.sourceChanged")}</strong><p>{t("research.literature.report.stalePassages", { count: sourceChangedCitationCount })}</p></aside>}{needsReviewCitationCount > 0 && <aside className="evidence-limitation"><strong>{t("research.literature.report.needsReview")}</strong><p>{t("research.literature.report.unboundPassages", { count: needsReviewCitationCount })}</p></aside>}{citations.length > 0 ? <><h2 id="real-findings">{t("research.literature.report.findings")}</h2>{claimGroups.map((group) => <p key={group.claim}>{group.claim} {group.citations.map((citation, occurrence) => { const index = citationIndex(citation); return <button key={`${canonicalEvidenceKey(citation)}-${occurrence}`} type="button" onClick={(event) => openCitation(citation, event.currentTarget)} className="citation-link" aria-label={t("research.literature.report.openVerifiedCitation", { index })}>[{index}]</button>; })}</p>)}</> : <aside className="evidence-limitation" id="real-findings"><strong>{t("research.literature.report.needsReview")}</strong><p>{t("research.literature.report.noVerifiedPassage")}</p></aside>}<h2 id="real-limitations">{t("research.literature.report.reviewStatus")}</h2><p>{t("research.literature.report.reviewBoundary")}</p><h2 id="real-references">{t("research.literature.report.references")}</h2><ol>{uniqueCitations.length > 0 ? uniqueCitations.map((citation) => <li key={canonicalEvidenceKey(citation)}>{citation.sourceTitle} · {t("research.literature.report.pageReference", { page: citation.pageLabel ?? citation.pageIndex + 1 })}</li>) : <li>{t("research.literature.report.noVerifiedPassages")}</li>}</ol></article>
            </div>
            {selectedCitation && <CitationDrawer citation={selectedCitation} exportCitation={reportExport.ok ? reportExport.citations.find((citation) => citation.canonicalEvidenceKey === canonicalEvidenceKey(selectedCitation)) ?? null : null} onClose={closeCitation} onOpenPdf={() => onOpenPdf(selectedCitation)} />}
          </div>
        </section>
      </div>
    );
  }
  return (
    <div className="flex h-full min-h-0 items-center justify-center bg-surface p-8">
      <div className="max-w-md text-center">
        <h2 className="text-base font-semibold text-text">{t("research.literature.report.noSummary")}</h2>
        <p className="mt-2 text-sm leading-6 text-muted">{t("research.literature.emptyLibrary")}</p>
      </div>
    </div>
  );
}

interface CitationDrawerProps {
  citation: ReportCitation;
  exportCitation: VerifiedCitationExport | null;
  copyGateKey?: string;
  copyBlocked?: boolean;
  copyBlockedReason?: string | null;
  onBeforeCopy?: () => Promise<unknown>;
  onReload?: () => void;
  onClose: () => void;
  onOpenPdf: () => void;
}

function CitationDrawer({
  citation,
  exportCitation,
  copyGateKey,
  copyBlocked = false,
  copyBlockedReason = null,
  onBeforeCopy,
  onReload,
  onClose,
  onOpenPdf,
}: CitationDrawerProps) {
  const { t } = useTranslation("pages");
  const closeRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const copyInFlightRef = useRef(false);
  const [copying, setCopying] = useState(false);
  const [copyConflict, setCopyConflict] = useState(false);
  const [copyError, setCopyError] = useState<string | null>(null);
  const dismiss = () => { returnFocusRef.current?.focus(); onClose(); };
  useEffect(() => { returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; closeRef.current?.focus(); }, []);
  useEffect(() => {
    setCopyConflict(false);
    setCopyError(null);
  }, [copyGateKey]);
  const copyCitation = async () => {
    if (
      !exportCitation ||
      copyBlocked ||
      copyConflict ||
      copyInFlightRef.current
    ) {
      return;
    }
    copyInFlightRef.current = true;
    setCopying(true);
    setCopyError(null);
    try {
      await onBeforeCopy?.();
      await copyText(serializeCitationText(exportCitation));
      toast.success(t("research.literature.citation.copied"));
    } catch (error) {
      const conflict =
        error instanceof ScienceCoreApiError && error.status === 409;
      if (conflict) {
        setCopyConflict(true);
        setCopyError(t("research.literature.citation.copyConflict"));
      } else {
        setCopyError(error instanceof Error ? error.message : String(error));
      }
      toast.error(t("research.literature.citation.copyFailed", { error: error instanceof Error ? error.message : String(error) }));
    } finally {
      copyInFlightRef.current = false;
      setCopying(false);
    }
  };
  const visibleCopyError = copyError ?? (copyBlocked ? copyBlockedReason : null);
  return (
    <aside
      className="citation-drawer"
      aria-label={t("research.literature.citation.detailAria")}
      onKeyDown={(event) => {
        if (event.key === "Escape") dismiss();
      }}
    >
      <div className="flex items-center justify-between">
        <h2>{t("research.literature.citation.verifiedPassage")}</h2>
        <button
          ref={closeRef}
          type="button"
          onClick={dismiss}
          className="icon-button"
          aria-label={t("research.literature.citation.closeAria")}
        >
          <X size={14} />
        </button>
      </div>
      <p className="text-caption text-muted">
        {t("research.literature.citation.claim", { claim: citation.claim })}
      </p>
      <div className="source-row selected-source">
        <FileText size={15} />
        <span>
          <strong>{citation.sourceTitle}</strong>
          <small>
            {t("research.literature.citation.sourceMeta", {
              page: citation.pageLabel ?? citation.pageIndex + 1,
              sourceId: citation.sourceId,
            })}
          </small>
        </span>
        <StateBadge
          label={t("research.literature.citation.locatedLocally")}
          tone={STATUS_TONES.ok}
        />
      </div>
      <h3>
        {citation.relationship === "contradicting"
          ? t("research.literature.citation.contradictingQuote")
          : t("research.literature.citation.supportingQuote")}
      </h3>
      <blockquote>{citation.text}</blockquote>
      <details className="citation-technical-details">
        <summary>{t("research.literature.citation.technicalDetails")}</summary>
        <p className="text-caption text-muted">
          {t("research.literature.citation.quoteMeta", {
            hash: citation.quoteHash,
            method: citation.extractionMethod,
          })}
        </p>
      </details>
      {visibleCopyError && (
        <div
          role="alert"
          className="rounded-input border border-warn/30 bg-warn/5 px-3 py-2 text-caption text-text"
        >
          <p>{visibleCopyError}</p>
          {copyConflict && onReload && (
            <button
              type="button"
              onClick={onReload}
              className="mt-2 compact-button secondary-button"
            >
              {t("research.literature.citation.reloadReport")}
            </button>
          )}
        </div>
      )}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onOpenPdf}
          className="compact-button primary-button"
        >
          {t("research.literature.citation.openExactPage")}
        </button>
        <button
          type="button"
          onClick={() => void copyCitation()}
          disabled={!exportCitation || copyBlocked || copyConflict || copying}
          title={visibleCopyError ?? undefined}
          className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"
        >
          {copying
            ? t("research.literature.citation.checking")
            : t("research.literature.citation.copy")}
        </button>
      </div>
    </aside>
  );
}

function GeneratingReport({ realSourceMode, onShowReview }: { realSourceMode: boolean; onShowReview: () => void }) {
  const { t } = useTranslation("pages");
  if (realSourceMode) {
    return <div className="generating-layout h-full min-h-0"><section className="generating-conversation"><h1>{t("research.literature.generating.localTitle")}</h1><p className="text-muted">{t("research.literature.generating.localBody")}</p><div className="generation-card"><Loader2 size={20} className="animate-spin text-accent" /><div><strong>{t("research.literature.generating.running")}</strong><p>{t("research.literature.generating.waitingResult")}</p></div><p className="col-span-2">{t("research.literature.generating.findingsBoundary")}</p><p className="col-span-2 text-muted">{t("research.literature.generating.noInference")}</p></div><div className="activity-composer mt-auto">{t("research.literature.generating.progressHint")}</div></section><section className="generating-artifacts"><h2>{t("research.literature.generating.artifacts")}</h2><div><h3>{t("research.literature.generating.noReport")}</h3><p>{t("research.literature.generating.noReportBody")}</p><button type="button" disabled className="compact-button secondary-button">{t("research.literature.generating.showArtifacts")}</button><button type="button" onClick={onShowReview} className="mt-3 block text-xs text-link hover:underline">{t("research.literature.generating.reviewEvidence")}</button></div></section></div>;
  }
  return <div className="flex h-full min-h-0 items-center justify-center bg-surface p-8"><div className="max-w-md text-center"><h2 className="text-base font-semibold text-text">{t("research.literature.report.noSummary")}</h2><p className="mt-2 text-sm leading-6 text-muted">{t("research.literature.emptyLibrary")}</p><button type="button" onClick={onShowReview} className="mt-4 compact-button secondary-button">{t("research.literature.home")}</button></div></div>;
}

function PaperIdentity({ paper }: { paper: ResearchPaper }) {
  return <span className="paper-identity" title={paper.source ? `${paper.title} · ${paper.source.id} · ${paper.source.contentHash}` : paper.title}><FileText size={14} /><span><strong>{paper.title}</strong><small>{paper.authors} · {paper.year} · {paper.journal} · {paper.citations}</small></span></span>;
}

function StateBadge({ label, tone }: { label: string; tone: "neutral" | "ok" | "warn" | "error" }) {
  return <span className={cn("state-badge", `state-badge-${tone}`)}><span aria-hidden="true" />{label}</span>;
}
