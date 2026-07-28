import { ArrowRight, GitCompareArrows } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { AnalysisSpecDiff } from "@spark/research-domain";
import { formatAgentLoopValue } from "./formatAgentLoopValue";

export function AnalysisSpecDiffCard({ diff }: { diff: AnalysisSpecDiff }) {
  const { t } = useTranslation("pages");

  return (
    <section className="border-t border-border-faint pt-3">
      <div className="flex items-center gap-2">
        <GitCompareArrows size={14} className="text-warn" />
        <h4 className="text-xs font-medium text-text">
          {t("research.workflow.agentLoop.specDiff", {
            defaultValue: "AnalysisSpec changes",
          })}
        </h4>
      </div>
      <div className="mt-2 space-y-3">
        <p className="text-xs leading-relaxed text-muted">{diff.reason}</p>
        <div className="overflow-x-auto rounded-input border border-border-faint bg-bg">
          <table className="min-w-full text-left text-xs">
            <thead className="bg-surface-2 text-caption text-muted">
              <tr>
                <th className="px-3 py-2">
                  {t("research.workflow.agentLoop.field", {
                    defaultValue: "Field",
                  })}
                </th>
                <th className="px-3 py-2">
                  {t("research.workflow.agentLoop.previousValue", {
                    defaultValue: "Previous",
                  })}
                </th>
                <th className="w-7 px-1 py-2" aria-hidden="true" />
                <th className="px-3 py-2">
                  {t("research.workflow.agentLoop.proposedValue", {
                    defaultValue: "Proposed",
                  })}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-faint text-text">
              {diff.changedFields.map((field) => (
                <tr key={field}>
                  <th className="px-3 py-2 font-mono text-caption font-medium">
                    {field}
                  </th>
                  <td className="max-w-64 break-words px-3 py-2 font-mono text-caption text-muted">
                    {formatAgentLoopValue(diff.previousValues[field] ?? null)}
                  </td>
                  <td className="px-1 py-2 text-muted" aria-hidden="true">
                    <ArrowRight size={12} />
                  </td>
                  <td className="max-w-64 break-words px-3 py-2 font-mono text-caption">
                    {formatAgentLoopValue(diff.proposedValues[field] ?? null)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
