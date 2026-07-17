import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import {
  ResearchSessionControls,
  runtimeModelOptions,
} from "./ResearchSessionControls";

const agents = [
  { name: "research", description: "General research", mode: "primary" },
  { name: "critique", description: "Critical review", mode: "subagent" },
];
const providers = [
  {
    id: "openrouter",
    name: "OpenRouter",
    models: [
      { id: "anthropic/claude-sonnet", name: "Claude Sonnet" },
      { id: "openai/gpt-5", name: "GPT-5" },
    ],
  },
];

describe("ResearchSessionControls", () => {
  it("shows only runtime-reported agents and models and emits real selections", () => {
    const onModeChange = vi.fn();
    const onAgentChange = vi.fn();
    const onModelChange = vi.fn();
    const onOpenSkills = vi.fn();
    render(
      <ResearchSessionControls
        mode="general"
        onModeChange={onModeChange}
        agents={agents}
        selectedAgent="research"
        onAgentChange={onAgentChange}
        providers={providers}
        selectedModel="openrouter/anthropic/claude-sonnet"
        onModelChange={onModelChange}
        skillCount={8}
        onOpenSkills={onOpenSkills}
      />,
    );

    expect(screen.getByRole("group", { name: "Primary agents" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Sub-agents" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "research — General research" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "critique — Critical review" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "biology" })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Claude Sonnet" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "GPT-5" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Research agent"), {
      target: { value: "critique" },
    });
    fireEvent.change(screen.getByLabelText("Model"), {
      target: { value: "openrouter/openai/gpt-5" },
    });
    fireEvent.change(screen.getByLabelText("Execution mode"), {
      target: { value: "verified" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Open research skills" }));

    expect(onAgentChange).toHaveBeenCalledWith("critique");
    expect(onModelChange).toHaveBeenCalledWith("openrouter/openai/gpt-5");
    expect(onModeChange).toHaveBeenCalledWith("verified");
    expect(screen.queryByRole("option", { name: /sandbox/i })).not.toBeInTheDocument();
    expect(onOpenSkills).toHaveBeenCalledOnce();
  });

  it("renders explicit disabled fallbacks when runtime catalogs are empty", () => {
    render(
      <ResearchSessionControls
        mode="general"
        onModeChange={vi.fn()}
        agents={[]}
        selectedAgent={null}
        onAgentChange={vi.fn()}
        providers={[]}
        selectedModel={null}
        onModelChange={vi.fn()}
        skillCount={0}
        onOpenSkills={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("Research agent")).toBeDisabled();
    expect(screen.getByRole("option", { name: "No agents available" })).toBeInTheDocument();
    expect(screen.getByLabelText("Model")).toBeDisabled();
    expect(screen.getByRole("option", { name: "No models available" })).toBeInTheDocument();
  });

  it("offers task-aware routing and reports the latest decision", () => {
    const onRoutingModeChange = vi.fn();
    render(
      <ResearchSessionControls
        mode="general"
        onModeChange={vi.fn()}
        agents={agents}
        selectedAgent="research"
        onAgentChange={vi.fn()}
        providers={providers}
        selectedModel="openrouter/openai/gpt-5"
        onModelChange={vi.fn()}
        routingMode="auto"
        onRoutingModeChange={onRoutingModeChange}
        lastModelRoute={{ tier: "deep", model: "openai/gpt-5.6-sol", matchedPreference: "sol" }}
        skillCount={0}
        onOpenSkills={vi.fn()}
      />,
    );

    expect(screen.getByRole("option", { name: "Auto · deep → openai/gpt-5.6-sol" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "__auto__" } });
    expect(onRoutingModeChange).toHaveBeenCalledWith("auto");
  });
});

describe("runtimeModelOptions", () => {
  it("keeps a configured runtime model visible while provider discovery catches up", () => {
    expect(runtimeModelOptions([], "anthropic/claude-sonnet")).toEqual([
      { value: "anthropic/claude-sonnet", label: "anthropic/claude-sonnet" },
    ]);
  });
});
