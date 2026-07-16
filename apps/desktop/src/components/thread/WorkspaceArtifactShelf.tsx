import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ArtifactBlock } from "@ai4s/shared";
import { ArtifactCard } from "./ArtifactCard";

/** Reconstructed workspace outputs that were not already surfaced by a live
 * tool event. Kept in the conversation so shell/Python results are visible
 * without first opening the full Files browser. */
export function WorkspaceArtifactShelf({
  artifacts,
  onOpen,
  onRefresh,
}: {
  artifacts: ArtifactBlock[];
  onOpen: (artifact: ArtifactBlock) => void;
  onRefresh: () => void;
}) {
  const { t } = useTranslation("session");
  if (artifacts.length === 0) return null;

  return (
    <section className="mt-2 space-y-2" aria-labelledby="workspace-artifacts-heading">
      <div className="flex items-center gap-2 px-1 text-xs text-muted">
        <h2 id="workspace-artifacts-heading" className="font-medium text-text">
          {t("workspaceArtifacts.heading")}
        </h2>
        <span>{t("workspaceArtifacts.count", { count: artifacts.length })}</span>
        <button
          type="button"
          className="ml-auto rounded p-1 hover:bg-surface-2 hover:text-text"
          onClick={onRefresh}
          aria-label={t("workspaceArtifacts.refreshAria")}
        >
          <RefreshCw size={12} />
        </button>
      </div>
      <div className="space-y-1.5" data-workspace-artifacts>
        {artifacts.slice(0, 8).map((artifact) => (
          <ArtifactCard key={artifact.path} block={artifact} onOpen={onOpen} />
        ))}
      </div>
    </section>
  );
}
