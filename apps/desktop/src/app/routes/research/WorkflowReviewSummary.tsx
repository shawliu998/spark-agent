import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, ShieldCheck, XCircle } from "lucide-react";
import type {
  DatasetAnalysisReview,
  DatasetAnalysisReviewIssue,
  ResearchWorkflowResult,
  ResearchWorkflowReview,
  WorkflowEvidenceRelationship,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";
import { statusLabel } from "./workflowModel";

export function WorkflowReviewSummary({
  review,
  result = null,
  onSelectEvidence,
}: {
  review: ResearchWorkflowReview | DatasetAnalysisReview | null;
  result?: ResearchWorkflowResult | null;
  onSelectEvidence?: (evidence: WorkflowEvidenceRelationship) => void;
}) {
  const { t } = useTranslation("pages");
  if (!review) {
    return (
      <div className="p-4">
        <div className="flex items-start gap-2 border-b border-border pb-3">
          <ShieldCheck size={15} className="mt-0.5 shrink-0 text-muted" />
          <div>
            <p className="text-sm font-medium text-text">
              {t("research.workflow.reviewPendingTitle", {
                defaultValue: "Deterministic review pending",
              })}
            </p>
            <p className="mt-1 text-xs leading-5 text-muted">
              {t("research.workflow.noReview", {
                defaultValue: "No review has been recorded yet. Treat current conclusions as provisional.",
              })}
            </p>
          </div>
        </div>
        {result && <ClaimReviewSection result={result} provisional />}
      </div>
    );
  }
  const isDatasetReview = review.reviewType === "deterministic-analysis-v1";
  const frozenReview =
    "resultSnapshotSha256" in review.result &&
    review.result.schemaVersion === "2";
  const reviewedResult =
    !isDatasetReview && "resultSnapshot" in review.result
      ? review.result.resultSnapshot ?? result
      : null;
  const passedChecks = review.result.checks.filter((check) => check.status === "passed").length;
  const warningChecks = review.result.checks.filter((check) => check.status === "warning").length;
  const failedChecks = review.result.checks.length - passedChecks - warningChecks;
  const errorVerdict = review.verdict === "failed" || review.verdict === "blocked";

  return (
    <div className="space-y-3 p-4">
      <div className="flex items-start gap-2 border-b border-border pb-3">
        <ShieldCheck
          size={15}
          className={review.verdict === "passed" ? "text-ok" : errorVerdict ? "text-error" : "text-warn"}
        />
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium text-muted">
            {isDatasetReview
              ? t("research.workflow.deterministicAnalysisReview", {
                  defaultValue: "Deterministic analysis integrity review",
                })
              : review.reviewType.startsWith("deterministic-claims-")
              ? frozenReview
                ? t("research.workflow.deterministicReview", {
                    defaultValue:
                      "Deterministic frozen-result integrity review",
                  })
                : t("research.workflow.legacyDeterministicReview", {
                    defaultValue:
                      "Legacy deterministic review — result not frozen",
                  })
              : review.reviewType}
          </p>
          <p
            role="status"
            aria-live="polite"
            className={cn("text-sm font-medium", errorVerdict ? "text-error" : "text-text")}
          >
            {t(`research.reviewVerdict.${review.verdict}`, {
              defaultValue: statusLabel(review.verdict),
            })}
          </p>
          <p className="mt-1 text-caption text-muted">
            {t("research.workflow.checkSummary", {
              defaultValue: "{{passed}} passed · {{warnings}} warnings · {{failed}} failed",
              passed: passedChecks,
              warnings: warningChecks,
              failed: failedChecks,
            })}
          </p>
        </div>
      </div>
      <p className="max-w-[70ch] text-ui leading-relaxed text-muted">
        {isDatasetReview
          ? t("research.workflow.analysisReviewBoundary", {
              defaultValue:
                "This deterministic review validates input, execution, and artifact integrity. It does not by itself establish that the selected statistical method is scientifically correct.",
            })
          : t("research.workflow.reviewBoundary", {
              defaultValue:
                "This deterministic review validates citation linkage and evidence integrity. It does not establish the scientific correctness, methodological quality, or generalizability of a conclusion, and it does not treat a model confidence score as evidence strength.",
            })}
      </p>
      {frozenReview &&
      "resultSnapshotSha256" in review.result &&
      review.result.resultSnapshotSha256 ? (
        <details className="border-y border-ok/20 py-1 text-caption text-ok">
          <summary className="min-h-8 cursor-pointer py-1.5 font-medium">
            {t("research.workflow.resultSnapshotHash", {
              defaultValue: "Frozen result SHA-256",
            })}
          </summary>
          <p className="break-all pb-2 font-mono text-text">
            {review.result.resultSnapshotSha256}
          </p>
        </details>
      ) : !isDatasetReview ? (
        <p className="rounded-input border border-warn/25 bg-warn/5 px-3 py-2 text-caption leading-relaxed text-warn">
          {t("research.workflow.legacyReviewBoundary", {
            defaultValue:
              "This historical review has no immutable result snapshot. Its live result remains available for compatibility but does not carry the v2 frozen-result guarantee.",
          })}
        </p>
      ) : null}
      {review.result.requiredRevisions.length > 0 && (
        <section className="border-y border-warn/25 bg-warn/5 px-3 py-2.5">
          <p className="text-xs font-medium text-text">
            {t("research.workflow.requiredRevisions", {
              defaultValue: "Required revisions",
            })}
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-relaxed text-muted">
            {review.result.requiredRevisions.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      )}
      {reviewedResult && (
        <ClaimReviewSection
          result={reviewedResult}
          frozen={frozenReview}
          onSelectEvidence={onSelectEvidence}
        />
      )}
      <section>
        <h3 className="pb-2 text-xs font-medium text-muted">
          {t("research.workflow.deterministicChecks", {
            defaultValue: "Deterministic checks ({{count}})",
            count: review.result.checks.length,
          })}
        </h3>
        {review.result.checks.length === 0 && (
          <p className="border-y border-border-faint py-3 text-xs text-muted">
            {t("research.workflow.noChecksRecorded", {
              defaultValue: "No individual checks were recorded.",
            })}
          </p>
        )}
        <ul className="divide-y divide-border-faint border-y border-border-faint">
        {review.result.checks.map((check, index) => {
          const scope =
            "claimId" in check
              ? (check.claimId ?? check.evidenceId)
              : check.artifactId;
          return (
            <li
              key={`${check.code}:${scope ?? index}`}
              className="py-3"
            >
              <div className="flex items-start gap-2">
                {check.status === "passed" ? (
                  <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-ok" />
                ) : check.status === "warning" ? (
                  <AlertTriangle size={13} className="mt-0.5 shrink-0 text-warn" />
                ) : (
                  <XCircle size={13} className="mt-0.5 shrink-0 text-error" />
                )}
                <p className="text-xs leading-relaxed text-muted">{check.message}</p>
              </div>
            </li>
          );
        })}
        </ul>
      </section>
      {isDatasetReview && "artifactIssues" in review.result && (
        <div className="space-y-3">
          {review.result.conclusion && (
            <div className="border-y border-border-faint py-3">
              <p className="text-xs font-medium text-muted">
                {t("research.workflow.reviewConclusion", {
                  defaultValue: "Deterministic reviewer conclusion",
                })}
              </p>
              <p className="mt-1 text-xs leading-relaxed text-text">
                {review.result.conclusion}
              </p>
            </div>
          )}
          <DatasetIssueSection
            label={t("research.workflow.artifactIssues", {
              defaultValue: "Artifact issues",
            })}
            issues={review.result.artifactIssues}
          />
          <DatasetIssueSection
            label={t("research.workflow.numericIssues", {
              defaultValue: "Numeric issues",
            })}
            issues={review.result.numericIssues}
          />
          <DatasetIssueSection
            label={t("research.workflow.methodWarnings", {
              defaultValue: "Method warnings",
            })}
            issues={review.result.methodWarnings}
            warning
          />
          <details className="border-t border-border-faint">
            <summary className="flex min-h-10 cursor-pointer items-center text-xs font-medium text-link hover:underline">
              {t("research.workflow.auditDetails", { defaultValue: "Audit details" })}
            </summary>
          <dl className="grid gap-3 pb-2 font-mono text-caption text-muted">
            <div>
              <dt className="font-sans text-xs font-medium">
                {t("research.workflow.reviewedRun", {
                  defaultValue: "Reviewed run",
                })}
              </dt>
              <dd className="mt-0.5 break-all text-text">
                {review.result.runId}
              </dd>
            </div>
            <div>
              <dt className="font-sans text-xs font-medium">
                {t("research.workflow.reviewedIntent", {
                  defaultValue: "Reviewed intent",
                })}
              </dt>
              <dd className="mt-0.5 break-all text-text">
                {review.result.analysisIntentId}
              </dd>
            </div>
            <div>
              <dt className="font-sans text-xs font-medium">
                {t("research.workflow.reviewedDatasetHash", {
                  defaultValue: "Input dataset SHA-256",
                })}
              </dt>
              <dd className="mt-0.5 break-all text-text">
                {review.result.inputDatasetContentHash}
              </dd>
            </div>
            {review.result.analysisSpecId && (
              <div>
                <dt className="font-sans text-xs font-medium">
                  {t("research.workflow.reviewedAnalysisSpec", {
                    defaultValue: "Reviewed AnalysisSpec",
                  })}
                </dt>
                <dd className="mt-0.5 break-all text-text">
                  {review.result.analysisSpecId}
                </dd>
              </div>
            )}
            {review.result.structuredResultSha256 && (
              <div>
                <dt className="font-sans text-xs font-medium">
                  {t("research.workflow.reviewedStructuredResult", {
                    defaultValue: "Structured result SHA-256",
                  })}
                </dt>
                <dd className="mt-0.5 break-all text-text">
                  {review.result.structuredResultSha256}
                </dd>
              </div>
            )}
          </dl>
          </details>
        </div>
      )}
    </div>
  );
}

function ClaimReviewSection({
  result,
  frozen = false,
  provisional = false,
  onSelectEvidence,
}: {
  result: ResearchWorkflowResult;
  frozen?: boolean;
  provisional?: boolean;
  onSelectEvidence?: (evidence: WorkflowEvidenceRelationship) => void;
}) {
  const { t } = useTranslation("pages");
  return (
    <section className="mt-3">
      <div className="flex items-end justify-between gap-3 pb-2">
        <div>
          <h3 className="text-xs font-medium text-text">
            {t("research.workflow.claimEvidenceReview", {
              defaultValue: "Claim–evidence review",
            })}
          </h3>
          <p className="mt-0.5 text-caption text-muted">
            {provisional
              ? t("research.workflow.provisionalClaims", {
                  defaultValue: "Unreviewed current result",
                })
              : frozen
                ? t("research.workflow.frozenClaims", {
                    defaultValue: "Claims from the frozen reviewed result",
                  })
                : t("research.workflow.currentClaims", {
                    defaultValue: "Current claims shown for orientation",
                  })}
          </p>
        </div>
        <span className="shrink-0 text-caption text-muted">
          {t("research.claimsHeading", {
            defaultValue: "{{count}} claims",
            count: result.claims.length,
          })}
        </span>
      </div>
      <ol className="divide-y divide-border border-y border-border">
        {result.claims.map((claim, index) => {
          const isSupported = claim.supportStatus === "supported";
          const needsAttention =
            claim.supportStatus === "contradicted" ||
            claim.supportStatus === "insufficient-evidence";
          return (
            <li key={claim.id || index} className="py-3">
              <div className="flex items-start gap-2.5">
                <span className="w-6 shrink-0 pt-0.5 font-mono text-caption text-muted">
                  C{index + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-medium leading-5 text-text">{claim.statement}</p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-caption">
                    <span
                      className={cn(
                        "inline-flex items-center gap-1.5 font-medium",
                        isSupported
                          ? "text-ok"
                          : needsAttention
                            ? "text-error"
                            : "text-warn",
                      )}
                    >
                      <span
                        aria-hidden="true"
                        className={cn(
                          "h-1.5 w-1.5 rounded-full",
                          isSupported
                            ? "bg-ok"
                            : needsAttention
                              ? "bg-error"
                              : "bg-warn",
                        )}
                      />
                      {t(`research.claimSupport.${claim.supportStatus}`, {
                        defaultValue: statusLabel(claim.supportStatus),
                      })}
                    </span>
                    <span className="text-muted">
                      {t("research.workflow.evidenceCount", {
                        defaultValue: "{{count}} passages",
                        count: claim.evidence.length,
                      })}
                    </span>
                  </div>
                  {claim.evidence.length === 0 ? (
                    <p className="mt-2 text-caption text-warn">
                      {t("research.noEvidence", {
                        defaultValue: "No verified source passage attached.",
                      })}
                    </p>
                  ) : (
                    <ul className="mt-2 divide-y divide-border-faint border-t border-border-faint">
                      {claim.evidence.map((evidence) => (
                        <li key={evidence.evidenceId}>
                          <button
                            type="button"
                            disabled={!onSelectEvidence}
                            onClick={() => onSelectEvidence?.(evidence)}
                            className="group flex min-h-11 w-full items-center gap-2 py-1.5 text-left text-caption text-muted hover:text-text disabled:cursor-default"
                            aria-label={t("research.workflow.openEvidence", {
                              defaultValue: "Open {{source}}, page {{page}}",
                              source: evidence.sourceTitle ?? evidence.sourceId,
                              page: evidence.pageLabel ?? evidence.pageIndex + 1,
                            })}
                          >
                            <span
                              className={cn(
                                "shrink-0 font-medium",
                                evidence.relationship === "contradicting"
                                  ? "text-warn"
                                  : "text-accent",
                              )}
                            >
                              {evidence.relationship === "contradicting"
                                ? t("research.evidenceRelationship.contradicting", {
                                    defaultValue: "Contradicts",
                                  })
                                : t("research.evidenceRelationship.supporting", {
                                    defaultValue: "Supports",
                                  })}
                            </span>
                            <span className="min-w-0 flex-1 truncate">
                              {evidence.sourceTitle ?? evidence.sourceId}
                            </span>
                            <span className="shrink-0">
                              {t("research.previewPage", {
                                defaultValue: "Page {{page}}",
                                page: evidence.pageLabel ?? evidence.pageIndex + 1,
                              })}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
      {result.unresolvedQuestions.length > 0 && (
        <div className="border-b border-warn/25 bg-warn/5 px-3 py-2.5">
          <p className="text-xs font-medium text-text">
            {t("research.unresolvedHeading", { defaultValue: "Unresolved questions" })}
          </p>
          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-caption leading-5 text-muted">
            {result.unresolvedQuestions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function DatasetIssueSection({
  label,
  issues,
  warning = false,
}: {
  label: string;
  issues: DatasetAnalysisReviewIssue[];
  warning?: boolean;
}) {
  const { t } = useTranslation("pages");
  if (issues.length === 0) return null;
  return (
    <div
      className={
        warning
          ? "rounded-input border border-warn/25 bg-warn/5 p-3"
          : "rounded-input border border-error/25 bg-error/5 p-3"
      }
    >
      <p className="text-xs font-medium text-muted">
        {label}
      </p>
      <ul className="mt-2 space-y-1.5">
        {issues.map((issue, index) => (
          <li
            key={`${issue.code}:${issue.artifactId ?? index}`}
            className="flex items-start gap-2 text-xs leading-relaxed text-muted"
          >
            <AlertTriangle
              size={13}
              className={
                warning
                  ? "mt-0.5 shrink-0 text-warn"
                  : "mt-0.5 shrink-0 text-error"
              }
            />
            <span>
              <strong className="font-medium text-text">{issue.code}</strong>: {issue.message}
              {issue.artifactId && (
                <span className="mt-0.5 block break-all font-mono text-caption">
                  {t("research.workflow.reviewArtifactId", {
                    defaultValue: "Artifact: {{id}}",
                    id: issue.artifactId,
                  })}
                </span>
              )}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
