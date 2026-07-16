import { BookOpenCheck, Bot, Boxes, ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { AgentInfo, ProviderInfo } from "@ai4s/sdk";
import { cn } from "@/lib/cn";

export type ResearchExecutionMode = "general" | "sandbox" | "verified";

export interface RuntimeModelOption {
  value: string;
  label: string;
}

/** Flatten only models reported by OpenCode; a configured default is also a
 * real runtime value and remains visible while provider discovery catches up. */
export function runtimeModelOptions(
  providers: ProviderInfo[],
  defaultModel: string | null,
): RuntimeModelOption[] {
  const options = providers.flatMap((provider) =>
    provider.models.map((model) => ({
      value: `${provider.id}/${model.id}`,
      label: model.name === model.id ? `${provider.name} · ${model.id}` : model.name,
    })),
  );
  const seen = new Set<string>();
  const unique = options.filter((option) => {
    if (seen.has(option.value)) return false;
    seen.add(option.value);
    return true;
  });
  if (defaultModel && !seen.has(defaultModel)) {
    unique.unshift({ value: defaultModel, label: defaultModel });
  }
  return unique;
}

/** Runtime-backed controls for a General Research turn. Native selects stay
 * keyboard-accessible and make an empty catalog explicit instead of inventing
 * agents or models that OpenCode did not report. */
export function ResearchSessionControls({
  mode,
  onModeChange,
  agents,
  selectedAgent,
  onAgentChange,
  providers,
  selectedModel,
  onModelChange,
  disabled,
  skillCount,
  onOpenSkills,
}: {
  mode: ResearchExecutionMode;
  onModeChange: (mode: ResearchExecutionMode) => void;
  agents: AgentInfo[];
  selectedAgent: string | null;
  onAgentChange: (agent: string) => void;
  providers: ProviderInfo[];
  selectedModel: string | null;
  onModelChange: (model: string) => void;
  disabled?: boolean;
  skillCount: number;
  onOpenSkills: () => void;
}) {
  const { t } = useTranslation("session");
  const models = runtimeModelOptions(providers, selectedModel);
  const primaryAgents = agents.filter((agent) => agent.mode === "primary" || agent.mode === "all");
  const subagents = agents.filter((agent) => agent.mode === "subagent");
  const otherAgents = agents.filter(
    (agent) => agent.mode !== "primary" && agent.mode !== "all" && agent.mode !== "subagent",
  );
  const agentGroups = [
    { label: t("researchControls.agent.primaryGroup"), agents: primaryAgents },
    { label: t("researchControls.agent.subagentGroup"), agents: subagents },
    { label: t("researchControls.agent.otherGroup"), agents: otherAgents },
  ].filter((group) => group.agents.length > 0);
  const selectClass = cn(
    "h-7 appearance-none rounded-full bg-transparent py-0 pl-7 pr-6 text-xs text-muted outline-none",
    "hover:bg-surface-2 hover:text-text focus-visible:ring-1 focus-visible:ring-accent",
    "disabled:cursor-not-allowed disabled:opacity-45",
  );

  return (
    <div className="flex min-w-0 items-center gap-1" data-research-session-controls>
      <label className="relative min-w-0">
        <Boxes
          size={13}
          className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted"
        />
        <select
          aria-label={t("researchControls.mode.aria")}
          value={mode}
          onChange={(event) => onModeChange(event.target.value as ResearchExecutionMode)}
          disabled={disabled}
          className={cn(selectClass, "max-w-[132px]")}
        >
          <option value="general">{t("researchControls.mode.general")}</option>
          <option value="sandbox">{t("researchControls.mode.sandbox")}</option>
          <option value="verified">{t("researchControls.mode.verified")}</option>
        </select>
        <ChevronDown
          size={11}
          className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-muted"
        />
      </label>

      <label className="relative min-w-0">
        <Bot
          size={13}
          className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted"
        />
        <select
          aria-label={t("researchControls.agent.aria")}
          value={selectedAgent ?? ""}
          onChange={(event) => onAgentChange(event.target.value)}
          disabled={disabled || agents.length === 0}
          className={cn(selectClass, "max-w-[150px]")}
        >
          {agents.length === 0 && (
            <option value="">{t("researchControls.agent.empty")}</option>
          )}
          {agentGroups.map((group) => (
            <optgroup key={group.label} label={group.label}>
              {group.agents.map((agent) => (
                <option key={agent.name} value={agent.name} title={agent.description}>
                  {agent.description ? `${agent.name} — ${agent.description}` : agent.name}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <ChevronDown
          size={11}
          className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-muted"
        />
      </label>

      <label className="relative min-w-0">
        <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 font-mono text-[10px] text-muted">
          M
        </span>
        <select
          aria-label={t("researchControls.model.aria")}
          value={selectedModel ?? ""}
          onChange={(event) => onModelChange(event.target.value)}
          disabled={disabled || models.length === 0}
          className={cn(selectClass, "max-w-[190px]")}
        >
          {models.length === 0 && (
            <option value="">{t("researchControls.model.empty")}</option>
          )}
          {models.map((model) => (
            <option key={model.value} value={model.value}>
              {model.label}
            </option>
          ))}
        </select>
        <ChevronDown
          size={11}
          className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-muted"
        />
      </label>

      <button
        type="button"
        onClick={onOpenSkills}
        disabled={disabled}
        className="flex h-7 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-xs text-muted hover:bg-surface-2 hover:text-text disabled:opacity-45"
        aria-label={t("researchControls.skills.aria")}
        title={t("researchControls.skills.title")}
      >
        <BookOpenCheck size={13} />
        <span>{t("researchControls.skills.count", { count: skillCount })}</span>
      </button>
    </div>
  );
}
