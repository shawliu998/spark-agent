import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderAt } from "@/test/render";
import { useRuntimeStore } from "@/lib/runtime";
import { useUiStore } from "@/lib/store";
import { Composer } from "./Composer";
import { WorkflowStarters } from "./WorkflowStarters";

// COPYCAT RULE: useUiStore is module-global; reset the locale after each test
// so this suite never bleeds a non-English locale into other test files.
afterEach(() => useUiStore.getState().setLocale("en"));

const RUNTIME_DEFAULTS = {
  status: useRuntimeStore.getState().status,
  currentId: useRuntimeStore.getState().currentId,
  threads: useRuntimeStore.getState().threads,
  sendPrompt: useRuntimeStore.getState().sendPrompt,
};
afterEach(() => useRuntimeStore.setState(RUNTIME_DEFAULTS));

describe("Composer strings (i18n)", () => {
  it("renders the default placeholder without the unsafe approval-mode switch", () => {
    render(<Composer onSend={() => {}} approvalMode="approve" onApprovalModeChange={() => {}} />);
    expect(screen.getByPlaceholderText("Ask anything")).toBeInTheDocument();
    expect(screen.queryByLabelText("Approval mode")).toBeNull();
  });
});

describe("WorkflowStarters strings (i18n)", () => {
  it("renders the welcome copy and a starter card's title/description in English", () => {
    render(<WorkflowStarters onPick={() => {}} />);
    expect(screen.getByText("What should we look into?")).toBeInTheDocument();
    expect(screen.getByText("Run a demo analysis, end to end")).toBeInTheDocument();
    expect(
      screen.getByText("Simulate a dataset, fit a model, and produce a figure and a traceable report."),
    ).toBeInTheDocument();
  });
});

describe("LiveSessionPage strings (i18n)", () => {
  it("renders the disconnected-runtime card in English (no Tauri sidecar in tests)", async () => {
    renderAt("/live");
    expect(
      await screen.findByText("OpenCode runtime", undefined, { timeout: 5_000 }),
    ).toBeInTheDocument();
    expect(
      screen.getByText((_, node) =>
        node?.textContent === "The desktop app runs a bundled OpenCode automatically. In the browser, start one with opencode serve and connect.",
      ),
    ).toBeInTheDocument();
  });

  it("does not navigate back to a late draft turn after the Live page unmounts", async () => {
    let resolveSend!: (id: string | null) => void;
    const pending = new Promise<string | null>((resolve) => {
      resolveSend = resolve;
    });
    const sendPrompt = vi.fn(() => pending);
    useRuntimeStore.setState({ status: "ready", currentId: null, threads: {}, sendPrompt });
    const { router } = renderAt("/live");

    const composer = await screen.findByLabelText("Ask anything");
    fireEvent.change(composer, { target: { value: "background draft" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    expect(sendPrompt).toHaveBeenCalledWith("background draft");
    await act(async () => {
      await router.navigate("/settings");
    });
    await act(async () => {
      resolveSend("ses_late_draft");
      await pending;
    });

    expect(router.state.location.pathname).toBe("/settings");
  });
});
