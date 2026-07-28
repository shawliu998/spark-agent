import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Quote,
} from "lucide-react";
import type {
  ResearchGenerationMode,
  ResearchSource,
  ResearchWorkflowResult,
  ResearchWorkflowSnapshot,
  WorkflowEvidenceRelationship,
} from "@spark/research-domain";
import { MarkdownViewer } from "@/components/markdown-viewer/MarkdownViewer";
import { cn } from "@/lib/cn";
import {
  generationModeForSnapshot,
  resultReviewState,
  statusLabel,
} from "./workflowModel";
import type {
  CitationPresentation,
  ClaimPresentation,
} from "./researchPresentation";
import { presentWorkflowClaim } from "./researchPresentation";

interface WorkflowResultViewProps {
  snapshot: ResearchWorkflowSnapshot;
  sources: ResearchSource[];
  onSelectEvidence: (evidence: WorkflowEvidenceRelationship) => void;
  onOpenReview: () => void;
}

export function WorkflowResultView({
  snapshot,
  sources,
  onSelectEvidence,
  onOpenReview,
}: WorkflowResultViewProps) {
  const { t } = useTranslation("pages");
  const result = snapshot.result;
  if (!result) return null;
  const reviewState = resultReviewState(snapshot);
  const generationMode = generationModeForSnapshot(snapshot);
  const claims = result.claims.map((claim, index) =>
    presentWorkflowClaim(claim, sources, index),
  );

  return (
    <article className="space-y-4">
      <section className="border-b border-border pb-5">
        <div className="mb-3 flex items-center gap-2 text-xs font-medium text-muted">
          {reviewState === "passed" ? (
            <CheckCircle2 size={14} className="text-ok" />
          ) : (
            <AlertTriangle
              size={14}
              className={reviewState === "needs-revision" ? "text-warn" : "text-muted"}
            />
          )}
          {reviewState === "passed"
            ? t("research.workflow.resultHeading", {
                defaultValue: "Evidence-integrity review passed",
              })
            : reviewState === "legacy-passed"
              ? t("research.workflow.legacyResultHeading", {
                  defaultValue:
                    "Legacy review passed — result was not immutably frozen",
                })
              : reviewState === "needs-revision"
                ? t("research.workflow.resultNeedsRevision", {
                    defaultValue: "Research result needs revision",
                  })
                : t("research.workflow.resultPendingReview", {
                    defaultValue: "Provisional evidence map — review pending",
                  })}
          {snapshot.latestReview && (
            <button
              type="button"
              onClick={onOpenReview}
              className="ml-auto text-link hover:underline"
            >
              {t("research.workflow.reviewDetails", {
                defaultValue: "Review details",
              })}
            </button>
          )}
        </div>
        <GenerationBoundary mode={generationMode} result={result} />
        <MarkdownViewer className="max-w-[70ch] text-base leading-7">{result.summary}</MarkdownViewer>
      </section>

      <div className="flex items-center gap-2 pt-1">
        <h3 className="text-xs font-medium text-muted">
          {t("research.claimsHeading", {
            defaultValue: "Claims ({{count}})",
            count: claims.length,
          })}
        </h3>
        <div className="h-px flex-1 bg-border" />
      </div>

      {claims.map((claim, index) => (
        <WorkflowClaimCard
          key={claim.key}
          claim={claim}
          index={index}
          onSelectEvidence={onSelectEvidence}
        />
      ))}

      {result.unresolvedQuestions.length > 0 && (
        <section className="border-y border-warn/25 bg-warn/5 px-1 py-4">
          <h3 className="flex items-center gap-2 text-xs font-medium text-text">
            <AlertTriangle size={14} className="text-warn" />
            {t("research.unresolvedHeading", {
              defaultValue: "Unresolved questions",
            })}
          </h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed text-muted">
            {result.unresolvedQuestions.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}

function WorkflowClaimCard({
  claim,
  index,
  onSelectEvidence,
}: {
  claim: ClaimPresentation;
  index: number;
  onSelectEvidence: (evidence: WorkflowEvidenceRelationship) => void;
}) {
  const { t } = useTranslation("pages");
  const tone =
    claim.supportStatus === "supported"
      ? "text-ok"
      : claim.supportStatus === "pending-review"
        ? "text-muted"
        : claim.supportStatus === "contradicted" ||
            claim.supportStatus === "insufficient-evidence"
          ? "text-error"
          : "text-warn";
  const dotTone =
    claim.supportStatus === "supported"
      ? "bg-ok"
      : claim.supportStatus === "pending-review"
        ? "bg-muted"
        : claim.supportStatus === "contradicted" ||
            claim.supportStatus === "insufficient-evidence"
          ? "bg-error"
          : "bg-warn";

  return (
    <section className="border-b border-border py-4">
      <div className="flex items-start gap-3">
        <span className="w-7 shrink-0 pt-0.5 font-mono text-caption font-medium text-muted">
          C{index + 1}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium leading-relaxed text-text">
            {claim.statement}
          </p>
          <span
            className={cn(
              "mt-2 inline-flex items-center gap-1.5 text-xs font-medium",
              tone,
            )}
          >
            <span aria-hidden="true" className={cn("h-1.5 w-1.5 rounded-full", dotTone)} />
            {t(`research.claimSupport.${claim.supportStatus}`, {
              defaultValue: statusLabel(claim.supportStatus),
            })}
          </span>
          {claim.issues.length > 0 && (
            <span className="ml-2 inline-flex text-caption text-warn">
              {t("research.workflow.incompleteClaimMetadata", {
                defaultValue: "Incomplete claim metadata — not treated as verified",
              })}
            </span>
          )}
        </div>
      </div>
      <div className="mt-3 space-y-2 border-t border-border-faint pt-3">
        {claim.citations.length === 0 && (
          <div className="flex items-center gap-2 text-xs text-warn">
            <AlertTriangle size={13} />
            {t("research.noEvidence", {
              defaultValue: "No verified source passage attached.",
            })}
          </div>
        )}
        {claim.citations.map((citation) => (
          <CitationRow
            key={citation.key}
            citation={citation}
            onSelectEvidence={onSelectEvidence}
          />
        ))}
      </div>
    </section>
  );
}

function CitationRow({
  citation,
  onSelectEvidence,
}: {
  citation: CitationPresentation;
  onSelectEvidence: (evidence: WorkflowEvidenceRelationship) => void;
}) {
  const { t } = useTranslation("pages");
  const relationshipTone =
    citation.relationship === "contradicting"
      ? "text-warn"
      : citation.relationship === "supporting"
        ? "text-accent"
        : "text-muted";
  return (
    <div className="rounded-input bg-surface-2">
      <button
        type="button"
        disabled={!citation.original}
        onClick={() => citation.original && onSelectEvidence(citation.original)}
        className="group w-full px-3 py-3 text-left hover:bg-bg/70 disabled:cursor-default"
      >
        <span className="flex min-w-0 items-center gap-2 text-xs font-medium text-muted">
          <Quote size={12} className="text-muted" />
          <span className="min-w-0 truncate">
            {t("research.evidencePage", {
              defaultValue: "{{source}}, page {{page}}",
              source: citation.sourceTitle,
              page: citation.page,
            })}
          </span>
          <span className={cn("ml-auto shrink-0 normal-case tracking-normal", relationshipTone)}>
            {citation.relationship === "unclassified"
              ? t("research.workflow.unclassifiedRelationship", {
                  defaultValue: "relationship not reported",
                })
              : t(`research.evidenceRelationship.${citation.relationship}`, {
                  defaultValue: citation.relationship,
                })}
          </span>
          {citation.original && (
            <ChevronRight
              size={12}
              className="shrink-0 transition-transform group-hover:translate-x-0.5"
            />
          )}
        </span>
        <span className="mt-1.5 block line-clamp-4 text-ui leading-relaxed text-text/90">
          {citation.text}
        </span>
        <span className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-muted">
          <span className={citation.verified ? "text-ok" : "text-warn"}>
            {citation.verified
              ? t("research.quoteLocated", { defaultValue: "quote located locally" })
              : t("research.needsReview", {
                  defaultValue: "quote location needs review",
                })}
          </span>
          <span>
            {citation.frozen
              ? t("research.workflow.frozenCitation", {
                  defaultValue: "frozen source provenance",
                })
              : t("research.workflow.currentCitationMetadata", {
                  defaultValue: "current source metadata",
                })}
          </span>
        </span>
      </button>
      <details className="mx-3 border-t border-border-faint">
        <summary className="flex min-h-9 cursor-pointer items-center font-sans text-xs font-medium text-link hover:underline">
          {t("research.workflow.citationProvenance", {
            defaultValue: "Citation provenance",
          })}
        </summary>
        <span className="block space-y-1 pb-2 font-mono text-caption leading-relaxed text-muted">
          <span className="block break-all">
            {t("research.workflow.evidenceId", { defaultValue: "Evidence ID" })}: {citation.evidenceId ?? "—"}
          </span>
          <span className="block break-all">
            {t("research.workflow.sourceId", { defaultValue: "Source ID" })}: {citation.sourceId ?? "—"}
          </span>
          <span className="block break-all">
            {t("research.workflow.quoteHash", { defaultValue: "Quote SHA-256" })}: {citation.quoteHash ?? "—"}
          </span>
          {citation.sourceContentHash && (
            <span className="block break-all">
              {t("research.workflow.fileHash", { defaultValue: "File SHA-256" })}: {citation.sourceContentHash}
            </span>
          )}
          {citation.sourcePageManifestHash && (
            <span className="block break-all">
              {t("research.workflow.pageManifestHash", {
                defaultValue: "Parsed-page manifest SHA-256",
              })}: {citation.sourcePageManifestHash}
            </span>
          )}
          {!citation.frozen && (
            <span className="block font-sans text-warn">
              {t("research.workflow.legacyCitationBoundary", {
                defaultValue:
                  "Citation metadata was not frozen with this result; current source metadata is shown for orientation only.",
              })}
            </span>
          )}
          {!citation.original && (
            <span className="block font-sans text-error">
              {t("research.workflow.citationUnavailable", {
                defaultValue:
                  "This citation is incomplete and cannot be opened at an exact source location.",
              })}
            </span>
          )}
        </span>
      </details>
    </div>
  );
}

function GenerationBoundary({
  mode,
  result,
}: {
  mode: ResearchGenerationMode;
  result: ResearchWorkflowResult;
}) {
  const { t } = useTranslation("pages");
  const remoteOutput = result.generator.startsWith("remote-model-assisted-");

  return (
    <div className="mb-4 max-w-[70ch] border-y border-border-faint py-3 text-ui leading-relaxed text-muted">
      <strong className="font-medium text-text">
        {remoteOutput
          ? t("research.workflow.modelAssistedResult", {
              defaultValue: "Model-assisted synthesis.",
            })
          : t("research.workflow.localResult", {
              defaultValue: "Locally generated synthesis.",
            })}
      </strong>{" "}
      {t("research.workflow.generationBoundary", {
        defaultValue:
          "Generation mode is provenance, not evidence strength. Claim support comes from the cited source relationships and the separate deterministic evidence-integrity review.",
      })}
      <p
        className={cn(
          "mt-2 text-xs",
          result.integrityStatus === "verified-frozen-v2"
            ? "text-ok"
            : "text-warn",
        )}
      >
        {result.integrityStatus === "verified-frozen-v2"
          ? t("research.workflow.frozenResultIntegrity", {
              defaultValue:
                "Frozen result verified: answer, ordered claims, citations, source files, and parsed-page manifests match the completed review.",
            })
          : t("research.workflow.unfrozenResultIntegrity", {
              defaultValue:
                "Unfrozen result: review is pending, requires revision, or predates immutable result snapshots.",
            })}
      </p>
      <details className="mt-2 border-t border-border-faint pt-1">
        <summary className="flex min-h-9 cursor-pointer items-center text-xs font-medium text-link hover:underline">
          {t("research.workflow.generationDetails", {
            defaultValue: "Generation details",
          })}
        </summary>
      <dl className="grid gap-3 pb-1 sm:grid-cols-3">
        <div className="min-w-0">
          <dt className="text-xs font-medium text-muted">
            {t("research.workflow.resultGenerator", {
              defaultValue: "Generator",
            })}
          </dt>
          <dd className="mt-0.5 break-words font-mono text-caption text-text">
            {result.generator}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs font-medium text-muted">
            {t("research.workflow.model", { defaultValue: "Model" })}
          </dt>
          <dd className="mt-0.5 break-words font-mono text-caption text-text">
            {result.model ??
              (mode === "local-deterministic"
                ? t("research.workflow.noRemoteModel", {
                    defaultValue: "None — local deterministic",
                  })
                : t("research.workflow.notReported", {
                    defaultValue: "Not reported (legacy snapshot)",
                  }))}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs font-medium text-muted">
            {t("research.workflow.promptVersion", {
              defaultValue: "Prompt version",
            })}
          </dt>
          <dd className="mt-0.5 break-words font-mono text-caption text-text">
            {result.promptVersion ??
              t("research.workflow.notReported", {
                defaultValue: "Not reported (legacy snapshot)",
              })}
          </dd>
        </div>
      </dl>
      </details>
    </div>
  );
}
