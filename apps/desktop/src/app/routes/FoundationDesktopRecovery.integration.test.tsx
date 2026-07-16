import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const runtime = vi.hoisted(() => ({
  clientDirectories: [] as Array<string | undefined>,
  connectCount: 0,
  getMessagesCount: 0,
  listDir: vi.fn(async (dir: string, root?: string) => {
    expect(root).toBe("workspace");
    if (dir === "") {
      return [
        { path: "outputs", name: "outputs", isDir: true, size: 0, modified: 10 },
      ];
    }
    if (dir === "outputs") {
      return [
        {
          path: "outputs/summary.csv",
          name: "summary.csv",
          isDir: false,
          size: 128,
          modified: 20,
        },
        {
          path: "outputs/figure.png",
          name: "figure.png",
          isDir: false,
          size: 2048,
          modified: 30,
        },
      ];
    }
    return [];
  }),
}));

vi.mock("@/lib/tauri", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/tauri")>()),
  isTauri: true,
  workspacePath: vi.fn(async () => "/workspace/general-research"),
  workspaceBase: vi.fn(async () => "/workspace"),
  setWorkspace: vi.fn(async (path: string) => path),
  markSession: vi.fn(async () => {}),
  runtimePassword: vi.fn(async () => "foundation-test-password"),
  getApprovalMode: vi.fn(async () => "approve"),
  logDebug: vi.fn(async () => {}),
  commitWorkspaceSnapshot: vi.fn(async () => false),
  detectTools: vi.fn(async () => []),
}));

vi.mock("@/lib/artifactFile", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/artifactFile")>()),
  listDir: runtime.listDir,
  resolveArtifactPath: vi.fn(async () => null),
}));

vi.mock("@/lib/runs", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/runs")>()),
  queryRuns: vi.fn(async () => ({
    rows: [],
    total: 0,
    facets: { status: [], surface: [] },
  })),
}));

vi.mock("@ai4s/sdk", () => {
  class OpenCodeClient {
    private status: (status: string) => void = () => {};

    constructor(options: { directory?: string }) {
      runtime.clientDirectories.push(options.directory);
    }

    onStatus(callback: (status: string) => void) {
      this.status = callback;
      return () => {
        this.status = () => {};
      };
    }

    onEvent() {}

    async connect() {
      runtime.connectCount += 1;
      this.status("connecting");
      this.status("ready");
    }

    close() {
      this.status("offline");
    }

    async listSessions() {
      return [
        {
          id: "ses_foundation",
          title: "General Research recovery",
          directory: "/workspace/general-research",
        },
      ];
    }

    async listSkills() {
      return Array.from({ length: 8 }, (_, index) => ({
        name: `research-skill-${index + 1}`,
        description: "Foundation research skill",
      }));
    }

    async listAgents() {
      return [
        {
          name: "research",
          description: "Evidence-first general research",
          mode: "primary",
        },
      ];
    }

    async listProviders() {
      return [
        {
          id: "foundation",
          name: "Foundation",
          models: [{ id: "research-model", name: "Research Model" }],
        },
      ];
    }

    async getDefaultModel() {
      return "foundation/research-model";
    }

    async listCommands() {
      return [];
    }

    async listQuestions() {
      return [];
    }

    async listPermissions() {
      return [];
    }

    async getMessages() {
      runtime.getMessagesCount += 1;
      return [
        {
          role: "user",
          parts: [{ type: "text", text: "Analyze the evidence and summarize the result." }],
        },
        {
          role: "assistant",
          completed: 1,
          parts: [
            {
              type: "text",
              text: "Analysis completed with a reproducible evidence trail.",
            },
          ],
        },
      ];
    }
  }

  return {
    OpenCodeClient,
    DEFAULT_OPENCODE_URL: "http://127.0.0.1:4096",
  };
});

import { LiveSessionPage } from "./LiveSessionPage";
import { useRuntimeStore } from "@/lib/runtime";

function resetDesktopStore() {
  useRuntimeStore.getState().disconnect();
  useRuntimeStore.setState({
    status: "offline",
    sessions: [],
    currentId: null,
    threads: {},
    skills: [],
    agents: [],
    providers: [],
    selectedAgent: null,
    commands: [],
    defaultModel: null,
    tools: [],
    error: null,
    questions: [],
    permissions: [],
    sessionParents: {},
    panes: {},
    workspace: null,
    workspacePinned: false,
    draftWorkspaceMaterialized: false,
    switching: false,
    sending: false,
    runningSessions: {},
    shellTurns: {},
  });
}

function renderRecoveredSession() {
  return render(
    <MemoryRouter
      initialEntries={["/live/ses_foundation"]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/live/:sessionId" element={<LiveSessionPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function expectRecoveredDesktopSurface() {
  expect(await screen.findByText("General Research recovery")).toBeInTheDocument();
  expect(screen.getByLabelText("Execution mode")).toHaveValue("general");
  await waitFor(() => expect(screen.getByLabelText("Research agent")).toHaveValue("research"));
  expect(
    screen.getByRole("option", { name: "research — Evidence-first general research" }),
  ).toBeInTheDocument();
  expect(
    await screen.findByText("Analyze the evidence and summarize the result."),
  ).toBeInTheDocument();
  expect(
    screen.getByText("Analysis completed with a reproducible evidence trail."),
  ).toBeInTheDocument();
  expect(await screen.findByText("summary.csv")).toBeInTheDocument();
  expect(screen.getByText("figure.png")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Workspace artifacts" })).toBeInTheDocument();
}

describe("Foundation desktop-store recovery integration", () => {
  beforeEach(() => {
    window.localStorage.clear();
    runtime.clientDirectories.length = 0;
    runtime.connectCount = 0;
    runtime.getMessagesCount = 0;
    runtime.listDir.mockClear();
    resetDesktopStore();
  });

  it("restores a General Research session and its CSV/PNG shelf after store teardown and UI remount", async () => {
    // This portable integration crosses the real Zustand/page/discovery/shelf
    // boundaries. It deliberately does not claim to restart a packaged macOS
    // process; the live sidecar smoke separately covers an actual process restart.
    await act(async () => {
      await useRuntimeStore.getState().connect();
    });
    const firstDesktop = renderRecoveredSession();
    await expectRecoveredDesktopSurface();

    firstDesktop.unmount();
    act(() => resetDesktopStore());

    await act(async () => {
      await useRuntimeStore.getState().connect();
    });
    renderRecoveredSession();
    await expectRecoveredDesktopSurface();

    expect(runtime.connectCount).toBe(2);
    expect(runtime.clientDirectories).toEqual([
      "/workspace/general-research",
      "/workspace/general-research",
    ]);
    expect(runtime.getMessagesCount).toBe(2);
    expect(runtime.listDir).toHaveBeenCalledWith("outputs", "workspace");
  });
});
