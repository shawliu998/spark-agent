import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  Eye,
  FileOutput,
  XCircle,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import type {
  ObservationWarning,
  StepObservationOut,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";
import { formatAgentLoopValue } from "./formatAgentLoopValue";

export function ObservationCard({
  observation,
}: {
  observation: StepObservationOut;
}) {
  const { t } = useTranslation("pages");
  const successful =
    observation.status === "succeeded" || observation.status === "needs-review";

  return (
    <section className="overflow-hidden rounded-card border border-border bg-surface">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <Eye size={15} className={successful ? "text-ok" : "text-warn"} />
        <h3 className="text-sm font-medium text-text">
          {t("research.workflow.agentLoop.observation", {
            defaultValue: "Agent observation",
          })}
        </h3>
        <span className="font-mono text-caption text-muted">
          {observation.stepKey}
        </span>
        <span
          role="status"
          aria-live="polite"
          className={cn(
            "ml-auto rounded-full px-2 py-0.5 text-caption font-medium",
            successful ? "bg-ok/10 text-ok" : "bg-warn/10 text-warn",
          )}
        >
          {t(`research.workflow.agentLoop.observationStatus.${observation.status}`)}
        </span>
      </div>

      <div className="space-y-4 p-4">
        <dl className="grid gap-2 text-caption sm:grid-cols-3">
          <ObservationMetadata
            label={t("research.workflow.agentLoop.observationType", {
              defaultValue: "Observation type",
            })}
            value={t(`research.workflow.agentLoop.observationTypeValue.${observation.observationType}`)}
          />
          <ObservationMetadata
            label={t("research.workflow.agentLoop.attempt", {
              defaultValue: "Attempt",
            })}
            value={String(observation.attempt)}
          />
          <ObservationMetadata
            label={t("research.workflow.agentLoop.failureCategory", {
              defaultValue: "Failure category",
            })}
            value={t(`research.workflow.agentLoop.failureCategoryValue.${observation.failureCategory}`)}
          />
        </dl>

        <div>
          <SectionLabel
            label={t("research.workflow.agentLoop.facts", {
              defaultValue: "Structured facts",
            })}
          />
          <ul className="mt-2 divide-y divide-border-faint border-y border-border-faint">
            {observation.facts.map((fact) => (
              <li key={fact.code} className="py-2.5">
                <div className="flex items-start gap-2">
                  <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-ok" />
                  <div className="min-w-0 flex-1">
                    <p className="text-xs leading-relaxed text-text">
                      {fact.statement}
                    </p>
                    <details className="mt-1 text-caption text-muted">
                      <summary className="min-h-7 cursor-pointer py-1 font-mono text-link">
                        {fact.code}
                      </summary>
                      <p className="break-all pb-1 font-mono">
                        {formatAgentLoopValue(fact.value)} · {fact.sourceType}:{" "}
                        {fact.sourceId}
                      </p>
                    </details>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>

        {observation.warnings.length > 0 && (
          <div>
            <SectionLabel
              label={t("research.workflow.agentLoop.warnings", {
                defaultValue: "Warnings",
              })}
            />
            <ul className="mt-2 space-y-2">
              {observation.warnings.map((warning) => (
                <ObservationWarningRow key={warning.code} warning={warning} />
              ))}
            </ul>
          </div>
        )}

        {observation.unresolvedQuestions.length > 0 && (
          <div>
            <SectionLabel
              label={t("research.workflow.agentLoop.unresolvedQuestions", {
                defaultValue: "Unresolved questions",
              })}
            />
            <ul className="mt-2 space-y-1.5">
              {observation.unresolvedQuestions.map((question) => (
                <li
                  key={question.code}
                  className="flex items-start gap-2 rounded-input border border-warn/20 bg-warn/5 px-3 py-2 text-xs text-muted"
                >
                  <CircleHelp size={13} className="mt-0.5 shrink-0 text-warn" />
                  <span>
                    {question.question}{" "}
                    <span className="font-mono text-caption">
                      ({question.answerType})
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {observation.artifactIds.length > 0 && (
          <div>
            <SectionLabel
              label={t("research.workflow.agentLoop.artifacts", {
                defaultValue: "Referenced artifacts",
              })}
            />
            <ul className="mt-2 divide-y divide-border-faint border-y border-border-faint">
              {observation.artifactIds.map((artifactId) => (
                <li
                  key={artifactId}
                  className="flex min-w-0 items-center gap-2 py-2 font-mono text-caption text-muted"
                >
                  <FileOutput size={12} className="shrink-0" />
                  <span className="truncate">{artifactId}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div>
          <SectionLabel
            label={t("research.workflow.agentLoop.recommendedActions", {
              defaultValue: "Recommended actions",
            })}
          />
          <ul className="mt-2 divide-y divide-border-faint border-y border-border-faint">
            {observation.recommendedActions.map((action) => (
              <li key={action} className="flex items-center gap-2 py-2 text-xs text-text">
                <ChevronRight size={12} className="shrink-0 text-accent" />
                {t(`research.workflow.agentLoop.actions.${action}`)}
              </li>
            ))}
          </ul>
        </div>

        <details className="border-t border-border-faint pt-2 text-caption text-muted">
          <summary className="min-h-8 cursor-pointer py-1.5 font-mono text-link">
            {observation.generator}
          </summary>
          <p className="break-all pb-1 font-mono">
            {t("research.workflow.agentLoop.observationLineage", {
              defaultValue: "Observation {{id}} · source job {{jobId}}",
              id: observation.id,
              jobId: observation.sourceJobId,
            })}
          </p>
        </details>
      </div>
    </section>
  );
}

function ObservationWarningRow({ warning }: { warning: ObservationWarning }) {
  const severe = warning.severity === "error";
  return (
    <li
      className={cn(
        "flex items-start gap-2 rounded-input border px-3 py-2.5",
        severe
          ? "border-error/25 bg-error/5"
          : "border-warn/25 bg-warn/5",
      )}
    >
      {severe ? (
        <XCircle size={13} className="mt-0.5 shrink-0 text-error" />
      ) : (
        <AlertTriangle size={13} className="mt-0.5 shrink-0 text-warn" />
      )}
      <div className="min-w-0">
        <p className="text-xs leading-relaxed text-text">{warning.message}</p>
        {warning.sourceId && (
          <details className="mt-1 text-caption text-muted">
            <summary className="min-h-7 cursor-pointer py-1 font-mono text-link">
              {warning.code}
            </summary>
            <p className="break-all pb-1 font-mono">{warning.sourceId}</p>
          </details>
        )}
      </div>
    </li>
  );
}

function ObservationMetadata({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-medium text-muted">{label}</dt>
      <dd className="mt-1 text-xs text-text">{value}</dd>
    </div>
  );
}

function SectionLabel({ label }: { label: string }) {
  return (
    <p className="text-caption font-medium text-muted">
      {label}
    </p>
  );
}
