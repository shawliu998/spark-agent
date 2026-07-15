import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, ShieldCheck, XCircle } from "lucide-react";
import type {
  DatasetAnalysisReview,
  DatasetAnalysisReviewIssue,
  ResearchWorkflowReview,
} from "@spark/research-domain";
import { statusLabel } from "./workflowModel";

export function WorkflowReviewSummary({
  review,
}: {
  review: ResearchWorkflowReview | DatasetAnalysisReview | null;
}) {
  const { t } = useTranslation("pages");
  if (!review) {
    return (
      <p className="px-4 py-6 text-center text-xs text-muted">
        {t("research.workflow.noReview", {
          defaultValue: "No review has been recorded yet.",
        })}
      </p>
    );
  }
  const isDatasetReview = review.reviewType === "deterministic-analysis-v1";
  const frozenReview =
    "resultSnapshotSha256" in review.result &&
    review.result.schemaVersion === "2";

  return (
    <div className="space-y-3 p-4">
      <div className="flex items-center gap-2">
        <ShieldCheck
          size={15}
          className={review.verdict === "passed" ? "text-ok" : "text-warn"}
        />
        <div>
          <p className="text-[10px] uppercase tracking-wider text-muted">
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
          <p className="text-sm font-medium text-text">
            {t(`research.reviewVerdict.${review.verdict}`, {
              defaultValue: statusLabel(review.verdict),
            })}
          </p>
        </div>
      </div>
      <p className="text-[11px] leading-relaxed text-muted">
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
        <p className="break-all rounded-input border border-ok/20 bg-ok/5 px-3 py-2 font-mono text-[9px] text-ok">
          {t("research.workflow.resultSnapshotHash", {
            defaultValue: "Frozen result SHA-256",
          })}
          : {review.result.resultSnapshotSha256}
        </p>
      ) : !isDatasetReview ? (
        <p className="rounded-input border border-warn/25 bg-warn/5 px-3 py-2 text-[10px] leading-relaxed text-warn">
          {t("research.workflow.legacyReviewBoundary", {
            defaultValue:
              "This historical review has no immutable result snapshot. Its live result remains available for compatibility but does not carry the v2 frozen-result guarantee.",
          })}
        </p>
      ) : null}
      <ul className="space-y-2">
        {review.result.checks.map((check, index) => {
          const scope =
            "claimId" in check
              ? (check.claimId ?? check.evidenceId)
              : check.artifactId;
          return (
            <li
              key={`${check.code}:${scope ?? index}`}
              className="rounded-input border border-border bg-bg px-3 py-2.5"
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
      {isDatasetReview && "artifactIssues" in review.result && (
        <div className="space-y-3">
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
          <dl className="grid gap-2 rounded-input border border-border-faint bg-bg p-3 font-mono text-[9px] text-muted">
            <div>
              <dt className="font-sans font-medium uppercase tracking-wider">
                {t("research.workflow.reviewedRun", {
                  defaultValue: "Reviewed run",
                })}
              </dt>
              <dd className="mt-0.5 break-all text-text">
                {review.result.runId}
              </dd>
            </div>
            <div>
              <dt className="font-sans font-medium uppercase tracking-wider">
                {t("research.workflow.reviewedIntent", {
                  defaultValue: "Reviewed intent",
                })}
              </dt>
              <dd className="mt-0.5 break-all text-text">
                {review.result.analysisIntentId}
              </dd>
            </div>
            <div>
              <dt className="font-sans font-medium uppercase tracking-wider">
                {t("research.workflow.reviewedDatasetHash", {
                  defaultValue: "Input dataset SHA-256",
                })}
              </dt>
              <dd className="mt-0.5 break-all text-text">
                {review.result.inputDatasetContentHash}
              </dd>
            </div>
          </dl>
        </div>
      )}
      {review.result.requiredRevisions.length > 0 && (
        <div className="rounded-input border border-warn/25 bg-warn/5 px-3 py-2.5">
          <p className="text-[10px] font-medium uppercase tracking-wider text-muted">
            {t("research.workflow.requiredRevisions", {
              defaultValue: "Required revisions",
            })}
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-relaxed text-muted">
            {review.result.requiredRevisions.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
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
      <p className="text-[10px] font-medium uppercase tracking-wider text-muted">
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
                <span className="mt-0.5 block break-all font-mono text-[9px]">
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
