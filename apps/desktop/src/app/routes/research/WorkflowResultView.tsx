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
  WorkflowClaim,
  WorkflowEvidenceRelationship,
} from "@spark/research-domain";
import { MarkdownViewer } from "@/components/markdown-viewer/MarkdownViewer";
import { cn } from "@/lib/cn";
import {
  generationModeForSnapshot,
  resultReviewState,
  statusLabel,
} from "./workflowModel";

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

  return (
    <article className="space-y-4">
      <section className="rounded-card border border-border bg-surface p-4 shadow-card">
        <div className="mb-3 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-muted">
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
        <MarkdownViewer>{result.summary}</MarkdownViewer>
      </section>

      <div className="flex items-center gap-2 pt-1">
        <h3 className="text-xs font-medium uppercase tracking-wider text-muted">
          {t("research.claimsHeading", {
            defaultValue: "Claims ({{count}})",
            count: result.claims.length,
          })}
        </h3>
        <div className="h-px flex-1 bg-border" />
      </div>

      {result.claims.map((claim, index) => (
        <WorkflowClaimCard
          key={claim.id}
          claim={claim}
          index={index}
          sources={sources}
          onSelectEvidence={onSelectEvidence}
        />
      ))}

      {result.unresolvedQuestions.length > 0 && (
        <section className="rounded-card border border-warn/30 bg-warn/5 p-4">
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
  sources,
  onSelectEvidence,
}: {
  claim: WorkflowClaim;
  index: number;
  sources: ResearchSource[];
  onSelectEvidence: (evidence: WorkflowEvidenceRelationship) => void;
}) {
  const { t } = useTranslation("pages");
  const tone =
    claim.supportStatus === "supported"
      ? "bg-ok/10 text-ok ring-ok/20"
      : claim.supportStatus === "pending-review"
        ? "bg-surface-2 text-muted ring-border"
        : claim.supportStatus === "contradicted" ||
            claim.supportStatus === "insufficient-evidence"
          ? "bg-error/10 text-error ring-error/20"
          : "bg-warn/10 text-warn ring-warn/20";

  return (
    <section className="rounded-card border border-border bg-surface p-4">
      <div className="flex items-start gap-3">
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface-2 text-[10px] font-semibold text-muted ring-1 ring-border">
          {index + 1}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium leading-relaxed text-text">
            {claim.statement}
          </p>
          <span
            className={cn(
              "mt-2 inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium ring-1",
              tone,
            )}
          >
            {t(`research.claimSupport.${claim.supportStatus}`, {
              defaultValue: statusLabel(claim.supportStatus),
            })}
          </span>
        </div>
      </div>
      <div className="mt-3 space-y-2 border-t border-border-faint pt-3">
        {claim.evidence.length === 0 && (
          <div className="flex items-center gap-2 text-xs text-warn">
            <AlertTriangle size={13} />
            {t("research.noEvidence", {
              defaultValue: "No verified source passage attached.",
            })}
          </div>
        )}
        {claim.evidence.map((evidence) => {
          const source = sources.find((item) => item.id === evidence.sourceId);
          const sourceTitle =
            evidence.sourceTitle ??
            source?.title ??
            t("research.unknownSource", { defaultValue: "Unknown source" });
          const frozenCitation =
            evidence.sourceTitle !== null &&
            evidence.sourceContentHash !== null &&
            evidence.sourcePageManifestHash !== null;
          return (
            <button
              key={evidence.evidenceId}
              type="button"
              onClick={() => onSelectEvidence(evidence)}
              className="group w-full rounded-input border border-border bg-bg px-3 py-2.5 text-left hover:border-accent/30 hover:bg-surface-2"
            >
              <span className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-wide text-muted">
                <Quote
                  size={12}
                  className={
                    evidence.relationship === "contradicting"
                      ? "text-warn"
                      : "text-accent"
                  }
                />
                {t("research.evidencePage", {
                  defaultValue: "{{source}}, page {{page}}",
                  source: sourceTitle,
                  page: evidence.pageLabel ?? evidence.pageIndex + 1,
                })}
                <span className="ml-auto normal-case tracking-normal">
                  {t(
                    `research.evidenceRelationship.${evidence.relationship}`,
                    { defaultValue: evidence.relationship },
                  )}
                </span>
                <ChevronRight
                  size={12}
                  className="transition-transform group-hover:translate-x-0.5"
                />
              </span>
              <span className="mt-1.5 block line-clamp-4 font-serif text-[13px] leading-relaxed text-text/90">
                {evidence.text}
              </span>
              <span className="mt-2 block space-y-0.5 border-t border-border-faint pt-2 font-mono text-[9px] leading-relaxed text-muted">
                <span className="block break-all">
                  {t("research.workflow.evidenceId", {
                    defaultValue: "Evidence ID",
                  })}
                  : {evidence.evidenceId}
                </span>
                <span className="block break-all">
                  {t("research.workflow.sourceId", {
                    defaultValue: "Source ID",
                  })}
                  : {evidence.sourceId}
                </span>
                <span className="block break-all">
                  {t("research.workflow.quoteHash", {
                    defaultValue: "Quote SHA-256",
                  })}
                  : {evidence.quoteHash}
                </span>
                {evidence.sourceContentHash && (
                  <span className="block break-all">
                    {t("research.workflow.fileHash", {
                      defaultValue: "File SHA-256",
                    })}
                    : {evidence.sourceContentHash}
                  </span>
                )}
                {evidence.sourcePageManifestHash && (
                  <span className="block break-all">
                    {t("research.workflow.pageManifestHash", {
                      defaultValue: "Parsed-page manifest SHA-256",
                    })}
                    : {evidence.sourcePageManifestHash}
                  </span>
                )}
                {!frozenCitation && (
                  <span className="block font-sans text-warn">
                    {t("research.workflow.legacyCitationBoundary", {
                      defaultValue:
                        "Legacy citation: the displayed source title is current metadata and was not frozen with this result.",
                    })}
                  </span>
                )}
              </span>
            </button>
          );
        })}
      </div>
    </section>
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
    <div className="mb-3 rounded-input border border-border-faint bg-bg px-3 py-2 text-[11px] leading-relaxed text-muted">
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
          "mt-2 rounded-input px-2 py-1 text-[10px] ring-1",
          result.integrityStatus === "verified-frozen-v2"
            ? "bg-ok/5 text-ok ring-ok/20"
            : "bg-warn/5 text-warn ring-warn/20",
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
      <dl className="mt-2 grid gap-2 border-t border-border-faint pt-2 sm:grid-cols-3">
        <div className="min-w-0">
          <dt className="text-[9px] font-medium uppercase tracking-wider text-muted">
            {t("research.workflow.resultGenerator", {
              defaultValue: "Generator",
            })}
          </dt>
          <dd className="mt-0.5 break-words font-mono text-[10px] text-text">
            {result.generator}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-[9px] font-medium uppercase tracking-wider text-muted">
            {t("research.workflow.model", { defaultValue: "Model" })}
          </dt>
          <dd className="mt-0.5 break-words font-mono text-[10px] text-text">
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
          <dt className="text-[9px] font-medium uppercase tracking-wider text-muted">
            {t("research.workflow.promptVersion", {
              defaultValue: "Prompt version",
            })}
          </dt>
          <dd className="mt-0.5 break-words font-mono text-[10px] text-text">
            {result.promptVersion ??
              t("research.workflow.notReported", {
                defaultValue: "Not reported (legacy snapshot)",
              })}
          </dd>
        </div>
      </dl>
    </div>
  );
}
