import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { HelpCircle, Loader2 } from "lucide-react";
import type {
  InteractionRequest,
  InteractionResponseValue,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";

interface ClarificationCardProps {
  interaction: InteractionRequest;
  mutating: boolean;
  onRespond: (
    interactionId: string,
    response: InteractionResponseValue,
  ) => Promise<void>;
}

function isMultiValueRequest(interaction: InteractionRequest): boolean {
  return (
    interaction.requestType === "multi-choice" ||
    (interaction.requestType === "column-selection" &&
      interaction.responseSchema.type === "array")
  );
}

function usesBooleanChoice(interaction: InteractionRequest): boolean {
  return (
    interaction.requestType === "boolean" ||
    ((interaction.requestType === "method-confirmation" ||
      interaction.requestType === "assumption-confirmation") &&
      interaction.options.length === 0)
  );
}

export function ClarificationCard({
  interaction,
  mutating,
  onRespond,
}: ClarificationCardProps) {
  const { t } = useTranslation("pages");
  const options = useMemo(
    () => interaction.options,
    [interaction.options],
  );
  const hasOptions = options.length > 0;
  const multiValue = isMultiValueRequest(interaction);
  const booleanChoice = usesBooleanChoice(interaction);
  const savedResponseJson = JSON.stringify(
    interaction.latestResponse?.response ?? null,
  );
  const [textValue, setTextValue] = useState("");
  const [selectedValues, setSelectedValues] = useState<string[]>([]);
  const [booleanValue, setBooleanValue] = useState<boolean | null>(null);

  useEffect(() => {
    const existing: unknown = JSON.parse(savedResponseJson);
    if (Array.isArray(existing)) {
      setTextValue("");
      setSelectedValues(existing);
      setBooleanValue(null);
    } else if (typeof existing === "boolean") {
      setTextValue("");
      setSelectedValues([]);
      setBooleanValue(existing);
    } else {
      setTextValue(existing == null ? "" : String(existing));
      setSelectedValues(
        typeof existing === "string" && hasOptions ? [existing] : [],
      );
      setBooleanValue(null);
    }
  }, [hasOptions, interaction.id, savedResponseJson]);

  const response = (): InteractionResponseValue | null => {
    if (multiValue) return selectedValues.length > 0 ? selectedValues : null;
    if (booleanChoice) return booleanValue;
    if (interaction.requestType === "number") {
      if (textValue.trim() === "") return null;
      const parsed = Number(textValue);
      return Number.isFinite(parsed) ? parsed : null;
    }
    if (options.length > 0) return selectedValues[0] ?? null;
    return textValue.trim() || null;
  };
  const currentResponse = response();
  const responseChanged =
    interaction.status !== "answered" ||
    JSON.stringify(currentResponse) !== savedResponseJson;

  return (
    <section className="rounded-card border border-warn/30 bg-warn/5 p-4 shadow-card">
      <div className="flex items-start gap-3">
        <HelpCircle size={17} className="mt-0.5 shrink-0 text-warn" />
        <div className="min-w-0 flex-1">
          <p className="text-caption font-medium text-warn">
            {interaction.status === "answered"
              ? t("research.workflow.clarification.answerTitle", {
                  defaultValue: "Clarification answer",
                })
              : t("research.workflow.clarification.title", {
                  defaultValue: "Clarification required",
                })}
          </p>
          <h3 className="mt-1 text-sm font-medium leading-relaxed text-text">
            {interaction.question}
          </h3>
          <p className="mt-1 text-caption text-muted">
            {interaction.status === "answered"
              ? t("research.workflow.clarification.revisionHint", {
                  defaultValue:
                    "You can update this answer before plan approval. The current plan will be superseded and regenerated.",
                })
              : t("research.workflow.clarification.durableHint", {
                  defaultValue:
                    "This required request is saved with the workflow and will still be here after restart.",
                })}
          </p>
        </div>
      </div>

      <div className="mt-3">
        {booleanChoice ? (
          <div
            role="group"
            aria-label={interaction.question}
            className="grid grid-cols-2 gap-2"
          >
            {([true, false] as const).map((value) => (
              <button
                key={String(value)}
                type="button"
                aria-pressed={booleanValue === value}
                onClick={() => setBooleanValue(value)}
                className={cn(
                  "rounded-input border px-3 py-2 text-xs",
                  booleanValue === value
                    ? "border-accent/40 bg-accent/5 text-text"
                    : "border-border bg-surface text-muted hover:bg-surface-2",
                )}
              >
                {value
                  ? t("research.workflow.clarification.yes", {
                      defaultValue: "Yes",
                    })
                  : t("research.workflow.clarification.no", {
                      defaultValue: "No",
                    })}
              </button>
            ))}
          </div>
        ) : options.length > 0 ? (
          <div className="grid gap-2 sm:grid-cols-2">
            {options.map((option) => {
              const selected = selectedValues.includes(option.value);
              return (
                <label
                  key={option.value}
                  className={cn(
                    "flex cursor-pointer items-start gap-2 rounded-input border px-3 py-2 text-xs",
                    selected
                      ? "border-accent/40 bg-accent/5"
                      : "border-border bg-surface hover:bg-surface-2",
                  )}
                >
                  <input
                    type={multiValue ? "checkbox" : "radio"}
                    name={`interaction-${interaction.id}`}
                    checked={selected}
                    onChange={() =>
                      setSelectedValues((current) =>
                        multiValue
                          ? current.includes(option.value)
                            ? current.filter((value) => value !== option.value)
                            : [...current, option.value]
                          : [option.value],
                      )
                    }
                    className="mt-0.5 accent-[var(--accent)]"
                  />
                  <span className="min-w-0">
                    <span className="font-medium text-text">{option.label}</span>
                    {option.description && (
                      <span className="mt-0.5 block leading-relaxed text-muted">
                        {option.description}
                      </span>
                    )}
                  </span>
                </label>
              );
            })}
          </div>
        ) : (
          <input
            type={interaction.requestType === "number" ? "number" : "text"}
            value={textValue}
            onChange={(event) => setTextValue(event.target.value)}
            aria-label={interaction.question}
            className="w-full rounded-input border border-border bg-surface px-3 py-2 text-sm text-text outline-none placeholder:text-muted focus:border-accent"
            placeholder={t("research.workflow.clarification.answerPlaceholder", {
              defaultValue: "Enter your answer",
            })}
          />
        )}
      </div>

      <div className="mt-3 flex items-center justify-between gap-3 border-t border-warn/20 pt-3">
        <p className="min-w-0 text-caption text-muted">
          {interaction.stepId
            ? t("research.workflow.clarification.step", {
                defaultValue: "Plan step: {{stepId}}",
                stepId: interaction.stepId,
              })
            : t("research.workflow.clarification.routing", {
                defaultValue: "Needed to finish routing this research goal.",
              })}
        </p>
        <button
          type="button"
          disabled={mutating || currentResponse === null || !responseChanged}
          onClick={() => {
            if (currentResponse !== null) {
              void onRespond(interaction.id, currentResponse);
            }
          }}
          className="flex shrink-0 items-center gap-1.5 rounded-input bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-40"
        >
          {mutating && <Loader2 size={12} className="animate-spin" />}
          {mutating
            ? t("research.workflow.clarification.submitting", {
                defaultValue: "Submitting…",
              })
            : interaction.status === "answered"
              ? t("research.workflow.clarification.update", {
                  defaultValue: "Update answer",
                })
              : t("research.workflow.clarification.submit", {
                  defaultValue: "Submit answer",
                })}
        </button>
      </div>
    </section>
  );
}
