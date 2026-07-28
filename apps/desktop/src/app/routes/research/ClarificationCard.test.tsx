import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { InteractionRequest } from "@spark/research-domain";
import { ClarificationCard } from "./ClarificationCard";

function interaction(
  overrides: Partial<InteractionRequest> = {},
): InteractionRequest {
  return {
    id: "interaction-1",
    workflowId: "workflow-1",
    stepId: null,
    requestType: "text",
    question: "What is the primary outcome?",
    options: [],
    required: true,
    status: "pending",
    responseSchema: { type: "string" },
    workflowRevision: 4,
    latestResponse: null,
    createdAt: "2026-07-16T08:00:00Z",
    answeredAt: null,
    ...overrides,
  };
}

describe("ClarificationCard", () => {
  it("requires a durable text answer and offers no skip action", () => {
    const onRespond = vi.fn(async () => {});
    render(
      <ClarificationCard
        interaction={interaction()}
        mutating={false}
        onRespond={onRespond}
      />,
    );

    expect(screen.queryByRole("button", { name: /skip/i })).not.toBeInTheDocument();
    const submit = screen.getByRole("button", { name: "Submit answer" });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("What is the primary outcome?"), {
      target: { value: "Accuracy at 30 days" },
    });
    fireEvent.click(submit);

    expect(onRespond).toHaveBeenCalledWith(
      "interaction-1",
      "Accuracy at 30 days",
    );
  });

  it("submits multi-choice and column selections as an array", () => {
    const onRespond = vi.fn(async () => {});
    render(
      <ClarificationCard
        interaction={interaction({
          requestType: "column-selection",
          question: "Which columns are outcomes?",
          options: [
            { value: "accuracy", label: "accuracy" },
            { value: "latency", label: "latency" },
          ],
          responseSchema: { type: "array" },
        })}
        mutating={false}
        onRespond={onRespond}
      />,
    );

    fireEvent.click(screen.getByRole("checkbox", { name: "accuracy" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "latency" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit answer" }));

    expect(onRespond).toHaveBeenCalledWith("interaction-1", [
      "accuracy",
      "latency",
    ]);
  });

  it("submits a multi-choice request as an array", () => {
    const onRespond = vi.fn(async () => {});
    render(
      <ClarificationCard
        interaction={interaction({
          requestType: "multi-choice",
          question: "Which outcomes should be included?",
          options: [
            { value: "accuracy", label: "accuracy" },
            { value: "latency", label: "latency" },
          ],
          responseSchema: { type: "array" },
        })}
        mutating={false}
        onRespond={onRespond}
      />,
    );

    fireEvent.click(screen.getByRole("checkbox", { name: "accuracy" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit answer" }));

    expect(onRespond).toHaveBeenCalledWith("interaction-1", ["accuracy"]);
  });

  it("submits a finite number", () => {
    const onRespond = vi.fn(async () => {});
    render(
      <ClarificationCard
        interaction={interaction({
          requestType: "number",
          question: "What confidence level should be used?",
          responseSchema: { type: "number" },
        })}
        mutating={false}
        onRespond={onRespond}
      />,
    );

    fireEvent.change(
      screen.getByRole("spinbutton", {
        name: "What confidence level should be used?",
      }),
      { target: { value: "0.95" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Submit answer" }));

    expect(onRespond).toHaveBeenCalledWith("interaction-1", 0.95);
  });

  it("submits an explicit boolean response", () => {
    const onRespond = vi.fn(async () => {});
    render(
      <ClarificationCard
        interaction={interaction({
          requestType: "boolean",
          question: "Is the dataset paired?",
          responseSchema: { type: "boolean" },
        })}
        mutating={false}
        onRespond={onRespond}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit answer" }));

    expect(onRespond).toHaveBeenCalledWith("interaction-1", true);
  });

  it("submits a method confirmation from registered options", () => {
    const onRespond = vi.fn(async () => {});
    render(
      <ClarificationCard
        interaction={interaction({
          requestType: "method-confirmation",
          question: "Which comparison method should be used?",
          options: [
            { value: "welch-t-test", label: "Welch t-test" },
            { value: "mann-whitney", label: "Mann–Whitney" },
          ],
          responseSchema: { type: "string" },
        })}
        mutating={false}
        onRespond={onRespond}
      />,
    );

    fireEvent.click(screen.getByRole("radio", { name: "Welch t-test" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit answer" }));

    expect(onRespond).toHaveBeenCalledWith(
      "interaction-1",
      "welch-t-test",
    );
  });

  it("requires an explicit boolean confirmation", () => {
    const onRespond = vi.fn(async () => {});
    render(
      <ClarificationCard
        interaction={interaction({
          requestType: "assumption-confirmation",
          question: "Treat missing rows as excluded?",
        })}
        mutating={false}
        onRespond={onRespond}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "No" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit answer" }));

    expect(onRespond).toHaveBeenCalledWith("interaction-1", false);
  });

  it("restores and updates an answered choice before plan approval", () => {
    const onRespond = vi.fn(async () => {});
    render(
      <ClarificationCard
        interaction={interaction({
          requestType: "single-choice",
          status: "answered",
          options: [
            { value: "accuracy", label: "accuracy" },
            { value: "latency", label: "latency" },
          ],
          latestResponse: {
            id: "response-1",
            interactionId: "interaction-1",
            revision: 1,
            response: "latency",
            responseSha256: "a".repeat(64),
            createdAt: "2026-07-16T08:01:00Z",
          },
          answeredAt: "2026-07-16T08:01:00Z",
        })}
        mutating={false}
        onRespond={onRespond}
      />,
    );

    const update = screen.getByRole("button", { name: "Update answer" });
    expect(screen.getByRole("radio", { name: "latency" })).toBeChecked();
    expect(update).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: "accuracy" }));
    expect(update).toBeEnabled();
    fireEvent.click(update);

    expect(onRespond).toHaveBeenCalledWith("interaction-1", "accuracy");
  });
});
