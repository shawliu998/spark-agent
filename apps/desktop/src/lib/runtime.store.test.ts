// Workspace-per-session behavior: a fresh draft's first message creates a new
// dated folder by default; an explicit switcher choice pins the destination.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  workspacePathValue: "/ws/base",
  newDatedWorkspace: vi.fn(async (name: string) => {
    mocks.workspacePathValue = `/ws/${name}`;
    return mocks.workspacePathValue;
  }),
  setWorkspace: vi.fn(async (path: string) => {
    mocks.workspacePathValue = path;
    return path;
  }),
  permissionValidationError: null as string | null,
  validateRuntimePermissions: vi.fn(async (_path: string | null) => {
    if (mocks.permissionValidationError) throw new Error(mocks.permissionValidationError);
  }),
  commitWorkspaceSnapshot: vi.fn(async () => false),
  kernelReset: vi.fn(async () => {}),
  /** Number of connect() attempts that fail before one succeeds. */
  failConnects: 0,
  /** Optional gates consumed by successive connect() attempts. */
  connectWaits: [] as Promise<void>[],
  /** Number of createSession() attempts that fail before one succeeds. */
  failCreates: 0,
  createdSessionId: "ses_new",
  createdSessionIds: [] as string[],
  createSession: vi.fn(),
  createSessionDirectories: [] as Array<string | undefined>,
  createWaits: [] as Promise<void>[],
  sendPrompt: vi.fn(),
  sendPromptOptions: vi.fn(),
  sendWaits: [] as Promise<void>[],
  sendPromptEmitsIdle: false,
  failPrompts: 0,
  /** Fire a normalized event into the store, as the SSE stream would. */
  fireEvent: (_e: unknown) => {},
  startRuntime: vi.fn(async () => mocks.startRuntimeUrl),
  runShell: vi.fn(),
  runCommand: vi.fn(),
  commandWaits: [] as Promise<void>[],
  suppressCommandEvents: false,
  replyPermission: vi.fn(),
  abortSession: vi.fn(),
  abortWaits: [] as Promise<void>[],
  /** SSE events the real server streams back DURING an abort POST's await — an
   *  "aborted" error and one or more session.idle events. Empty by default. */
  abortTrailing: [] as unknown[],
  getMessages: vi.fn(),
  getMessagesReturned: vi.fn(),
  messageWaits: [] as Promise<void>[],
  listQuestions: vi.fn(),
  listQuestionsReturned: vi.fn(),
  listPermissions: vi.fn(),
  listPermissionsReturned: vi.fn(),
  questionWaits: [] as Promise<void>[],
  permissionWaits: [] as Promise<void>[],
  questions: [] as unknown[],
  pendingPermissions: [] as unknown[],
  /** Records setDefaultModel calls; `currentModel` is what getDefaultModel returns. */
  setDefaultModelSpy: vi.fn(),
  currentModel: null as string | null,
  /** History the mock server returns for any session. */
  messages: [] as unknown[],
  /** Next getMessages call throws. */
  failMessages: false,
  /** Next runShell call throws (HTTP-level failure). */
  failShell: false,
  /** Next runCommand call throws before any event (HTTP-level failure). */
  failCommand: false,
  /** Next runCommand call streams an event, then throws — the WKWebView
   *  ~60 s fetch kill on a long sync turn ("Load failed"). */
  dropCommandPost: false,
  /** Approval mode the Rust config currently holds. */
  approvalMode: "balanced" as string,
  getApprovalMode: vi.fn(async () => mocks.approvalMode),
  startRuntimeUrl: "http://127.0.0.1:1",
  restartUrl: "http://127.0.0.1:1",
  clientOpen: false,
  mutationSawClosedClient: false,
  setApprovalMode: vi.fn(async (mode: string) => {
    mocks.mutationSawClosedClient = !mocks.clientOpen;
    mocks.approvalMode = mode;
    return { runtimeUrl: mocks.restartUrl };
  }),
  setProxySetting: vi.fn(async () => {
    mocks.mutationSawClosedClient = !mocks.clientOpen;
    return { runtimeUrl: mocks.restartUrl };
  }),
  removeConfigEntry: vi.fn(async () => {
    mocks.mutationSawClosedClient = !mocks.clientOpen;
    return { runtimeUrl: mocks.restartUrl };
  }),
  saveProviderApiKey: vi.fn(async () => {
    mocks.mutationSawClosedClient = !mocks.clientOpen;
    return { runtimeUrl: mocks.restartUrl };
  }),
  removeProviderApiKey: vi.fn(async () => {
    mocks.mutationSawClosedClient = !mocks.clientOpen;
    return { runtimeUrl: mocks.restartUrl };
  }),
  saveScienceConnectorApiKey: vi.fn(async () => {
    mocks.mutationSawClosedClient = !mocks.clientOpen;
    return { runtimeUrl: mocks.restartUrl };
  }),
  removeScienceConnector: vi.fn(async () => {
    mocks.mutationSawClosedClient = !mocks.clientOpen;
    return { runtimeUrl: mocks.restartUrl };
  }),
  finalizeProviderLogin: vi.fn(async () => {
    mocks.mutationSawClosedClient = !mocks.clientOpen;
    return { runtimeUrl: mocks.restartUrl };
  }),
  importOpenCodeLogin: vi.fn(async () => {
    mocks.mutationSawClosedClient = !mocks.clientOpen;
    return { imported: true, runtimeUrl: mocks.restartUrl };
  }),
  /** Constructor options every OpenCodeClient was created with. */
  clientOpts: [] as Record<string, unknown>[],
  listedSessions: [] as unknown[],
  taskPlans: [] as unknown[],
  createTaskPlanRecord: vi.fn(async () => {}),
  recordTaskSession: vi.fn(async () => {}),
  recordTaskSessionStatus: vi.fn(async () => {}),
  recordTaskStartFailure: vi.fn(async () => {}),
  recordTaskSynthesis: vi.fn(async () => {}),
  listTaskPlans: vi.fn(async () => mocks.taskPlans),
}));

vi.mock("./tauri", () => ({
  isTauri: true,
  logDebug: async () => {},
  detectTools: async () => [],
  startRuntime: mocks.startRuntime,
  workspacePath: async () => mocks.workspacePathValue,
  setWorkspace: mocks.setWorkspace,
  validateRuntimePermissions: mocks.validateRuntimePermissions,
  newDatedWorkspace: mocks.newDatedWorkspace,
  markSession: async () => {},
  commitWorkspaceSnapshot: mocks.commitWorkspaceSnapshot,
  getApprovalMode: mocks.getApprovalMode,
  setApprovalMode: mocks.setApprovalMode,
  setProxySetting: mocks.setProxySetting,
  removeConfigEntry: mocks.removeConfigEntry,
  saveProviderApiKey: mocks.saveProviderApiKey,
  removeProviderApiKey: mocks.removeProviderApiKey,
  saveScienceConnectorApiKey: mocks.saveScienceConnectorApiKey,
  removeScienceConnector: mocks.removeScienceConnector,
  finalizeProviderLogin: mocks.finalizeProviderLogin,
  importOpenCodeLogin: mocks.importOpenCodeLogin,
  runtimePassword: async () => "pw-test",
}));
vi.mock("./kernel", () => ({ kernelReset: mocks.kernelReset }));
vi.mock("./taskPlans", () => ({
  createTaskPlanRecord: mocks.createTaskPlanRecord,
  recordTaskSession: mocks.recordTaskSession,
  recordTaskSessionStatus: mocks.recordTaskSessionStatus,
  recordTaskStartFailure: mocks.recordTaskStartFailure,
  recordTaskSynthesis: mocks.recordTaskSynthesis,
  listTaskPlans: mocks.listTaskPlans,
}));
vi.mock("@ai4s/sdk", () => {
  class OpenCodeClient {
    private statusCb: (s: string) => void = () => {};
    private directory: string | undefined;
    constructor(opts: Record<string, unknown>) {
      mocks.clientOpts.push(opts);
      mocks.clientOpen = true;
      this.directory = typeof opts.directory === "string" ? opts.directory : undefined;
    }
    onStatus(cb: (s: string) => void) {
      this.statusCb = cb;
      return () => {
        this.statusCb = () => {};
      };
    }
    onEvent(cb: (e: unknown) => void) {
      mocks.fireEvent = cb;
    }
    async connect() {
      this.statusCb("connecting");
      const wait = mocks.connectWaits.shift();
      if (wait) await wait;
      if (mocks.failConnects > 0) {
        mocks.failConnects--;
        this.statusCb("error");
        throw new Error("Could not open OpenCode event stream");
      }
      this.statusCb("ready");
    }
    async listSessions() {
      return mocks.listedSessions;
    }
    async listSkills() {
      return [{ name: "stub" }];
    }
    async listAgents() {
      return [];
    }
    async listProviders() {
      return [];
    }
    async getDefaultModel() {
      return mocks.currentModel;
    }
    async setDefaultModel(model: string) {
      mocks.setDefaultModelSpy(model);
      mocks.currentModel = model;
    }
    async createSession() {
      mocks.createSession();
      mocks.createSessionDirectories.push(this.directory);
      const wait = mocks.createWaits.shift();
      if (wait) await wait;
      if (mocks.failCreates > 0) {
        mocks.failCreates--;
        throw new Error("Load failed");
      }
      return mocks.createdSessionIds.shift() ?? mocks.createdSessionId;
    }
    async sendPrompt(sid: string, _text: string, options?: Record<string, unknown>) {
      mocks.sendPrompt(sid);
      mocks.sendPromptOptions(options);
      const wait = mocks.sendWaits.shift();
      if (wait) await wait;
      if (mocks.failPrompts > 0) {
        mocks.failPrompts--;
        throw new Error("prompt rejected");
      }
      if (mocks.sendPromptEmitsIdle) {
        mocks.fireEvent({ type: "session.idle", sessionId: sid });
      }
    }
    async listCommands() {
      return [{ name: "init", description: "guided AGENTS.md setup", source: "command" }];
    }
    // Like the real endpoints, shell/command resolve only when the turn is
    // over — and session.idle fires BEFORE the POST resolves.
    async runShell(sid: string, command: string, agent: string) {
      mocks.runShell(sid, command, agent);
      if (mocks.failShell) throw new Error("shell exploded");
      mocks.fireEvent({
        type: "tool.updated",
        sessionId: sid,
        callId: "csh",
        tool: "bash",
        status: "success",
        title: "",
        input: { command },
        output: "/ws/mock\n",
      });
      mocks.fireEvent({ type: "session.idle", sessionId: sid });
    }
    async runCommand(sid: string, name: string, args?: string, options?: unknown) {
      mocks.runCommand(sid, name, args, options);
      const wait = mocks.commandWaits.shift();
      if (wait) await wait;
      if (mocks.failCommand) throw new Error("command exploded");
      if (mocks.dropCommandPost) {
        mocks.fireEvent({ type: "text.updated", sessionId: sid, partId: "t1", text: "working…" });
        throw new Error("Load failed");
      }
      if (mocks.suppressCommandEvents) return;
      mocks.fireEvent({ type: "session.idle", sessionId: sid });
    }
    async replyPermission(requestId: string, reply: string) {
      mocks.replyPermission(requestId, reply);
    }
    async abortSession(sid: string) {
      mocks.abortSession(sid);
      // The real server answers an abort with its own SSE burst that streams
      // back while this POST is still being awaited — reproduce that timing so
      // the guard must already be set before the await, not after it.
      for (const e of mocks.abortTrailing) mocks.fireEvent(e);
      const wait = mocks.abortWaits.shift();
      if (wait) await wait;
    }
    async getMessages(sid: string) {
      mocks.getMessages(sid);
      const messages = mocks.messages;
      const wait = mocks.messageWaits.shift();
      if (wait) await wait;
      if (mocks.failMessages) throw new Error("history hung");
      mocks.getMessagesReturned();
      return messages;
    }
    async listQuestions() {
      mocks.listQuestions();
      const questions = mocks.questions;
      const wait = mocks.questionWaits.shift();
      if (wait) await wait;
      mocks.listQuestionsReturned();
      return questions;
    }
    async listPermissions() {
      mocks.listPermissions();
      const permissions = mocks.pendingPermissions;
      const wait = mocks.permissionWaits.shift();
      if (wait) await wait;
      mocks.listPermissionsReturned();
      return permissions;
    }
    // The real client emits "offline" on teardown — the store must keep that
    // away from the UI while reconnecting (first-boot flicker regression).
    close() {
      mocks.clientOpen = false;
      this.statusCb("offline");
    }
  }
  return { OpenCodeClient, DEFAULT_OPENCODE_URL: "http://127.0.0.1:4096" };
});

import type { ArtifactBlock } from "@ai4s/shared";
import { DRAFT_KEY, resolveResearchAgent, rootSessionOf, useRuntimeStore } from "./runtime";

let resetEndpointPort = 49_000;

afterEach(() => {
  // The runtime deliberately retains trailing-terminal fences across a
  // same-endpoint reconnect. Tests reuse session ids, so switch trust
  // namespaces between cases to clear module-level ownership state exactly as
  // a real endpoint change does.
  useRuntimeStore.getState().setServerUrl(`http://127.0.0.1:${resetEndpointPort++}`);
});

function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(async () => {
  vi.clearAllMocks();
  mocks.failConnects = 0;
  mocks.connectWaits = [];
  mocks.messageWaits = [];
  mocks.questionWaits = [];
  mocks.permissionWaits = [];
  mocks.questions = [];
  mocks.pendingPermissions = [];
  mocks.failCreates = 0;
  mocks.createdSessionId = "ses_new";
  mocks.createdSessionIds = [];
  mocks.createSessionDirectories = [];
  mocks.createWaits = [];
  mocks.sendWaits = [];
  mocks.sendPromptEmitsIdle = false;
  mocks.failPrompts = 0;
  mocks.failShell = false;
  mocks.failCommand = false;
  mocks.commandWaits = [];
  mocks.suppressCommandEvents = false;
  mocks.dropCommandPost = false;
  mocks.abortWaits = [];
  mocks.abortTrailing = [];
  mocks.messages = [];
  mocks.failMessages = false;
  mocks.approvalMode = "balanced";
  mocks.startRuntimeUrl = "http://127.0.0.1:1";
  mocks.restartUrl = "http://127.0.0.1:1";
  mocks.workspacePathValue = "/ws/base";
  mocks.permissionValidationError = null;
  mocks.clientOpen = false;
  mocks.mutationSawClosedClient = false;
  mocks.currentModel = null;
  mocks.taskPlans = [];
  mocks.listedSessions = [];
  useRuntimeStore.setState({
    status: "offline",
    serverUrl: "http://127.0.0.1:1",
    currentId: null,
    workspacePinned: false,
    draftWorkspaceMaterialized: false,
    threads: {},
    error: null,
    switching: false,
    sending: false,
    taskBatchLaunching: false,
    runningSessions: {},
    permissions: [],
    sessionParents: {},
    panes: {},
    providers: [],
    modelRoutingMode: "manual",
    lastModelRoute: null,
    sessionExecutions: {},
    taskPlans: [],
  });
  await useRuntimeStore.getState().connect();
  expect(useRuntimeStore.getState().status).toBe("ready");
});

describe("runtime authentication", () => {
  it("connect() passes the per-run runtime password to the SDK client", async () => {
    // The sidecar requires Basic auth (OPENCODE_SERVER_PASSWORD); an
    // unauthenticated client would 401 on every call.
    mocks.clientOpts.length = 0;
    await useRuntimeStore.getState().connect();
    expect(mocks.clientOpts[mocks.clientOpts.length - 1]).toMatchObject({
      password: "pw-test",
    });
  });
});

describe("General Research runtime selection", () => {
  it("prefers only real runtime agents and never invents a research option", () => {
    expect(
      resolveResearchAgent(
        [
          { name: "explore", description: "Explore", mode: "primary" },
          { name: "critique", description: "Critique", mode: "subagent" },
        ],
        "missing-agent",
      ),
    ).toBe("explore");
    expect(resolveResearchAgent([], "research")).toBeNull();
  });

  it("sends the selected runtime agent and configured model on a general prompt", async () => {
    useRuntimeStore.setState({
      currentId: "ses_general",
      agents: [{ name: "research", description: "Research", mode: "primary" }],
      selectedAgent: "research",
      defaultModel: "openrouter/anthropic/claude-sonnet",
      threads: {},
    });

    await useRuntimeStore.getState().sendPrompt("compare the paper and dataset");

    expect(mocks.sendPromptOptions).toHaveBeenCalledWith({
      agent: "research",
      model: "openrouter/anthropic/claude-sonnet",
    });
    expect(mocks.validateRuntimePermissions).toHaveBeenCalledWith("/ws/base");
  });

  it("auto-routes planning and acceptance work to a reported Sol model", async () => {
    useRuntimeStore.setState({
      currentId: "ses_auto",
      agents: [{ name: "research", description: "Research", mode: "primary" }],
      selectedAgent: "research",
      providers: [
        {
          id: "moonshot",
          name: "Moonshot",
          models: [{ id: "kimi-k3", name: "Kimi K3" }],
        },
        {
          id: "openai",
          name: "OpenAI",
          models: [{ id: "gpt-5.6-sol", name: "Codex Sol" }],
        },
      ],
      defaultModel: "moonshot/kimi-k3",
      modelRoutingMode: "auto",
      threads: {},
    });

    await useRuntimeStore.getState().sendPrompt("规划整体架构并完成验收");

    expect(mocks.sendPromptOptions).toHaveBeenCalledWith({
      agent: "research",
      model: "openai/gpt-5.6-sol",
    });
    expect(useRuntimeStore.getState().lastModelRoute).toMatchObject({
      tier: "deep",
      model: "openai/gpt-5.6-sol",
    });
    expect(useRuntimeStore.getState().sessionExecutions.ses_auto).toMatchObject({
      agent: "research",
      model: "openai/gpt-5.6-sol",
      route: { tier: "deep", model: "openai/gpt-5.6-sol" },
    });
  });

  it("launches a same-workspace task plan into independently routed sessions", async () => {
    mocks.createdSessionIds = ["ses_evidence", "ses_review"];
    useRuntimeStore.setState({
      workspacePinned: true,
      workspace: "/ws/base",
      agents: [{ name: "research", description: "Research", mode: "primary" }],
      selectedAgent: "research",
      providers: [
        {
          id: "openai",
          name: "OpenAI",
          models: [
            { id: "gpt-5.6-luna", name: "Codex Luna" },
            { id: "gpt-5.6-sol", name: "Codex Sol" },
          ],
        },
      ],
      modelRoutingMode: "auto",
    });

    const ids = await useRuntimeStore.getState().launchTaskBatch("Assess the result", [
      { id: "evidence", title: "Summarize sources", prompt: "Gather the relevant evidence." },
      { id: "review", title: "Review architecture", prompt: "Review risks and acceptance criteria." },
    ]);

    expect(ids).toEqual(["ses_evidence", "ses_review"]);
    expect(mocks.sendPromptOptions).toHaveBeenNthCalledWith(1, {
      agent: "research",
      model: "openai/gpt-5.6-luna",
    });
    expect(mocks.sendPromptOptions).toHaveBeenNthCalledWith(2, {
      agent: "research",
      model: "openai/gpt-5.6-sol",
    });
    expect(useRuntimeStore.getState().runningSessions).toMatchObject({
      ses_evidence: true,
      ses_review: true,
    });
    expect(useRuntimeStore.getState().sessionExecutions.ses_review.model).toBe(
      "openai/gpt-5.6-sol",
    );
    expect(useRuntimeStore.getState().taskBatchLaunching).toBe(false);
    expect(mocks.createTaskPlanRecord).toHaveBeenCalledOnce();
    expect(mocks.recordTaskSession).toHaveBeenCalledTimes(2);
  });

  it("does not relock a batch task when idle arrives before prompt POST resolves", async () => {
    mocks.createdSessionIds = ["ses_fast", "ses_slow"];
    mocks.sendPromptEmitsIdle = true;
    useRuntimeStore.setState({ workspacePinned: true, workspace: "/ws/base" });

    await useRuntimeStore.getState().launchTaskBatch("Compare evidence", [
      { id: "fast", title: "Fast", prompt: "Summarize one source." },
      { id: "slow", title: "Slow", prompt: "Summarize another source." },
    ]);

    expect(useRuntimeStore.getState().runningSessions).toEqual({});
  });

  it("blocks endpoint replacement while a task batch is launching", async () => {
    const gate = deferred();
    mocks.createdSessionIds = ["ses_old", "ses_second"];
    mocks.sendWaits = [gate.promise];
    useRuntimeStore.setState({ workspacePinned: true, workspace: "/ws/base" });

    const launch = useRuntimeStore.getState().launchTaskBatch("Compare evidence", [
      { id: "a", title: "A", prompt: "Summarize source A." },
      { id: "b", title: "B", prompt: "Summarize source B." },
    ]);
    await vi.waitFor(() => expect(mocks.sendPrompt).toHaveBeenCalledWith("ses_old"));
    const originalUrl = useRuntimeStore.getState().serverUrl;
    useRuntimeStore.getState().setServerUrl("http://127.0.0.1:29999");
    expect(useRuntimeStore.getState().serverUrl).toBe(originalUrl);
    expect(useRuntimeStore.getState().error).toMatch(/active task/);
    gate.resolve();

    await expect(launch).resolves.toEqual(["ses_old", "ses_second"]);
    expect(useRuntimeStore.getState().taskBatchLaunching).toBe(false);
  });

  it("recovers only journaled plan sessions and their terminal state", async () => {
    mocks.listedSessions = [
      { id: "ses_task", title: "Evidence", directory: "/ws/base" },
      { id: "ses_chat", title: "Ordinary chat", directory: "/ws/base" },
    ];
    mocks.messages = [
      { role: "assistant", completed: 10, parts: [{ type: "text", text: "Done" }] },
    ];
    mocks.taskPlans = [
      {
        schemaVersion: 1,
        planId: "plan_1",
        objective: "Assess evidence",
        createdAt: 1,
        tasks: [
          {
            id: "evidence",
            title: "Evidence",
            prompt: "Gather evidence",
            sessions: [
              {
                sessionId: "ses_task",
                agent: "research",
                requestedModel: "openai/gpt-5.6-terra",
                routeTier: "standard",
                matchedPreference: "terra",
                status: "completed",
                error: null,
                recordedAt: 2,
              },
            ],
            startFailures: [],
          },
        ],
        syntheses: [],
      },
    ];

    await useRuntimeStore.getState().refreshSessions();
    await useRuntimeStore.getState().refreshTaskPlans();

    expect(useRuntimeStore.getState().sessionExecutions.ses_task).toMatchObject({
      planId: "plan_1",
      kind: "task",
      model: "openai/gpt-5.6-terra",
    });
    expect(useRuntimeStore.getState().sessionExecutions.ses_chat).toBeUndefined();
    expect(useRuntimeStore.getState().runningSessions.ses_task).toBeUndefined();
  });

  it("fails closed when a running plan session cannot be reconciled", async () => {
    mocks.listedSessions = [{ id: "ses_unknown", title: "Evidence", directory: "/ws/base" }];
    mocks.failMessages = true;
    mocks.taskPlans = [
      {
        schemaVersion: 1,
        planId: "plan_unknown",
        objective: "Assess evidence",
        createdAt: 1,
        tasks: [
          {
            id: "evidence",
            title: "Evidence",
            prompt: "Gather evidence",
            sessions: [
              {
                sessionId: "ses_unknown",
                agent: "research",
                requestedModel: null,
                routeTier: null,
                matchedPreference: null,
                status: "running",
                error: null,
                recordedAt: 2,
              },
            ],
            startFailures: [],
          },
        ],
        syntheses: [],
      },
    ];

    await useRuntimeStore.getState().refreshSessions();
    await useRuntimeStore.getState().refreshTaskPlans();

    expect(useRuntimeStore.getState().runningSessions.ses_unknown).toBe(true);
    expect(useRuntimeStore.getState().sessionExecutions.ses_unknown.recoveryUnknown).toBe(true);
    await expect(useRuntimeStore.getState().setProxySetting("none", "")).rejects.toThrow(
      /active task/,
    );
  });

  it("blocks runtime-restarting settings while a journaled plan task is active", async () => {
    useRuntimeStore.setState({
      runningSessions: { ses_task: true },
      sessionExecutions: {
        ses_task: {
          agent: "research",
          model: null,
          route: null,
          startedAt: 1,
          planId: "plan_1",
          kind: "task",
        },
      },
    });

    await expect(useRuntimeStore.getState().setProxySetting("none", "")).rejects.toThrow(
      /active task/,
    );
    expect(mocks.setProxySetting).not.toHaveBeenCalled();
  });

  it("keeps a task failed when an idle event follows its provider error", async () => {
    useRuntimeStore.setState({
      sessions: [{ id: "ses_failed", title: "Failed task", directory: "/ws/base" }],
      threads: { ses_failed: { blocks: [], index: {}, loaded: true } },
      runningSessions: { ses_failed: true },
      sessionExecutions: {
        ses_failed: {
          agent: "research",
          model: null,
          route: null,
          startedAt: 1,
          planId: "plan_failed",
          objective: "Assess evidence",
          kind: "task",
        },
      },
    });

    mocks.fireEvent({ type: "error", sessionId: "ses_failed", message: "provider failed" });
    mocks.fireEvent({ type: "session.idle", sessionId: "ses_failed" });

    expect(useRuntimeStore.getState().sessionExecutions.ses_failed).toMatchObject({
      startError: "provider failed",
      terminalStatus: "failed",
    });
    expect(useRuntimeStore.getState().runningSessions.ses_failed).toBeUndefined();
    expect(mocks.recordTaskSessionStatus).toHaveBeenCalledWith(
      expect.objectContaining({
        sessionId: "ses_failed",
        status: "failed",
        error: "provider failed",
      }),
    );
    expect(mocks.recordTaskSessionStatus).not.toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: "ses_failed", status: "completed" }),
    );
  });

  it("synthesizes completed task handoffs in a new deep-model session", async () => {
    mocks.createdSessionIds = ["ses_synthesis"];
    mocks.messages = [
      {
        role: "assistant",
        completed: 10,
        parts: [{ type: "text", text: "Evidence handoff with limitations." }],
      },
    ];
    useRuntimeStore.setState({
      workspace: "/ws/base",
      workspacePinned: true,
      sessions: [
        { id: "ses_a", title: "Evidence", directory: "/ws/base" },
        { id: "ses_b", title: "Review", directory: "/ws/base" },
      ],
      providers: [
        {
          id: "openai",
          name: "OpenAI",
          models: [{ id: "gpt-5.6-sol", name: "Codex Sol" }],
        },
      ],
      selectedAgent: "research",
      modelRoutingMode: "auto",
      sessionExecutions: {
        ses_a: {
          agent: "research",
          model: "openai/gpt-5.6-luna",
          route: null,
          startedAt: 1,
          planId: "plan_1",
          objective: "Assess the result",
          taskTitle: "Evidence",
          kind: "task",
        },
        ses_b: {
          agent: "research",
          model: "openai/gpt-5.6-sol",
          route: null,
          startedAt: 2,
          planId: "plan_1",
          objective: "Assess the result",
          taskTitle: "Review",
          kind: "task",
        },
      },
      runningSessions: {},
    });

    const id = await useRuntimeStore.getState().synthesizeTaskPlan("plan_1");

    expect(id).toBe("ses_synthesis");
    expect(mocks.getMessages).toHaveBeenCalledTimes(2);
    expect(mocks.sendPromptOptions).toHaveBeenCalledWith({
      agent: "research",
      model: "openai/gpt-5.6-sol",
    });
    expect(useRuntimeStore.getState().sessionExecutions.ses_synthesis).toMatchObject({
      planId: "plan_1",
      kind: "synthesis",
      model: "openai/gpt-5.6-sol",
    });
    expect(useRuntimeStore.getState().threads.ses_synthesis.blocks[0]).toMatchObject({
      kind: "user",
      text: expect.stringContaining("Evidence handoff with limitations."),
    });
    expect(mocks.recordTaskSynthesis).toHaveBeenCalledWith(
      expect.objectContaining({ planId: "plan_1", sessionId: "ses_synthesis" }),
    );
  });

  it("fails closed before posting when resolved agent permissions are unsafe", async () => {
    useRuntimeStore.setState({
      currentId: "ses_guarded",
      workspace: "/ws/base",
      threads: {},
    });
    mocks.permissionValidationError =
      "OpenCode permission floor rejected the workspace: custom agent allows bash";

    await expect(useRuntimeStore.getState().sendPrompt("do not run")).resolves.toBe("ses_guarded");

    expect(mocks.validateRuntimePermissions).toHaveBeenCalledWith("/ws/base");
    expect(mocks.sendPrompt).not.toHaveBeenCalled();
    expect(useRuntimeStore.getState().error).toContain("permission floor rejected");
  });

  it("keeps the submitted selections when draft materialization refreshes the catalog", async () => {
    useRuntimeStore.setState({
      currentId: null,
      workspacePinned: false,
      draftWorkspaceMaterialized: false,
      agents: [{ name: "research", description: "Research", mode: "primary" }],
      selectedAgent: "research",
      defaultModel: "foundation/submitted-model",
      threads: {},
    });

    await useRuntimeStore.getState().sendPrompt("materialize then research");

    expect(mocks.newDatedWorkspace).toHaveBeenCalledTimes(1);
    expect(mocks.sendPromptOptions).toHaveBeenCalledWith({
      agent: "research",
      model: "foundation/submitted-model",
    });
  });
});

describe("per-session workspace folders", () => {
  it("creates a fresh dated folder before the first message of an unpinned draft", async () => {
    const id = await useRuntimeStore.getState().sendPrompt("hello");
    expect(id).toBe("ses_new");
    expect(mocks.newDatedWorkspace).toHaveBeenCalledTimes(1);
    expect(mocks.newDatedWorkspace.mock.calls[0][0]).toMatch(/^\d{4}-\d{2}-\d{2}-\d{4}$/);
    // The kernel is reset so it respawns inside the new folder.
    expect(mocks.kernelReset).toHaveBeenCalled();
  });

  it("reuses the workspace materialized for a draft attachment on the first session", async () => {
    const prepared = await useRuntimeStore.getState().prepareDraftWorkspace();
    expect(prepared).toMatch(/^\/ws\/\d{4}-\d{2}-\d{2}-\d{4}$/);
    expect(useRuntimeStore.getState()).toMatchObject({
      workspace: prepared,
      workspacePinned: false,
      draftWorkspaceMaterialized: true,
    });
    expect(mocks.newDatedWorkspace).toHaveBeenCalledTimes(1);

    const id = await useRuntimeStore.getState().sendPrompt(
      "Files added to the workspace: observations.csv",
    );

    expect(id).toBe("ses_new");
    expect(mocks.newDatedWorkspace).toHaveBeenCalledTimes(1);
    expect(mocks.createSessionDirectories).toEqual([prepared]);
  });

  it("single-flights concurrent draft attachment and paste preparation", async () => {
    const [attachmentWorkspace, pasteWorkspace] = await Promise.all([
      useRuntimeStore.getState().prepareDraftWorkspace(),
      useRuntimeStore.getState().prepareDraftWorkspace(),
    ]);

    expect(attachmentWorkspace).toBe(pasteWorkspace);
    expect(mocks.newDatedWorkspace).toHaveBeenCalledTimes(1);
  });

  it("keeps a pinned folder: no dated folder is created", async () => {
    useRuntimeStore.setState({ workspacePinned: true });
    const id = await useRuntimeStore.getState().sendPrompt("hello");
    expect(id).toBe("ses_new");
    expect(mocks.newDatedWorkspace).not.toHaveBeenCalled();
  });

  it("does not create another folder for later messages in the same session", async () => {
    await useRuntimeStore.getState().sendPrompt("first");
    await useRuntimeStore.getState().sendPrompt("second");
    expect(mocks.newDatedWorkspace).toHaveBeenCalledTimes(1);
  });

  it("masks transient connect errors while deliberately reconnecting", async () => {
    mocks.failConnects = 1;
    const done = useRuntimeStore.getState().connectRetry(3);
    await new Promise((r) => setTimeout(r, 50)); // after the first failed attempt
    expect(useRuntimeStore.getState().status).toBe("connecting");
    expect(useRuntimeStore.getState().error).toBe(null);
    await done;
    expect(useRuntimeStore.getState().status).toBe("ready");
    expect(useRuntimeStore.getState().error).toBe(null);
  });

  it("never passes through 'offline' while retrying (first-boot page flicker)", async () => {
    // On a fresh install the retry loop runs for minutes (macOS TCC dialog);
    // each attempt tears down the previous client, whose close() emits
    // "offline" — if that reaches the store, the page flips between the
    // offline help card and the connecting screen once per attempt.
    mocks.failConnects = 1;
    const seen: string[] = [];
    const unsub = useRuntimeStore.subscribe((s, prev) => {
      if (s.status !== prev.status) seen.push(s.status);
    });
    await useRuntimeStore.getState().connectRetry(3);
    unsub();
    expect(useRuntimeStore.getState().status).toBe("ready");
    expect(seen).not.toContain("offline");
  });

  it("surfaces the last error only when the retry window is exhausted", async () => {
    mocks.failConnects = 99;
    await expect(useRuntimeStore.getState().connectRetry(1)).resolves.toBeUndefined();
    expect(useRuntimeStore.getState().status).toBe("error");
    expect(useRuntimeStore.getState().error).toContain("event stream");
  });

  it("disconnect synchronously invalidates an in-flight connect", async () => {
    const gate = deferred();
    mocks.connectWaits.push(gate.promise);
    const pending = useRuntimeStore.getState().connect();
    await vi.waitFor(() => expect(useRuntimeStore.getState().status).toBe("connecting"));

    useRuntimeStore.getState().disconnect();
    expect(useRuntimeStore.getState().status).toBe("offline");
    gate.resolve();
    await pending;

    // The stale client's eventual ready callback/result cannot resurrect it.
    expect(useRuntimeStore.getState().status).toBe("offline");
    expect(mocks.clientOpen).toBe(false);
  });

  it("disconnect cancels throttled live folds from the old client", async () => {
    vi.useFakeTimers();
    try {
      useRuntimeStore.setState({
        currentId: "ses_live",
        threads: { ses_live: { blocks: [], index: {}, loaded: true } },
      });
      const update = (partialOutput: string) =>
        mocks.fireEvent({
          type: "tool.updated",
          sessionId: "ses_live",
          callId: "call_live",
          tool: "bash",
          status: "running",
          title: "work",
          input: { command: "work" },
          partialOutput,
        });
      update("first");
      update("stale-after-disconnect"); // throttled into a pending timer
      expect(useRuntimeStore.getState().threads.ses_live.blocks[0]).toMatchObject({
        partialOutput: "first",
      });

      useRuntimeStore.getState().disconnect();
      await vi.advanceTimersByTimeAsync(300);
      expect(useRuntimeStore.getState().threads.ses_live.blocks[0]).toMatchObject({
        partialOutput: "first",
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("a newer manual connect wins without a stale completion overwriting it", async () => {
    const firstGate = deferred();
    mocks.connectWaits.push(firstGate.promise);
    const first = useRuntimeStore.getState().connect();
    await vi.waitFor(() => expect(useRuntimeStore.getState().status).toBe("connecting"));

    const second = useRuntimeStore.getState().connect();
    await second;
    expect(useRuntimeStore.getState().status).toBe("ready");

    firstGate.resolve();
    await first;
    expect(useRuntimeStore.getState().status).toBe("ready");
    expect(useRuntimeStore.getState().error).toBe(null);
  });

  it("a superseded openSession does not start a second, dueling reconnect", async () => {
    // Opening a folder-scoped session reconnects the SSE stream. If a newer
    // open (rapid switching, or an effect that fires twice) overlaps an older
    // one, TWO connectRetry loops must NOT run: they tear down each other's
    // in-flight EventSource and leak half-open sockets until the webview's
    // per-host connection pool is exhausted and every later session hangs.
    useRuntimeStore.setState({
      sessions: [
        { id: "A", title: "A", directory: "/ws/A" },
        { id: "B", title: "B", directory: "/ws/B" },
      ] as never,
    });
    const before = mocks.clientOpts.length;

    // Fire both without awaiting the first — the exact overlap seen in the wild.
    await Promise.all([
      useRuntimeStore.getState().openSession("A"),
      useRuntimeStore.getState().openSession("B"),
    ]);

    // Only the winner reconnects (one new client), and only its history loads.
    expect(mocks.clientOpts.length - before).toBe(1);
    expect(useRuntimeStore.getState().currentId).toBe("B");
    expect(mocks.getMessages).toHaveBeenLastCalledWith("B");
  });

  it("echoes the first message instantly into the draft, then grafts it onto the session", async () => {
    const p = useRuntimeStore.getState().sendPrompt("hi");
    // Synchronously (before any await resolves): the message is visible and
    // the composer is locked — the user is never staring at an unchanged page.
    expect(useRuntimeStore.getState().sending).toBe(true);
    expect(useRuntimeStore.getState().threads[DRAFT_KEY]?.blocks).toEqual([
      { kind: "user", text: "hi" },
    ]);
    await p;
    const s = useRuntimeStore.getState();
    expect(s.currentId).toBe("ses_new");
    expect(s.threads[DRAFT_KEY]).toBeUndefined();
    expect(s.threads["ses_new"].blocks).toEqual([{ kind: "user", text: "hi" }]);
    expect(s.sending).toBe(false);
    expect(s.runningSessions["ses_new"]).toBe(true); // turn active until idle
  });

  it("ignores a second send while one is in flight", async () => {
    const p = useRuntimeStore.getState().sendPrompt("hi");
    const second = await useRuntimeStore.getState().sendPrompt("hi again");
    expect(second).toBe(null);
    await p;
    expect(useRuntimeStore.getState().threads[DRAFT_KEY] ?? undefined).toBeUndefined();
    expect(useRuntimeStore.getState().threads["ses_new"].blocks).toHaveLength(1);
  });

  it("session.idle ends the turn: running cleared, done line folded in", async () => {
    await useRuntimeStore.getState().sendPrompt("hi");
    expect(useRuntimeStore.getState().runningSessions["ses_new"]).toBe(true);
    mocks.fireEvent({ type: "session.idle", sessionId: "ses_new" });
    const s = useRuntimeStore.getState();
    expect(s.runningSessions["ses_new"]).toBeUndefined();
    expect(s.threads["ses_new"].blocks.slice(-1)[0]).toMatchObject({ kind: "status-line", tone: "done" });
  });

  it("a session error lands as a red line in the thread and unlocks the turn", async () => {
    await useRuntimeStore.getState().sendPrompt("hi");
    mocks.fireEvent({ type: "error", sessionId: "ses_new", message: "model unavailable" });
    const s = useRuntimeStore.getState();
    expect(s.runningSessions["ses_new"]).toBeUndefined();
    expect(s.threads["ses_new"].blocks.slice(-1)[0]).toEqual({
      kind: "status-line",
      text: "model unavailable",
      tone: "error",
    });
  });

  it("retries a failed createSession once (transient 'Load failed')", async () => {
    mocks.failCreates = 1;
    const id = await useRuntimeStore.getState().sendPrompt("hi");
    expect(id).toBe("ses_new");
    expect(useRuntimeStore.getState().error).toBe(null);
  });

  it("a hard create failure shows a red line in the draft and unlocks the composer", async () => {
    mocks.failCreates = 99;
    const id = await useRuntimeStore.getState().sendPrompt("hi");
    expect(id).toBe(null);
    const s = useRuntimeStore.getState();
    expect(s.sending).toBe(false);
    expect(s.threads[DRAFT_KEY].blocks.slice(-1)[0]).toMatchObject({
      kind: "status-line",
      tone: "error",
    });
  });

  it("does not send a prompt after Disconnect while draft creation is pending", async () => {
    const gate = deferred();
    mocks.createWaits.push(gate.promise);
    mocks.createdSessionId = "ses_disconnected_draft";
    useRuntimeStore.setState({ currentId: null, workspacePinned: true, threads: {} });

    const sending = useRuntimeStore.getState().sendPrompt("cancel before prompt");
    await vi.waitFor(() => expect(mocks.createSession).toHaveBeenCalledTimes(1));
    useRuntimeStore.getState().disconnect();
    gate.resolve();

    await expect(sending).resolves.toBeNull();
    expect(mocks.sendPrompt).not.toHaveBeenCalled();
    expect(useRuntimeStore.getState().currentId).toBeNull();
    expect(useRuntimeStore.getState().status).toBe("offline");
  });

  it("does not retry a failed prompt after Disconnect", async () => {
    const gate = deferred();
    mocks.sendWaits.push(gate.promise);
    mocks.failPrompts = 1;
    useRuntimeStore.setState({
      currentId: "ses_disconnect_retry",
      threads: { ses_disconnect_retry: { loaded: true, index: {}, blocks: [] } },
    });

    const sending = useRuntimeStore.getState().sendPrompt("only one attempt");
    await vi.waitFor(() => expect(mocks.sendPrompt).toHaveBeenCalledTimes(1));
    useRuntimeStore.getState().disconnect();
    gate.resolve();

    await expect(sending).resolves.toBeNull();
    expect(mocks.sendPrompt).toHaveBeenCalledTimes(1);
    expect(useRuntimeStore.getState().status).toBe("offline");
  });

  it("does not send a draft prompt through an endpoint replaced during session creation", async () => {
    const gate = deferred();
    mocks.createWaits.push(gate.promise);
    mocks.createdSessionId = "ses_restarted_draft";
    useRuntimeStore.setState({ currentId: null, workspacePinned: true, threads: {} });

    const sending = useRuntimeStore.getState().sendPrompt("do not use old endpoint");
    await vi.waitFor(() => expect(mocks.createSession).toHaveBeenCalledTimes(1));
    await useRuntimeStore.getState().setProxySetting("none", "");
    gate.resolve();

    await expect(sending).resolves.toBeNull();
    expect(mocks.sendPrompt).not.toHaveBeenCalled();
    expect(useRuntimeStore.getState().currentId).toBeNull();
    expect(useRuntimeStore.getState().status).toBe("ready");
  });

  it("does not retry a failed prompt through an endpoint replaced by a mutation", async () => {
    const gate = deferred();
    mocks.sendWaits.push(gate.promise);
    mocks.failPrompts = 1;
    useRuntimeStore.setState({
      currentId: "ses_restart_retry",
      threads: { ses_restart_retry: { loaded: true, index: {}, blocks: [] } },
    });

    const sending = useRuntimeStore.getState().sendPrompt("only old endpoint sees this once");
    await vi.waitFor(() => expect(mocks.sendPrompt).toHaveBeenCalledTimes(1));
    await useRuntimeStore.getState().setProxySetting("none", "");
    gate.resolve();

    await expect(sending).resolves.toBeNull();
    expect(mocks.sendPrompt).toHaveBeenCalledTimes(1);
    expect(useRuntimeStore.getState().status).toBe("ready");
  });

  it("does not send a draft prompt after the user adopts a different server URL", async () => {
    const gate = deferred();
    mocks.createWaits.push(gate.promise);
    mocks.createdSessionId = "ses_changed_url_draft";
    useRuntimeStore.setState({ currentId: null, workspacePinned: true, threads: {} });

    const sending = useRuntimeStore.getState().sendPrompt("stay off the old server");
    await vi.waitFor(() => expect(mocks.createSession).toHaveBeenCalledTimes(1));
    useRuntimeStore.getState().setServerUrl("http://127.0.0.1:43199");
    await useRuntimeStore.getState().connect();
    gate.resolve();

    await expect(sending).resolves.toBeNull();
    expect(mocks.sendPrompt).not.toHaveBeenCalled();
    expect(useRuntimeStore.getState().currentId).toBeNull();
    expect(mocks.clientOpts[mocks.clientOpts.length - 1]).toMatchObject({
      baseUrl: "http://127.0.0.1:43199",
    });
  });

  it("does not retry a failed prompt after the user adopts a different server URL", async () => {
    const gate = deferred();
    mocks.sendWaits.push(gate.promise);
    mocks.failPrompts = 1;
    useRuntimeStore.setState({
      currentId: "ses_changed_url_retry",
      threads: { ses_changed_url_retry: { loaded: true, index: {}, blocks: [] } },
    });

    const sending = useRuntimeStore.getState().sendPrompt("old server gets one attempt");
    await vi.waitFor(() => expect(mocks.sendPrompt).toHaveBeenCalledTimes(1));
    useRuntimeStore.getState().setServerUrl("http://127.0.0.1:43200");
    await useRuntimeStore.getState().connect();
    gate.resolve();

    await expect(sending).resolves.toBeNull();
    expect(mocks.sendPrompt).toHaveBeenCalledTimes(1);
    expect(mocks.clientOpts[mocks.clientOpts.length - 1]).toMatchObject({
      baseUrl: "http://127.0.0.1:43200",
    });
  });

  it("disconnects the old endpoint and clears its session namespace when the URL changes", async () => {
    useRuntimeStore.setState({
      currentId: "ses_old_endpoint",
      sessions: [
        { id: "ses_old_endpoint", title: "Old endpoint", directory: "/ws/base" },
      ] as never,
      threads: {
        ses_old_endpoint: {
          loaded: true,
          index: {},
          blocks: [{ kind: "agent", markdown: "old endpoint data" }],
        },
      },
      runningSessions: { ses_old_endpoint: true },
      shellTurns: { ses_old_endpoint: true },
      questions: [{ requestId: "q_old", sessionId: "ses_old_endpoint" }] as never,
      permissions: [{ requestId: "p_old", sessionId: "ses_old_endpoint" }] as never,
    });

    const nextUrl = "http://127.0.0.1:43201";
    useRuntimeStore.getState().setServerUrl(nextUrl);

    const changed = useRuntimeStore.getState();
    expect(mocks.clientOpen).toBe(false);
    expect(changed.status).toBe("offline");
    expect(changed.serverUrl).toBe(nextUrl);
    expect(changed.currentId).toBeNull();
    expect(changed.sessions).toEqual([]);
    expect(changed.threads).toEqual({});
    expect(changed.runningSessions).toEqual({});
    expect(changed.shellTurns).toEqual({});
    expect(changed.questions).toEqual([]);
    expect(changed.permissions).toEqual([]);

    await expect(changed.sendPrompt("must not reach the old client")).resolves.toBeNull();
    expect(mocks.createSession).not.toHaveBeenCalled();
    expect(mocks.sendPrompt).not.toHaveBeenCalled();

    await useRuntimeStore.getState().connect();
    expect(mocks.clientOpts[mocks.clientOpts.length - 1]).toMatchObject({ baseUrl: nextUrl });
    expect(useRuntimeStore.getState().currentId).toBeNull();
  });

  it("does not let an old sync command settlement mutate the replacement namespace", async () => {
    const gate = deferred();
    mocks.commandWaits.push(gate.promise);
    mocks.suppressCommandEvents = true;
    useRuntimeStore.setState({
      currentId: "ses_shared_id",
      threads: { ses_shared_id: { loaded: true, index: {}, blocks: [] } },
    });

    const oldCommand = useRuntimeStore.getState().runCommand("init");
    await vi.waitFor(() =>
      expect(mocks.runCommand).toHaveBeenCalledWith("ses_shared_id", "init", undefined, undefined),
    );

    useRuntimeStore.getState().setServerUrl("http://127.0.0.1:43203");
    await useRuntimeStore.getState().connect();
    const replacementThread = {
      loaded: true,
      index: {},
      blocks: [{ kind: "agent", markdown: "replacement endpoint turn" }] as never,
    };
    useRuntimeStore.setState({
      currentId: "ses_shared_id",
      threads: { ses_shared_id: replacementThread },
      runningSessions: { ses_shared_id: true },
    });
    gate.resolve();

    await expect(oldCommand).resolves.toBeNull();
    const state = useRuntimeStore.getState();
    expect(state.runningSessions.ses_shared_id).toBe(true);
    expect(state.threads.ses_shared_id).toBe(replacementThread);
  });

  it("keeps a late async turn running across a same-endpoint client reconnect", async () => {
    const gate = deferred();
    mocks.sendWaits.push(gate.promise);
    useRuntimeStore.setState({
      currentId: "ses_reconnected_async",
      threads: { ses_reconnected_async: { loaded: true, index: {}, blocks: [] } },
    });

    const sending = useRuntimeStore.getState().sendPrompt("accepted on the prior client");
    await vi.waitFor(() =>
      expect(mocks.sendPrompt).toHaveBeenCalledWith("ses_reconnected_async"),
    );
    await useRuntimeStore.getState().connect();
    gate.resolve();

    await expect(sending).resolves.toBe("ses_reconnected_async");
    expect(useRuntimeStore.getState().runningSessions.ses_reconnected_async).toBe(true);
  });

  it("reconciles a completed sync turn across a same-endpoint client reconnect", async () => {
    const gate = deferred();
    mocks.commandWaits.push(gate.promise);
    mocks.suppressCommandEvents = true;
    useRuntimeStore.setState({
      currentId: "ses_reconnected_sync",
      threads: { ses_reconnected_sync: { loaded: true, index: {}, blocks: [] } },
    });

    const command = useRuntimeStore.getState().runCommand("init");
    await vi.waitFor(() =>
      expect(mocks.runCommand).toHaveBeenCalledWith(
        "ses_reconnected_sync",
        "init",
        undefined,
        undefined,
      ),
    );
    expect(useRuntimeStore.getState().runningSessions.ses_reconnected_sync).toBe(true);
    await useRuntimeStore.getState().connect();
    mocks.messages = [
      { role: "user", parts: [{ type: "text", text: "/init" }] },
      { role: "assistant", completed: 1, parts: [{ type: "text", text: "done" }] },
    ];
    gate.resolve();

    await expect(command).resolves.toBe("ses_reconnected_sync");
    await useRuntimeStore.getState().reconcileRunning();
    await vi.waitFor(() =>
      expect(useRuntimeStore.getState().runningSessions.ses_reconnected_sync).toBeUndefined(),
    );
  });

  it("a failed send stays on its origin thread after the user opens another session", async () => {
    const gate = deferred();
    mocks.sendWaits.push(gate.promise);
    mocks.failPrompts = 2; // both bounded attempts fail
    useRuntimeStore.setState({
      currentId: "ses_origin",
      sessions: [
        { id: "ses_origin", title: "Origin", directory: "/ws/base" },
        { id: "ses_destination", title: "Destination", directory: "/ws/base" },
      ] as never,
      threads: {
        ses_origin: { loaded: true, index: {}, blocks: [] },
        ses_destination: {
          loaded: true,
          index: {},
          blocks: [{ kind: "agent", markdown: "destination stays clean" }],
        },
      },
    });

    const sending = useRuntimeStore.getState().sendPrompt("from origin");
    await vi.waitFor(() => expect(mocks.sendPrompt).toHaveBeenCalledWith("ses_origin"));
    await useRuntimeStore.getState().openSession("ses_destination");
    gate.resolve();
    await expect(sending).resolves.toBeNull();

    const state = useRuntimeStore.getState();
    expect(state.currentId).toBe("ses_destination");
    expect(state.threads.ses_origin.blocks.slice(-1)[0]).toMatchObject({
      kind: "status-line",
      tone: "error",
    });
    expect(state.threads.ses_destination.blocks).toEqual([
      { kind: "agent", markdown: "destination stays clean" },
    ]);
  });

  it("a draft session created after navigation continues in the background without stealing route", async () => {
    const gate = deferred();
    mocks.createdSessionId = "ses_background_draft";
    mocks.createWaits.push(gate.promise);
    useRuntimeStore.setState({ currentId: null, workspacePinned: true, threads: {} });

    const sending = useRuntimeStore.getState().sendPrompt("background draft");
    await vi.waitFor(() => expect(mocks.createSession).toHaveBeenCalledTimes(1));
    useRuntimeStore.setState({
      sessions: [{ id: "ses_destination", title: "Destination", directory: "/ws/base" }] as never,
      threads: {
        ...useRuntimeStore.getState().threads,
        ses_destination: {
          loaded: true,
          index: {},
          blocks: [{ kind: "agent", markdown: "selected conversation" }],
        },
      },
    });
    await useRuntimeStore.getState().openSession("ses_destination");
    gate.resolve();
    await expect(sending).resolves.toBeNull();

    const state = useRuntimeStore.getState();
    expect(state.currentId).toBe("ses_destination");
    expect(state.threads.ses_destination.blocks).toEqual([
      { kind: "agent", markdown: "selected conversation" },
    ]);
    expect(state.threads.ses_background_draft.blocks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "user", text: "background draft" }),
      ]),
    );
    expect(mocks.sendPrompt).toHaveBeenCalledWith("ses_background_draft");
  });

  it("a skill install created after navigation stays in the background on its captured client", async () => {
    const gate = deferred();
    mocks.createdSessionId = "ses_skill_install";
    mocks.createWaits.push(gate.promise);
    useRuntimeStore.setState({ currentId: null, threads: {} });

    const installing = useRuntimeStore.getState().installSkill("https://example.test/skill.md");
    await vi.waitFor(() => expect(mocks.createSession).toHaveBeenCalledTimes(1));
    useRuntimeStore.setState({
      sessions: [
        { id: "ses_destination", title: "Destination", directory: "/ws/destination" },
      ] as never,
      threads: {
        ses_destination: {
          loaded: true,
          index: {},
          blocks: [{ kind: "agent", markdown: "selected conversation" }],
        },
      },
    });
    await useRuntimeStore.getState().openSession("ses_destination");
    gate.resolve();
    await expect(installing).resolves.toBeNull();

    const state = useRuntimeStore.getState();
    expect(state.currentId).toBe("ses_destination");
    expect(state.threads.ses_skill_install.blocks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "user", text: expect.stringContaining("Install skill") }),
      ]),
    );
    expect(state.runningSessions.ses_skill_install).toBe(true);
    expect(mocks.sendPrompt).toHaveBeenCalledWith("ses_skill_install");
  });

  it("does not start a skill agent after Disconnect while its session is being created", async () => {
    const gate = deferred();
    mocks.createdSessionId = "ses_disconnected_install";
    mocks.createWaits.push(gate.promise);

    const installing = useRuntimeStore.getState().installSkill("safe skill");
    await vi.waitFor(() => expect(mocks.createSession).toHaveBeenCalledTimes(1));
    useRuntimeStore.getState().disconnect();
    gate.resolve();

    await expect(installing).resolves.toBeNull();
    expect(mocks.sendPrompt).not.toHaveBeenCalled();
    expect(useRuntimeStore.getState().threads.ses_disconnected_install).toBeUndefined();
    expect(useRuntimeStore.getState().status).toBe("offline");
  });

  it("does not start a skill agent through an endpoint replaced during session creation", async () => {
    const gate = deferred();
    mocks.createdSessionId = "ses_restarted_install";
    mocks.createWaits.push(gate.promise);

    const installing = useRuntimeStore.getState().installSkill("safe skill");
    await vi.waitFor(() => expect(mocks.createSession).toHaveBeenCalledTimes(1));
    await useRuntimeStore.getState().setProxySetting("none", "");
    gate.resolve();

    await expect(installing).resolves.toBeNull();
    expect(mocks.sendPrompt).not.toHaveBeenCalled();
    expect(useRuntimeStore.getState().threads.ses_restarted_install).toBeUndefined();
    expect(useRuntimeStore.getState().status).toBe("ready");
  });

  it("does not restore a running prompt lock when its old POST succeeds after restart", async () => {
    const gate = deferred();
    mocks.sendWaits.push(gate.promise);
    useRuntimeStore.setState({
      currentId: "ses_late_restart_prompt",
      threads: { ses_late_restart_prompt: { loaded: true, index: {}, blocks: [] } },
    });

    const sending = useRuntimeStore.getState().sendPrompt("late old success");
    await vi.waitFor(() =>
      expect(mocks.sendPrompt).toHaveBeenCalledWith("ses_late_restart_prompt"),
    );
    await useRuntimeStore.getState().setProxySetting("none", "");
    gate.resolve();

    await expect(sending).resolves.toBeNull();
    const state = useRuntimeStore.getState();
    expect(state.sending).toBe(false);
    expect(state.runningSessions.ses_late_restart_prompt).toBeUndefined();
  });

  it("clears a skill install lock when its old POST succeeds after restart", async () => {
    const gate = deferred();
    mocks.createdSessionId = "ses_late_restart_install";
    mocks.sendWaits.push(gate.promise);

    const installing = useRuntimeStore.getState().installSkill("safe skill");
    await vi.waitFor(() =>
      expect(mocks.sendPrompt).toHaveBeenCalledWith("ses_late_restart_install"),
    );
    expect(useRuntimeStore.getState().runningSessions.ses_late_restart_install).toBe(true);

    await useRuntimeStore.getState().setProxySetting("none", "");
    expect(useRuntimeStore.getState().runningSessions.ses_late_restart_install).toBeUndefined();
    await expect(useRuntimeStore.getState().sendPrompt("new turn after restart")).resolves.toBe(
      "ses_late_restart_install",
    );
    expect(useRuntimeStore.getState().runningSessions.ses_late_restart_install).toBe(true);
    gate.resolve();

    await expect(installing).resolves.toBeNull();
    expect(useRuntimeStore.getState().runningSessions.ses_late_restart_install).toBe(true);
  });

  it("does not let a rejected old install clear a newer same-session turn", async () => {
    const gate = deferred();
    mocks.createdSessionId = "ses_reconnected_install";
    mocks.sendWaits.push(gate.promise);

    const installing = useRuntimeStore.getState().installSkill("safe skill");
    await vi.waitFor(() =>
      expect(mocks.sendPrompt).toHaveBeenCalledWith("ses_reconnected_install"),
    );
    await useRuntimeStore.getState().connect();
    await useRuntimeStore.getState().interrupt();
    await expect(useRuntimeStore.getState().sendPrompt("new turn owns the lock")).resolves.toBe(
      "ses_reconnected_install",
    );
    expect(useRuntimeStore.getState().runningSessions.ses_reconnected_install).toBe(true);

    mocks.failPrompts = 1;
    gate.resolve();
    await expect(installing).resolves.toBeNull();

    const state = useRuntimeStore.getState();
    expect(state.runningSessions.ses_reconnected_install).toBe(true);
    expect(state.threads.ses_reconnected_install.blocks).not.toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "status-line", text: expect.stringContaining("Install failed") }),
      ]),
    );
  });

  it("marks a deliberate switch as `switching` for its whole duration", async () => {
    mocks.failConnects = 1; // keep the reconnect in flight for one retry beat
    const done = useRuntimeStore.getState().switchWorkspace({ path: "/ws/mine" });
    await new Promise((r) => setTimeout(r, 50));
    expect(useRuntimeStore.getState().switching).toBe(true);
    await done;
    expect(useRuntimeStore.getState().switching).toBe(false);
    expect(useRuntimeStore.getState().status).toBe("ready");
  });

  it("bootstrap does not reconnect after a disconnect during runtime startup", async () => {
    const gate = deferred();
    mocks.startRuntimeUrl = "http://127.0.0.1:43140";
    mocks.startRuntime.mockImplementationOnce(async () => {
      await gate.promise;
      return mocks.startRuntimeUrl;
    });
    const before = mocks.clientOpts.length;

    const boot = useRuntimeStore.getState().bootstrap();
    await vi.waitFor(() => expect(mocks.startRuntime).toHaveBeenCalled());
    useRuntimeStore.getState().disconnect();
    gate.resolve();
    await boot;

    expect(useRuntimeStore.getState().status).toBe("offline");
    expect(useRuntimeStore.getState().serverUrl).toBe(mocks.startRuntimeUrl);
    expect(mocks.clientOpts).toHaveLength(before);
  });

  it("a session opened before the client exists resumes after Connect", async () => {
    useRuntimeStore.getState().disconnect();
    mocks.messages = [
      { role: "assistant", completed: 1, parts: [{ type: "text", text: "cold route history" }] },
    ];
    useRuntimeStore.setState({
      sessions: [{ id: "ses_cold", title: "Cold", directory: "/ws/base" }] as never,
      threads: {},
    });

    await useRuntimeStore.getState().openSession("ses_cold");
    expect(mocks.getMessages).not.toHaveBeenCalledWith("ses_cold");
    await useRuntimeStore.getState().connect();
    await vi.waitFor(() => expect(mocks.getMessages).toHaveBeenCalledWith("ses_cold"));
    await vi.waitFor(() => expect(useRuntimeStore.getState().threads.ses_cold?.loaded).toBe(true));

    expect(useRuntimeStore.getState().threads.ses_cold.blocks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "agent", markdown: "cold route history" }),
      ]),
    );
  });

  it("switchWorkspace does not reconnect after disconnect during its setup await", async () => {
    const gate = deferred();
    mocks.setWorkspace.mockImplementationOnce(async (path: string) => {
      await gate.promise;
      return path;
    });
    const before = mocks.clientOpts.length;

    const switching = useRuntimeStore.getState().switchWorkspace({ path: "/ws/slow" });
    await vi.waitFor(() => expect(mocks.setWorkspace).toHaveBeenCalledWith("/ws/slow"));
    expect(useRuntimeStore.getState().switching).toBe(true);
    useRuntimeStore.getState().disconnect();
    expect(useRuntimeStore.getState().switching).toBe(false);
    gate.resolve();
    await switching;

    expect(useRuntimeStore.getState().status).toBe("offline");
    expect(mocks.clientOpts).toHaveLength(before);
  });

  it("a manual Connect waits for an in-flight workspace setter and uses its new scope", async () => {
    const gate = deferred();
    mocks.setWorkspace.mockImplementationOnce(async (path: string) => {
      await gate.promise;
      mocks.workspacePathValue = path;
      return path;
    });
    const before = mocks.clientOpts.length;

    const switching = useRuntimeStore.getState().switchWorkspace({ path: "/ws/authoritative" });
    await vi.waitFor(() =>
      expect(mocks.setWorkspace).toHaveBeenCalledWith("/ws/authoritative"),
    );
    useRuntimeStore.getState().disconnect();
    const connecting = useRuntimeStore.getState().connect();
    await Promise.resolve();
    expect(mocks.clientOpts).toHaveLength(before);

    gate.resolve();
    await Promise.all([switching, connecting]);

    expect(useRuntimeStore.getState().status).toBe("ready");
    expect(useRuntimeStore.getState().workspace).toBe("/ws/authoritative");
    expect(mocks.clientOpts[mocks.clientOpts.length - 1]).toMatchObject({
      directory: "/ws/authoritative",
    });
  });

  it("openSession does not reconnect after disconnect during its workspace await", async () => {
    const gate = deferred();
    mocks.setWorkspace.mockImplementationOnce(async (path: string) => {
      await gate.promise;
      return path;
    });
    useRuntimeStore.setState({
      sessions: [{ id: "ses_slow", title: "Slow", directory: "/ws/slow" }] as never,
    });
    const before = mocks.clientOpts.length;

    const opening = useRuntimeStore.getState().openSession("ses_slow");
    await vi.waitFor(() => expect(mocks.setWorkspace).toHaveBeenCalledWith("/ws/slow"));
    useRuntimeStore.getState().disconnect();
    gate.resolve();
    await opening;

    expect(useRuntimeStore.getState().status).toBe("offline");
    expect(useRuntimeStore.getState().switching).toBe(false);
    expect(mocks.clientOpts).toHaveLength(before);
  });

  it("does not send through the old client while a session workspace switch is pending", async () => {
    const gate = deferred();
    mocks.setWorkspace.mockImplementationOnce(async (path: string) => {
      await gate.promise;
      mocks.workspacePathValue = path;
      return path;
    });
    useRuntimeStore.setState({
      sessions: [{ id: "ses_guard", title: "Guard", directory: "/ws/guard" }] as never,
      threads: {},
    });

    const opening = useRuntimeStore.getState().openSession("ses_guard");
    await vi.waitFor(() => expect(mocks.setWorkspace).toHaveBeenCalledWith("/ws/guard"));
    expect(useRuntimeStore.getState().switching).toBe(true);
    await expect(useRuntimeStore.getState().sendPrompt("wrong workspace")).resolves.toBeNull();
    await expect(
      useRuntimeStore.getState().installSkill("https://example.test/wrong-workspace.md"),
    ).resolves.toBeNull();
    expect(mocks.sendPrompt).not.toHaveBeenCalled();
    expect(mocks.createSession).not.toHaveBeenCalled();
    expect(useRuntimeStore.getState().error).toMatch(/workspace.*switch/i);

    gate.resolve();
    await opening;
  });

  it("Connect resumes an openSession superseded by Disconnect after its workspace setter", async () => {
    const gate = deferred();
    mocks.setWorkspace.mockImplementationOnce(async (path: string) => {
      await gate.promise;
      mocks.workspacePathValue = path;
      return path;
    });
    mocks.messages = [
      { role: "assistant", completed: 1, parts: [{ type: "text", text: "restored history" }] },
    ];
    useRuntimeStore.setState({
      sessions: [{ id: "ses_resume", title: "Resume", directory: "/ws/resume" }] as never,
      threads: {},
    });

    const opening = useRuntimeStore.getState().openSession("ses_resume");
    await vi.waitFor(() => expect(mocks.setWorkspace).toHaveBeenCalledWith("/ws/resume"));
    useRuntimeStore.getState().disconnect();
    const connecting = useRuntimeStore.getState().connect();
    gate.resolve();
    await Promise.all([opening, connecting]);
    await vi.waitFor(() =>
      expect(useRuntimeStore.getState().threads.ses_resume?.loaded).toBe(true),
    );

    expect(mocks.getMessages).toHaveBeenCalledWith("ses_resume");
    expect(useRuntimeStore.getState().currentId).toBe("ses_resume");
    expect(mocks.clientOpts[mocks.clientOpts.length - 1]).toMatchObject({ directory: "/ws/resume" });
  });

  it("a newer openSession restores its directory even when it matched the pre-switch scope", async () => {
    const gate = deferred();
    mocks.setWorkspace.mockImplementationOnce(async (path: string) => {
      await gate.promise;
      mocks.workspacePathValue = path;
      return path;
    });
    mocks.messages = [
      { role: "assistant", completed: 1, parts: [{ type: "text", text: "base session" }] },
    ];
    useRuntimeStore.setState({
      sessions: [{ id: "ses_base", title: "Base", directory: "/ws/base" }] as never,
      threads: {},
    });

    const olderSwitch = useRuntimeStore.getState().switchWorkspace({ path: "/ws/a" });
    await vi.waitFor(() => expect(mocks.setWorkspace).toHaveBeenCalledWith("/ws/a"));
    const newerOpen = useRuntimeStore.getState().openSession("ses_base");
    gate.resolve();
    await Promise.all([olderSwitch, newerOpen]);

    expect(useRuntimeStore.getState().currentId).toBe("ses_base");
    expect(mocks.workspacePathValue).toBe("/ws/base");
    expect(useRuntimeStore.getState().workspace).toBe("/ws/base");
    expect(useRuntimeStore.getState().switching).toBe(false);
    expect(mocks.clientOpts[mocks.clientOpts.length - 1]).toMatchObject({ directory: "/ws/base" });
  });

  it("performTurn fails safely instead of reconnecting after disconnect during folder setup", async () => {
    const gate = deferred();
    mocks.newDatedWorkspace.mockImplementationOnce(async (name: string) => {
      await gate.promise;
      return `/ws/${name}`;
    });
    const before = mocks.clientOpts.length;

    const sending = useRuntimeStore.getState().sendPrompt("do not revive");
    await vi.waitFor(() => expect(mocks.newDatedWorkspace).toHaveBeenCalled());
    useRuntimeStore.getState().disconnect();
    gate.resolve();
    await expect(sending).resolves.toBe(null);

    const state = useRuntimeStore.getState();
    expect(state.status).toBe("offline");
    expect(state.sending).toBe(false);
    expect(state.threads[DRAFT_KEY].blocks.slice(-1)[0]).toMatchObject({
      kind: "status-line",
      tone: "error",
    });
    expect(mocks.clientOpts).toHaveLength(before);
  });

  it("runShell is rejected by the internal safety policy", async () => {
    const id = await useRuntimeStore.getState().runShell("pwd");
    expect(id).toBeNull();
    expect(mocks.runShell).not.toHaveBeenCalled();
    const s = useRuntimeStore.getState();
    expect(s.error).toContain("Direct shell mode is disabled");
  });

  it("an agent bash step (no shell turn) stays a quiet line without inline output", async () => {
    await useRuntimeStore.getState().sendPrompt("hi");
    mocks.fireEvent({
      type: "tool.updated",
      sessionId: "ses_new",
      callId: "c9",
      tool: "bash",
      status: "success",
      title: "install deps",
      input: { command: "pip install numpy" },
      output: "lots of pip noise",
    });
    const bash = useRuntimeStore
      .getState()
      .threads["ses_new"].blocks.find((b) => b.kind === "tool-call");
    // A bash step is titled by its (de-noised) command — the honest record —
    // not the model's free-text description.
    expect(bash).toMatchObject({ title: "pip install numpy", verb: "Ran", status: "success" });
    expect((bash as { outputSummary?: string }).outputSummary).toBeUndefined();
  });

  it("runCommand: echoes `/name args` and posts the command with its runtime selections", async () => {
    useRuntimeStore.setState({ selectedAgent: "research", defaultModel: "mock/research-model" });
    const id = await useRuntimeStore.getState().runCommand("init", "focus on tests");
    expect(id).toBe("ses_new");
    expect(mocks.runCommand).toHaveBeenCalledWith("ses_new", "init", "focus on tests", {
      agent: "research",
      model: "mock/research-model",
    });
    const s = useRuntimeStore.getState();
    expect(s.threads["ses_new"].blocks[0]).toEqual({ kind: "user", text: "/init focus on tests" });
    expect(s.runningSessions["ses_new"]).toBeUndefined();
  });

  it("/clear starts a new draft in the same folder without calling OpenCode command", async () => {
    useRuntimeStore.setState({
      currentId: "ses_old",
      workspacePinned: false,
      threads: {
        ses_old: { blocks: [{ kind: "user", text: "old context" }], index: {}, loaded: true },
      },
    });
    const id = await useRuntimeStore.getState().runCommand("clear");
    expect(id).toBe(null);
    expect(mocks.runCommand).not.toHaveBeenCalled();

    const cleared = useRuntimeStore.getState();
    expect(cleared.currentId).toBe(null);
    expect(cleared.workspacePinned).toBe(true);
    expect(cleared.threads.ses_old.blocks).toEqual([{ kind: "user", text: "old context" }]);
    expect(cleared.threads[DRAFT_KEY].blocks).toEqual([
      {
        kind: "status-line",
        text: "Chat context cleared. Files stay in the same folder.",
        tone: "review",
        divider: true,
      },
    ]);

    const connectsBeforeNextTurn = mocks.clientOpts.length;
    await useRuntimeStore.getState().sendPrompt("next");
    expect(mocks.newDatedWorkspace).not.toHaveBeenCalled();
    expect(mocks.clientOpts.length).toBeGreaterThan(connectsBeforeNextTurn);
  });

  it("openSession stops the loading skeleton when history fails to load", async () => {
    mocks.failMessages = true;
    useRuntimeStore.setState({
      sessions: [{ id: "ses_bad", title: "Bad session", directory: "/ws/base" }],
      currentId: null,
      threads: {},
    });

    await useRuntimeStore.getState().openSession("ses_bad");

    const thread = useRuntimeStore.getState().threads.ses_bad;
    expect(thread.loaded).toBe(true);
    expect(thread.blocks).toEqual([
      { kind: "status-line", text: "Failed to load messages: history hung", tone: "error" },
    ]);
  });

  it("openSession drops questions, permissions, and history returned by an old client", async () => {
    const gate = deferred();
    mocks.questionWaits.push(gate.promise);
    mocks.permissionWaits.push(gate.promise);
    mocks.messageWaits.push(gate.promise);
    mocks.questions = [
      {
        type: "question.asked",
        sessionId: "ses_stale",
        requestId: "q_stale_client",
        questions: [],
      },
    ];
    mocks.pendingPermissions = [
      {
        type: "permission.asked",
        sessionId: "ses_stale",
        requestId: "p_stale_client",
        action: "read",
        resources: ["old.txt"],
      },
    ];
    mocks.messages = [
      { role: "assistant", completed: 1, parts: [{ type: "text", text: "stale history" }] },
    ];
    useRuntimeStore.setState({
      sessions: [{ id: "ses_stale", title: "Stale", directory: "/ws/base" }] as never,
      threads: {},
      questions: [],
      permissions: [],
    });

    const opening = useRuntimeStore.getState().openSession("ses_stale");
    await vi.waitFor(() => {
      expect(mocks.listQuestions).toHaveBeenCalled();
      expect(mocks.listPermissions).toHaveBeenCalled();
      expect(mocks.getMessages).toHaveBeenCalledWith("ses_stale");
    });
    // The replacement client has a different authoritative snapshot. The old
    // client's gated values must remain tied to the request that captured them.
    mocks.questions = [];
    mocks.pendingPermissions = [];
    mocks.messages = [];
    await useRuntimeStore.getState().connect();
    gate.resolve();
    await opening;
    await vi.waitFor(() => {
      expect(mocks.listQuestionsReturned).toHaveBeenCalled();
      expect(mocks.listPermissionsReturned).toHaveBeenCalled();
      expect(mocks.getMessagesReturned).toHaveBeenCalled();
    });

    const state = useRuntimeStore.getState();
    expect(state.questions).toEqual([]);
    expect(state.permissions).toEqual([]);
    expect(state.threads.ses_stale).toMatchObject({ loaded: true, blocks: [] });
  });

  it("openSession reloads complete history after a live SSE race reaches idle", async () => {
    const gate = deferred();
    mocks.messageWaits.push(gate.promise);
    mocks.messages = [
      { role: "user", parts: [{ type: "text", text: "prior question" }] },
      { role: "assistant", completed: 1, parts: [{ type: "text", text: "prior answer" }] },
    ];
    useRuntimeStore.setState({
      sessions: [{ id: "ses_live_history", title: "Live", directory: "/ws/base" }] as never,
      threads: {},
    });

    const opening = useRuntimeStore.getState().openSession("ses_live_history");
    await vi.waitFor(() => expect(mocks.getMessages).toHaveBeenCalledWith("ses_live_history"));
    mocks.fireEvent({
      type: "text.updated",
      sessionId: "ses_live_history",
      partId: "txt_live",
      text: "live text",
    });
    mocks.fireEvent({
      type: "tool.updated",
      sessionId: "ses_live_history",
      callId: "tool_live",
      tool: "read",
      status: "success",
      title: "live.txt",
      input: { path: "live.txt" },
      output: "live output",
    });
    gate.resolve();
    await opening;

    // Keep the live tail renderable while separately remembering that the
    // complete authoritative history still needs to be fetched at idle.
    const liveThread = useRuntimeStore.getState().threads.ses_live_history;
    expect(liveThread.loaded).toBe(true);
    expect(liveThread.blocks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "agent", markdown: "live text" }),
        expect.objectContaining({ kind: "tool-call", title: "live.txt" }),
      ]),
    );
    mocks.messages = [
      { role: "user", parts: [{ type: "text", text: "prior question" }] },
      { role: "assistant", completed: 1, parts: [{ type: "text", text: "prior answer" }] },
      { role: "user", parts: [{ type: "text", text: "latest question" }] },
      {
        role: "assistant",
        completed: 2,
        parts: [
          { type: "text", text: "live text" },
          {
            type: "tool",
            tool: "read",
            state: {
              status: "completed",
              title: "live.txt",
              input: { path: "live.txt" },
              output: "live output",
            },
          },
        ],
      },
    ];
    mocks.fireEvent({ type: "session.idle", sessionId: "ses_live_history" });
    await vi.waitFor(() => expect(mocks.getMessages).toHaveBeenCalledTimes(2));
    await vi.waitFor(() => expect(useRuntimeStore.getState().threads.ses_live_history.loaded).toBe(true));

    const blocks = useRuntimeStore.getState().threads.ses_live_history.blocks;
    expect(blocks.filter((b) => b.kind === "agent").map((b) => b.markdown)).toEqual([
      "prior answer",
      "live text",
    ]);
    expect(blocks.filter((b) => b.kind === "tool-call")).toHaveLength(1);
    expect(blocks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "user", text: "prior question" }),
        expect.objectContaining({ kind: "tool-call", title: "live.txt" }),
      ]),
    );
  });

  it("a prior idle cannot make history recovery overwrite a newly sent optimistic turn", async () => {
    const gate = deferred();
    mocks.messageWaits.push(gate.promise);
    mocks.messages = [
      { role: "assistant", completed: 1, parts: [{ type: "text", text: "old history" }] },
    ];
    // Seed the terminal revision from the previous turn, then mimic a session
    // whose in-memory thread has not yet been loaded in this route.
    mocks.fireEvent({ type: "session.idle", sessionId: "ses_new_turn_race" });
    useRuntimeStore.setState({
      sessions: [
        { id: "ses_new_turn_race", title: "Race", directory: "/ws/base" },
      ] as never,
      threads: {},
      workspacePinned: true,
    });

    const opening = useRuntimeStore.getState().openSession("ses_new_turn_race");
    await vi.waitFor(() => expect(mocks.getMessages).toHaveBeenCalledWith("ses_new_turn_race"));
    await useRuntimeStore.getState().sendPrompt("new optimistic question");
    gate.resolve();
    await opening;

    expect(mocks.getMessages).toHaveBeenCalledTimes(1);
    expect(useRuntimeStore.getState().threads.ses_new_turn_race.blocks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "user", text: "new optimistic question" }),
      ]),
    );

    mocks.messages = [
      { role: "assistant", completed: 1, parts: [{ type: "text", text: "old history" }] },
      { role: "user", parts: [{ type: "text", text: "new optimistic question" }] },
      { role: "assistant", completed: 2, parts: [{ type: "text", text: "new answer" }] },
    ];
    mocks.fireEvent({ type: "session.idle", sessionId: "ses_new_turn_race" });
    await vi.waitFor(() => expect(mocks.getMessages).toHaveBeenCalledTimes(2));
    await vi.waitFor(() =>
      expect(useRuntimeStore.getState().threads.ses_new_turn_race.blocks).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ kind: "agent", markdown: "new answer" }),
        ]),
      ),
    );
  });

  it("a deferred history refresh from an old client cannot replace the new client's thread", async () => {
    const firstGate = deferred();
    const refreshGate = deferred();
    mocks.messageWaits.push(firstGate.promise, refreshGate.promise);
    mocks.messages = [
      { role: "assistant", completed: 1, parts: [{ type: "text", text: "old prefix" }] },
    ];
    useRuntimeStore.setState({
      sessions: [{ id: "ses_refresh_client", title: "Refresh", directory: "/ws/base" }] as never,
      threads: {},
    });

    const opening = useRuntimeStore.getState().openSession("ses_refresh_client");
    await vi.waitFor(() => expect(mocks.getMessages).toHaveBeenCalledTimes(1));
    mocks.fireEvent({
      type: "text.updated",
      sessionId: "ses_refresh_client",
      partId: "live_old_client",
      text: "live from old client",
    });
    firstGate.resolve();
    await opening;
    mocks.messages = [
      { role: "assistant", completed: 2, parts: [{ type: "text", text: "stale refresh" }] },
    ];
    mocks.fireEvent({ type: "session.idle", sessionId: "ses_refresh_client" });
    await vi.waitFor(() => expect(mocks.getMessages).toHaveBeenCalledTimes(2));

    mocks.messages = [
      { role: "assistant", completed: 3, parts: [{ type: "text", text: "authoritative new client" }] },
    ];
    await useRuntimeStore.getState().connect();
    await vi.waitFor(() => expect(mocks.getMessages).toHaveBeenCalledTimes(3));
    await vi.waitFor(() =>
      expect(useRuntimeStore.getState().threads.ses_refresh_client.blocks).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ kind: "agent", markdown: "authoritative new client" }),
        ]),
      ),
    );
    refreshGate.resolve();
    await vi.waitFor(() => expect(mocks.getMessagesReturned).toHaveBeenCalledTimes(3));

    const blocks = useRuntimeStore.getState().threads.ses_refresh_client.blocks;
    expect(blocks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "agent", markdown: "authoritative new client" }),
      ]),
    );
    expect(blocks).not.toEqual(
      expect.arrayContaining([expect.objectContaining({ markdown: "stale refresh" })]),
    );
  });

  it("openSession REST recovery cannot re-add asks resolved by newer SSE", async () => {
    const gate = deferred();
    mocks.questionWaits.push(gate.promise);
    mocks.permissionWaits.push(gate.promise);
    const resolvedQuestion = {
      type: "question.asked",
      sessionId: "ses_race",
      requestId: "q_resolved_live",
      questions: [],
    };
    const recoveredQuestion = {
      ...resolvedQuestion,
      requestId: "q_recovered",
    };
    const resolvedPermission = {
      type: "permission.asked",
      sessionId: "ses_race",
      requestId: "p_resolved_live",
      action: "read",
      resources: ["done.txt"],
    };
    const recoveredPermission = {
      ...resolvedPermission,
      requestId: "p_recovered",
    };
    mocks.questions = [resolvedQuestion, recoveredQuestion];
    mocks.pendingPermissions = [resolvedPermission, recoveredPermission];
    useRuntimeStore.setState({
      sessions: [{ id: "ses_race", title: "Race", directory: "/ws/base" }] as never,
      threads: { ses_race: { blocks: [], index: {}, loaded: true } },
      questions: [],
      permissions: [],
    });

    const opening = useRuntimeStore.getState().openSession("ses_race");
    await vi.waitFor(() => {
      expect(mocks.listQuestions).toHaveBeenCalled();
      expect(mocks.listPermissions).toHaveBeenCalled();
    });
    mocks.fireEvent(resolvedQuestion);
    mocks.fireEvent({
      type: "question.resolved",
      sessionId: "ses_race",
      requestId: resolvedQuestion.requestId,
    });
    mocks.fireEvent(resolvedPermission);
    mocks.fireEvent({
      type: "permission.resolved",
      sessionId: "ses_race",
      requestId: resolvedPermission.requestId,
    });
    gate.resolve();
    await opening;
    await vi.waitFor(() => {
      expect(useRuntimeStore.getState().questions.map((q) => q.requestId)).toContain(
        "q_recovered",
      );
      expect(useRuntimeStore.getState().permissions.map((p) => p.requestId)).toContain(
        "p_recovered",
      );
    });

    expect(useRuntimeStore.getState().questions.map((q) => q.requestId)).not.toContain(
      "q_resolved_live",
    );
    expect(useRuntimeStore.getState().permissions.map((p) => p.requestId)).not.toContain(
      "p_resolved_live",
    );
  });

  it("switchWorkspace pins the chosen folder; startDraft un-pins it", async () => {
    await useRuntimeStore.getState().switchWorkspace({ path: "/ws/mine" });
    expect(mocks.setWorkspace).toHaveBeenCalledWith("/ws/mine");
    expect(useRuntimeStore.getState().workspacePinned).toBe(true);
    useRuntimeStore.getState().startDraft();
    expect(useRuntimeStore.getState().workspacePinned).toBe(false);
  });
});

// A task tool spawns a subagent in a CHILD session; its permission asks carry
// the child's id, and a sync POST held open for a long turn is killed by
// WKWebView at ~60 s. Both must not strand the conversation.
describe("subagent permission asks and long sync turns", () => {
  it("maps a task tool's child session to the parent conversation", async () => {
    const id = await useRuntimeStore.getState().sendPrompt("explore the repo");
    mocks.fireEvent({
      type: "tool.updated",
      sessionId: id,
      callId: "c1",
      tool: "task",
      status: "running",
      title: "Explore repo",
      childSessionId: "ses_child",
    });
    mocks.fireEvent({
      type: "permission.asked",
      sessionId: "ses_child",
      requestId: "per_1",
      action: "external_directory",
      resources: ["/repo/*"],
    });
    const s = useRuntimeStore.getState();
    expect(s.sessionParents["ses_child"]).toBe(id);
    expect(rootSessionOf(s.sessionParents, "ses_child")).toBe(id);
    expect(s.permissions).toHaveLength(1);
  });

  it("keeps the turn alive when a sync POST dies mid-turn but SSE kept streaming", async () => {
    mocks.dropCommandPost = true;
    const id = await useRuntimeStore.getState().runCommand("growth-marketing");
    expect(id).toBe("ses_new");
    const s = useRuntimeStore.getState();
    expect(
      s.threads["ses_new"].blocks.some((b) => b.kind === "status-line" && b.tone === "error"),
    ).toBe(false);
    expect(s.runningSessions["ses_new"]).toBe(true); // still working server-side
    expect(s.sending).toBe(false); // composer input unlocked for the queue
    mocks.fireEvent({ type: "session.idle", sessionId: "ses_new" });
    expect(useRuntimeStore.getState().runningSessions["ses_new"]).toBeUndefined();
  });

  it("keeps a successful sync POST locked until its terminal event", async () => {
    mocks.suppressCommandEvents = true;
    await useRuntimeStore.getState().runCommand("init");

    // The HTTP response and SSE are separate connections. A successful response
    // cannot unlock early because its delayed idle could then clear a newer turn.
    expect(useRuntimeStore.getState().runningSessions.ses_new).toBe(true);
    mocks.fireEvent({ type: "session.idle", sessionId: "ses_new" });
    expect(useRuntimeStore.getState().runningSessions.ses_new).toBeUndefined();
  });

  it("a command POST that fails before any event still shows the red line", async () => {
    mocks.failCommand = true;
    await useRuntimeStore.getState().runCommand("init");
    const s = useRuntimeStore.getState();
    const blocks = s.threads["ses_new"].blocks;
    expect(blocks[blocks.length - 1]).toMatchObject({ kind: "status-line", tone: "error" });
    expect(s.runningSessions["ses_new"]).toBeUndefined();
    expect(s.sending).toBe(false);
  });

  it("reloads a sync command result whose event stream was replaced during navigation", async () => {
    mocks.workspacePathValue = "/ws/a";
    await useRuntimeStore.getState().connect();
    useRuntimeStore.setState({
      sessions: [
        { id: "ses_command_a", title: "A", directory: "/ws/a" },
        { id: "ses_command_b", title: "B", directory: "/ws/b" },
      ] as never,
      currentId: "ses_command_a",
      threads: {
        ses_command_a: { loaded: true, index: {}, blocks: [] },
        ses_command_b: { loaded: true, index: {}, blocks: [] },
      },
    });
    await useRuntimeStore.getState().openSession("ses_command_a"); // retain its directory hint
    const gate = deferred();
    mocks.commandWaits.push(gate.promise);
    mocks.suppressCommandEvents = true;

    const command = useRuntimeStore.getState().runCommand("init", "audit");
    await vi.waitFor(() =>
      expect(mocks.runCommand).toHaveBeenCalledWith("ses_command_a", "init", "audit", undefined),
    );
    await useRuntimeStore.getState().openSession("ses_command_b");
    gate.resolve();
    await expect(command).resolves.toBeNull();

    mocks.messages = [
      { role: "user", parts: [{ type: "text", text: "run the audit" }] },
      {
        role: "assistant",
        completed: 2,
        parts: [{ type: "text", text: "authoritative command result" }],
      },
    ];
    await useRuntimeStore.getState().openSession("ses_command_a");

    expect(useRuntimeStore.getState().threads.ses_command_a.blocks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "agent", markdown: "authoritative command result" }),
      ]),
    );
  });

  it("one reply answers exactly one pending ask even when others are identical", async () => {
    await useRuntimeStore.getState().sendPrompt("go");
    const ask = (requestId: string) =>
      mocks.fireEvent({
        type: "permission.asked",
        sessionId: "ses_child",
        requestId,
        action: "external_directory",
        resources: ["/repo/*"],
      });
    ask("per_a");
    ask("per_b");
    ask("per_c");
    expect(useRuntimeStore.getState().permissions).toHaveLength(3);
    await useRuntimeStore.getState().replyPermission("per_a", "once");
    expect(mocks.replyPermission).toHaveBeenCalledTimes(1);
    expect(mocks.replyPermission).toHaveBeenCalledWith("per_a", "once");
    expect(useRuntimeStore.getState().permissions.map((p) => p.requestId)).toEqual([
      "per_b",
      "per_c",
    ]);
  });

  it("does not batch distinct permission resources containing separator characters", async () => {
    const ask = (requestId: string, resources: string[]) =>
      mocks.fireEvent({
        type: "permission.asked",
        sessionId: "ses_collision",
        requestId,
        action: "external_directory",
        resources,
      });
    ask("per_left", ["a|b", "c"]);
    ask("per_right", ["a", "b|c"]);

    await useRuntimeStore.getState().replyPermission("per_left", "once");

    expect(mocks.replyPermission).toHaveBeenCalledTimes(1);
    expect(mocks.replyPermission).toHaveBeenCalledWith("per_left", "once");
    expect(useRuntimeStore.getState().permissions.map((p) => p.requestId)).toEqual(["per_right"]);
  });

  it("rejects persistent permission grants", async () => {
    await useRuntimeStore.getState().sendPrompt("go");
    mocks.fireEvent({
      type: "permission.asked",
      sessionId: "ses_new",
      requestId: "per_always",
      action: "bash",
      resources: ["git status"],
    });
    await useRuntimeStore.getState().replyPermission("per_always", "always");
    expect(mocks.replyPermission).not.toHaveBeenCalled();
    expect(useRuntimeStore.getState().permissions).toHaveLength(1);
  });
});

// A missed session.idle (SSE reconnect window, directory-scoped event stream)
// must not spin "Working…" forever: the store reconciles its running locks
// against the server's truth, and the user can always interrupt a turn.
describe("stale running locks and interrupt", () => {
  const doneHistory = [
    { role: "user", parts: [{ type: "text", text: "hi" }] },
    { role: "assistant", completed: 1783301200079, parts: [{ type: "text", text: "all done" }] },
  ];

  it("reconcileRunning clears a stale lock and reloads the missed history", async () => {
    await useRuntimeStore.getState().sendPrompt("hi");
    expect(useRuntimeStore.getState().runningSessions["ses_new"]).toBe(true);
    mocks.messages = doneHistory; // the turn ended server-side; idle was missed
    await useRuntimeStore.getState().reconcileRunning();
    const s = useRuntimeStore.getState();
    expect(s.runningSessions["ses_new"]).toBeUndefined();
    expect(
      s.threads["ses_new"].blocks.some((b) => b.kind === "agent" && b.markdown === "all done"),
    ).toBe(true);
  });

  it("reconcileRunning keeps the lock while the turn is genuinely running", async () => {
    await useRuntimeStore.getState().sendPrompt("hi");
    mocks.messages = [
      { role: "user", parts: [{ type: "text", text: "hi" }] },
      { role: "assistant", parts: [{ type: "text", text: "thinking…" }] }, // no `completed`
    ];
    await useRuntimeStore.getState().reconcileRunning();
    expect(useRuntimeStore.getState().runningSessions["ses_new"]).toBe(true);
  });

  it("fences a late old idle after history reconciliation unlocks a sync turn", async () => {
    const gate = deferred();
    mocks.commandWaits.push(gate.promise);
    mocks.suppressCommandEvents = true;
    useRuntimeStore.setState({
      currentId: "ses_sync_reconciled",
      threads: { ses_sync_reconciled: { loaded: true, index: {}, blocks: [] } },
    });
    const command = useRuntimeStore.getState().runCommand("init");
    await vi.waitFor(() =>
      expect(mocks.runCommand).toHaveBeenCalledWith(
        "ses_sync_reconciled",
        "init",
        undefined,
        undefined,
      ),
    );
    mocks.messages = [
      { role: "user", parts: [{ type: "text", text: "/init" }] },
      { role: "assistant", completed: 1, parts: [] },
    ];
    gate.resolve();
    await command;
    await useRuntimeStore.getState().reconcileRunning();
    expect(useRuntimeStore.getState().runningSessions.ses_sync_reconciled).toBeUndefined();

    await useRuntimeStore.getState().sendPrompt("replacement after reconcile");
    mocks.fireEvent({ type: "session.idle", sessionId: "ses_sync_reconciled" });
    expect(useRuntimeStore.getState().runningSessions.ses_sync_reconciled).toBe(true);

    mocks.fireEvent({
      type: "text.updated",
      sessionId: "ses_sync_reconciled",
      partId: "replacement-after-reconcile",
      text: "replacement answer",
    });
    mocks.fireEvent({ type: "session.idle", sessionId: "ses_sync_reconciled" });
    expect(useRuntimeStore.getState().runningSessions.ses_sync_reconciled).toBeUndefined();
  });

  it("connect() reconciles running locks left over from before the reconnect", async () => {
    await useRuntimeStore.getState().sendPrompt("hi");
    mocks.messages = doneHistory;
    await useRuntimeStore.getState().connect(); // e.g. a workspace switch
    await new Promise((r) => setTimeout(r, 10)); // reconcile runs behind connect
    expect(useRuntimeStore.getState().runningSessions["ses_new"]).toBeUndefined();
  });

  it("interrupt aborts the turn, unlocks the composer and marks the thread", async () => {
    await useRuntimeStore.getState().sendPrompt("hi");
    await useRuntimeStore.getState().interrupt();
    expect(mocks.abortSession).toHaveBeenCalledWith("ses_new");
    const s = useRuntimeStore.getState();
    expect(s.runningSessions["ses_new"]).toBeUndefined();
    expect(s.sending).toBe(false);
    expect(s.threads["ses_new"].blocks.slice(-1)[0]).toEqual({
      kind: "status-line",
      text: "Interrupted",
      tone: "error",
    });
  });

  it("does not clear another session's pending send when an interrupt settles", async () => {
    useRuntimeStore.setState({
      currentId: "ses_a",
      threads: {
        ses_a: { loaded: true, index: {}, blocks: [] },
        ses_b: { loaded: true, index: {}, blocks: [] },
      },
    });
    await useRuntimeStore.getState().sendPrompt("turn a");
    const abortGate = deferred();
    mocks.abortWaits.push(abortGate.promise);
    const interrupting = useRuntimeStore.getState().interrupt();
    await vi.waitFor(() => expect(mocks.abortSession).toHaveBeenCalledWith("ses_a"));

    useRuntimeStore.setState({ currentId: "ses_b" });
    const sendGate = deferred();
    mocks.sendWaits.push(sendGate.promise);
    const sendingB = useRuntimeStore.getState().sendPrompt("turn b");
    await vi.waitFor(() => expect(mocks.sendPrompt).toHaveBeenCalledWith("ses_b"));
    expect(useRuntimeStore.getState().sending).toBe(true);

    abortGate.resolve();
    await interrupting;
    expect(useRuntimeStore.getState().sending).toBe(true);

    sendGate.resolve();
    await expect(sendingB).resolves.toBe("ses_b");
    expect(useRuntimeStore.getState().sending).toBe(false);
    expect(useRuntimeStore.getState().runningSessions.ses_b).toBe(true);
  });

  it("does not let an interrupted sync success clear a replacement turn", async () => {
    const gate = deferred();
    mocks.commandWaits.push(gate.promise);
    const oldCommand = useRuntimeStore.getState().runCommand("init");
    await vi.waitFor(() => expect(mocks.runCommand).toHaveBeenCalled());

    await useRuntimeStore.getState().interrupt();
    await useRuntimeStore.getState().sendPrompt("replacement");
    gate.resolve(); // old command emits its idle, then its held-open POST resolves
    await oldCommand;

    expect(useRuntimeStore.getState().runningSessions.ses_new).toBe(true);
  });

  it("does not let an interrupted sync rejection clear or fail a replacement turn", async () => {
    const gate = deferred();
    mocks.commandWaits.push(gate.promise);
    mocks.failCommand = true;
    const oldCommand = useRuntimeStore.getState().runCommand("init");
    await vi.waitFor(() => expect(mocks.runCommand).toHaveBeenCalled());

    await useRuntimeStore.getState().interrupt();
    await useRuntimeStore.getState().sendPrompt("replacement");
    gate.resolve();
    await oldCommand;

    const state = useRuntimeStore.getState();
    expect(state.runningSessions.ses_new).toBe(true);
    expect(state.error).toBeNull();
    expect(state.threads.ses_new.blocks).not.toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "status-line", text: expect.stringContaining("Send failed") }),
      ]),
    );
  });

  it("the abort's own error/idle events add no noise after an interrupt", async () => {
    await useRuntimeStore.getState().sendPrompt("hi");
    await useRuntimeStore.getState().interrupt();
    const before = useRuntimeStore.getState().threads["ses_new"].blocks;
    mocks.fireEvent({ type: "error", sessionId: "ses_new", message: "The message was aborted" });
    mocks.fireEvent({ type: "session.idle", sessionId: "ses_new" });
    expect(useRuntimeStore.getState().threads["ses_new"].blocks).toEqual(before);
  });

  it("swallows the abort's trailing error and BOTH idle events (only 'Interrupted' shows)", async () => {
    // Regression: the abort's SSE burst (an "aborted" error + two session.idle
    // events) arrives DURING the abort POST's await. If the guard is set after
    // the await, or consumed by the first idle, the thread grows a stray
    // "Aborted" and one or two "done" lines before "Interrupted".
    await useRuntimeStore.getState().sendPrompt("hi");
    mocks.abortTrailing = [
      { type: "error", sessionId: "ses_new", message: "The message was aborted" },
      { type: "session.idle", sessionId: "ses_new" },
      { type: "session.idle", sessionId: "ses_new" },
    ];
    await useRuntimeStore.getState().interrupt();
    const statusLines = useRuntimeStore
      .getState()
      .threads["ses_new"].blocks.filter((b) => b.kind === "status-line");
    expect(statusLines).toEqual([{ kind: "status-line", text: "Interrupted", tone: "error" }]);
  });

  it("does not let an old interrupt settlement clear a newer same-session turn", async () => {
    await useRuntimeStore.getState().sendPrompt("hi");
    const gate = deferred();
    mocks.abortTrailing = [{ type: "session.idle", sessionId: "ses_new" }];
    mocks.abortWaits.push(gate.promise);

    const interrupting = useRuntimeStore.getState().interrupt();
    await vi.waitFor(() => expect(mocks.abortSession).toHaveBeenCalledWith("ses_new"));
    expect(useRuntimeStore.getState().runningSessions.ses_new).toBeUndefined();

    await expect(useRuntimeStore.getState().sendPrompt("new turn")).resolves.toBe("ses_new");
    expect(useRuntimeStore.getState().runningSessions.ses_new).toBe(true);
    gate.resolve();
    await interrupting;

    const state = useRuntimeStore.getState();
    expect(state.runningSessions.ses_new).toBe(true);
    expect(state.threads.ses_new.blocks).not.toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "status-line", text: "Interrupted" }),
      ]),
    );
  });

  it("fences abort terminal events that arrive after a replacement turn starts", async () => {
    await useRuntimeStore.getState().sendPrompt("hi");
    await useRuntimeStore.getState().interrupt();
    await useRuntimeStore.getState().sendPrompt("replacement turn");
    expect(useRuntimeStore.getState().runningSessions.ses_new).toBe(true);

    mocks.fireEvent({ type: "error", sessionId: "ses_new", message: "The message was aborted" });
    mocks.fireEvent({ type: "session.idle", sessionId: "ses_new" });
    mocks.fireEvent({ type: "session.idle", sessionId: "ses_new" });

    let state = useRuntimeStore.getState();
    expect(state.runningSessions.ses_new).toBe(true);
    expect(state.threads.ses_new.blocks.filter((b) => b.kind === "status-line")).toEqual([
      { kind: "status-line", text: "Interrupted", tone: "error" },
    ]);

    // Ordered activity proves subsequent terminal frames belong to the new
    // turn, so its own idle still unlocks and folds normally.
    mocks.fireEvent({
      type: "text.updated",
      sessionId: "ses_new",
      partId: "replacement-text",
      text: "new answer",
    });
    mocks.fireEvent({ type: "session.idle", sessionId: "ses_new" });
    state = useRuntimeStore.getState();
    expect(state.runningSessions.ses_new).toBeUndefined();
    expect(state.threads.ses_new.blocks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "agent", markdown: "new answer" }),
      ]),
    );
  });

  it("fences a second old idle split across replacement-turn startup", async () => {
    await useRuntimeStore.getState().sendPrompt("hi");
    mocks.abortTrailing = [{ type: "session.idle", sessionId: "ses_new" }];
    await useRuntimeStore.getState().interrupt();
    await useRuntimeStore.getState().sendPrompt("replacement turn");

    // The old server can emit idle twice. idle1 arrived during abort; idle2
    // arrives only after the replacement POST has already been accepted.
    mocks.fireEvent({ type: "session.idle", sessionId: "ses_new" });
    expect(useRuntimeStore.getState().runningSessions.ses_new).toBe(true);

    mocks.fireEvent({
      type: "text.updated",
      sessionId: "ses_new",
      partId: "replacement-after-split-idle",
      text: "replacement answer",
    });
    mocks.fireEvent({ type: "session.idle", sessionId: "ses_new" });
    expect(useRuntimeStore.getState().runningSessions.ses_new).toBeUndefined();
  });

  it("reconciles a replacement turn whose only event is idle", async () => {
    await useRuntimeStore.getState().sendPrompt("hi");
    await useRuntimeStore.getState().interrupt();
    await useRuntimeStore.getState().sendPrompt("silent replacement");
    mocks.messages = [
      { role: "user", parts: [{ type: "text", text: "silent replacement" }] },
      {
        role: "assistant",
        completed: Date.now() + 1_000,
        parts: [],
      },
    ];

    mocks.fireEvent({ type: "session.idle", sessionId: "ses_new" });

    await vi.waitFor(() =>
      expect(useRuntimeStore.getState().runningSessions.ses_new).toBeUndefined(),
    );
  });

  it("reconciles a replacement turn whose only event is an error", async () => {
    await useRuntimeStore.getState().sendPrompt("hi");
    await useRuntimeStore.getState().interrupt();
    await useRuntimeStore.getState().sendPrompt("failing replacement");

    mocks.messages = [
      { role: "user", parts: [{ type: "text", text: "failing replacement" }] },
      { role: "assistant", completed: Date.now(), parts: [] },
    ];
    mocks.fireEvent({ type: "error", sessionId: "ses_new", message: "Provider quota exceeded" });

    await vi.waitFor(() =>
      expect(useRuntimeStore.getState().runningSessions.ses_new).toBeUndefined(),
    );
  });

  it("a new turn after an interrupt folds its events normally again", async () => {
    await useRuntimeStore.getState().sendPrompt("hi");
    await useRuntimeStore.getState().interrupt();
    mocks.fireEvent({ type: "session.idle", sessionId: "ses_new" }); // suppressed; guard clears on the next turn
    await useRuntimeStore.getState().sendPrompt("again");
    mocks.fireEvent({
      type: "text.updated",
      sessionId: "ses_new",
      partId: "again-text",
      text: "normal answer",
    });
    mocks.fireEvent({ type: "session.idle", sessionId: "ses_new" });
    const s = useRuntimeStore.getState();
    expect(s.runningSessions["ses_new"]).toBeUndefined();
    expect(s.threads["ses_new"].blocks.slice(-1)[0]).toMatchObject({ kind: "status-line", tone: "done" });
  });

  it("interrupt does nothing when no turn is running", async () => {
    await useRuntimeStore.getState().interrupt();
    expect(mocks.abortSession).not.toHaveBeenCalled();
  });
});

// The right pane belongs to a session: each one keeps its own open artifact /
// Files browser and gets it back when reopened — never another session's.
describe("per-session right pane", () => {
  const artifact = (path: string): ArtifactBlock => ({
    kind: "artifact",
    path,
    filename: path.split("/").pop()!,
    artifact: "report",
    tool: "write",
  });

  it("remembers each session's pane and restores it on switch-back", () => {
    useRuntimeStore.setState({ currentId: "ses_1" });
    useRuntimeStore.getState().openArtifact(artifact("report.pdf"));
    // Session 2 has nothing open; session 1's pdf must not leak into it.
    useRuntimeStore.setState({ currentId: "ses_2" });
    expect(useRuntimeStore.getState().panes["ses_2"]).toBeUndefined();
    useRuntimeStore.getState().openArtifact(artifact("analysis.ipynb"));
    // Back to session 1: the pdf is there again, untouched.
    useRuntimeStore.setState({ currentId: "ses_1" });
    expect(useRuntimeStore.getState().panes["ses_1"]?.artifact?.path).toBe("report.pdf");
    expect(useRuntimeStore.getState().panes["ses_2"]?.artifact?.path).toBe("analysis.ipynb");
  });

  it("a closed pane stays closed after switching away and back", () => {
    useRuntimeStore.setState({ currentId: "ses_1" });
    useRuntimeStore.getState().openArtifact(artifact("report.pdf"));
    useRuntimeStore.getState().closeArtifact();
    useRuntimeStore.setState({ currentId: "ses_2" });
    useRuntimeStore.setState({ currentId: "ses_1" });
    expect(useRuntimeStore.getState().panes["ses_1"]?.artifact).toBe(null);
  });

  it("the artifact inspector, Files browser, and Runs pane are mutually exclusive", () => {
    useRuntimeStore.setState({ currentId: "ses_1" });
    useRuntimeStore.getState().openArtifact(artifact("report.pdf"));
    useRuntimeStore.getState().setShowFiles(true);
    expect(useRuntimeStore.getState().panes["ses_1"]).toEqual({ artifact: null, showFiles: true, showRuns: false });
    // Opening Runs closes Files; opening an artifact closes Runs.
    useRuntimeStore.getState().setShowRuns(true);
    expect(useRuntimeStore.getState().panes["ses_1"]).toEqual({ artifact: null, showFiles: false, showRuns: true });
    useRuntimeStore.getState().openArtifact(artifact("report.pdf"));
    const p = useRuntimeStore.getState().panes["ses_1"];
    expect(p?.showFiles).toBe(false);
    expect(p?.showRuns).toBe(false);
  });

  it("grafts the draft's pane onto the session created by the first message", async () => {
    useRuntimeStore.getState().openArtifact(artifact("notes.md"));
    expect(useRuntimeStore.getState().panes[DRAFT_KEY]?.artifact?.path).toBe("notes.md");
    await useRuntimeStore.getState().sendPrompt("hi");
    const s = useRuntimeStore.getState();
    expect(s.panes[DRAFT_KEY]).toBeUndefined();
    expect(s.panes["ses_new"]?.artifact?.path).toBe("notes.md");
  });

  it("startDraft resets the draft pane; session panes keep their memory", () => {
    useRuntimeStore.setState({ currentId: "ses_1" });
    useRuntimeStore.getState().openArtifact(artifact("report.pdf"));
    useRuntimeStore.setState({ currentId: null });
    useRuntimeStore.getState().openArtifact(artifact("stale.md"));
    useRuntimeStore.getState().startDraft();
    const s = useRuntimeStore.getState();
    expect(s.panes[DRAFT_KEY]).toBeUndefined();
    expect(s.panes["ses_1"]?.artifact?.path).toBe("report.pdf");
  });

  it("switchWorkspace drops the draft pane (old folder's files) but not session panes", async () => {
    useRuntimeStore.setState({ currentId: "ses_1" });
    useRuntimeStore.getState().openArtifact(artifact("report.pdf"));
    useRuntimeStore.setState({ currentId: null });
    useRuntimeStore.getState().openArtifact(artifact("old-folder.md"));
    await useRuntimeStore.getState().switchWorkspace({ path: "/ws/other" });
    const s = useRuntimeStore.getState();
    expect(s.panes[DRAFT_KEY]).toBeUndefined();
    expect(s.panes["ses_1"]?.artifact?.path).toBe("report.pdf");
  });

  it("deleteSession forgets the session's pane", async () => {
    useRuntimeStore.setState({ currentId: "ses_1" });
    useRuntimeStore.getState().openArtifact(artifact("report.pdf"));
    await useRuntimeStore.getState().deleteSession("ses_1");
    expect(useRuntimeStore.getState().panes["ses_1"]).toBeUndefined();
  });
});


describe("approval mode", () => {
  it("restores Full Access from the native config when connecting", async () => {
    expect(useRuntimeStore.getState().approvalMode).toBe("balanced");
    mocks.approvalMode = "full";
    await useRuntimeStore.getState().connect();
    expect(useRuntimeStore.getState().approvalMode).toBe("full");
  });

  it("persists Full Access through the native command and reconnects", async () => {
    await useRuntimeStore.getState().setApprovalMode("full");
    expect(mocks.setApprovalMode).toHaveBeenCalledWith("full");
    expect(useRuntimeStore.getState().approvalMode).toBe("full");
  });

  it("preserves a custom native permission policy without labelling it Manual", async () => {
    mocks.approvalMode = "custom";
    await useRuntimeStore.getState().connect();
    expect(useRuntimeStore.getState().approvalMode).toBe("custom");
  });

  it("falls back to Balanced when native config cannot be read", async () => {
    mocks.getApprovalMode.mockRejectedValueOnce(new Error("config unavailable"));
    await useRuntimeStore.getState().connect();
    expect(useRuntimeStore.getState().approvalMode).toBe("balanced");
  });

  it("rejects an unknown mode without persisting it", async () => {
    await expect(
      useRuntimeStore.getState().setApprovalMode("custom" as never),
    ).rejects.toThrow("report-only");
    expect(mocks.setApprovalMode).not.toHaveBeenCalled();
    expect(useRuntimeStore.getState().approvalMode).toBe("balanced");
    expect(useRuntimeStore.getState().error).toContain("report-only");
  });

  it("closes the old client and adopts the proxy restart's changed port", async () => {
    mocks.restartUrl = "http://127.0.0.1:43124";
    const before = mocks.clientOpts.length;

    await useRuntimeStore.getState().setProxySetting("none", "");

    expect(mocks.setProxySetting).toHaveBeenCalledWith("none", "");
    expect(mocks.mutationSawClosedClient).toBe(true);
    expect(useRuntimeStore.getState().serverUrl).toBe(mocks.restartUrl);
    expect(mocks.clientOpts.slice(before)).toHaveLength(1);
    expect(mocks.clientOpts[mocks.clientOpts.length - 1]).toMatchObject({
      baseUrl: mocks.restartUrl,
    });
    expect(useRuntimeStore.getState().status).toBe("ready");
  });

  it("terminates turns that were running when the sidecar restart began", async () => {
    useRuntimeStore.setState({
      currentId: "ses_running_at_restart",
      threads: { ses_running_at_restart: { loaded: true, index: {}, blocks: [] } },
    });
    await expect(useRuntimeStore.getState().sendPrompt("accepted before restart")).resolves.toBe(
      "ses_running_at_restart",
    );
    expect(useRuntimeStore.getState().runningSessions.ses_running_at_restart).toBe(true);

    await useRuntimeStore.getState().setProxySetting("none", "");

    const state = useRuntimeStore.getState();
    expect(state.sending).toBe(false);
    expect(state.runningSessions.ses_running_at_restart).toBeUndefined();
    expect(state.shellTurns.ses_running_at_restart).toBeUndefined();
  });

  it("does not overwrite a newer user-selected URL with a late mutation result", async () => {
    const gate = deferred();
    mocks.restartUrl = "http://127.0.0.1:43130";
    mocks.setProxySetting.mockImplementationOnce(async () => {
      await gate.promise;
      return { runtimeUrl: mocks.restartUrl };
    });

    const mutation = useRuntimeStore.getState().setProxySetting("none", "");
    await vi.waitFor(() => expect(mocks.setProxySetting).toHaveBeenCalledTimes(1));
    const queuedMutation = useRuntimeStore.getState().removeConfigEntry("mcp", "queued-old");
    const queuedModel = useRuntimeStore
      .getState()
      .setDefaultModel("anthropic/old-namespace-model");
    expect(mocks.removeConfigEntry).not.toHaveBeenCalled();
    const selectedUrl = "http://127.0.0.1:43202";
    useRuntimeStore.getState().setServerUrl(selectedUrl);
    gate.resolve();
    await Promise.all([mutation, queuedMutation, queuedModel]);

    const state = useRuntimeStore.getState();
    expect(state.serverUrl).toBe(selectedUrl);
    expect(state.status).toBe("offline");
    expect(mocks.clientOpen).toBe(false);
    expect(mocks.removeConfigEntry).toHaveBeenCalledWith("mcp", "queued-old");
    expect(mocks.setDefaultModelSpy).not.toHaveBeenCalled();
  });

  it("serializes a manual connect behind a runtime mutation handoff", async () => {
    const mutationGate = deferred();
    mocks.restartUrl = "http://127.0.0.1:43128";
    mocks.setProxySetting.mockImplementationOnce(async () => {
      mocks.mutationSawClosedClient = !mocks.clientOpen;
      await mutationGate.promise;
      return { runtimeUrl: mocks.restartUrl };
    });
    const before = mocks.clientOpts.length;

    const mutation = useRuntimeStore.getState().setProxySetting("none", "");
    await vi.waitFor(() => expect(mocks.setProxySetting).toHaveBeenCalledTimes(1));
    const manualConnect = useRuntimeStore.getState().connect();
    await Promise.resolve();
    expect(mocks.clientOpts).toHaveLength(before);

    mutationGate.resolve();
    await Promise.all([mutation, manualConnect]);
    expect(useRuntimeStore.getState().status).toBe("ready");
    expect(mocks.clientOpts.length - before).toBe(2);
    expect(mocks.clientOpts[mocks.clientOpts.length - 1]).toMatchObject({
      baseUrl: mocks.restartUrl,
    });
  });

  it("a loaded session chosen while a mutation has no client resumes in its own workspace", async () => {
    mocks.workspacePathValue = "/ws/a";
    await useRuntimeStore.getState().connect();
    useRuntimeStore.setState({
      currentId: "ses_a",
      sessions: [
        { id: "ses_a", title: "A", directory: "/ws/a" },
        { id: "ses_b_loaded", title: "B", directory: "/ws/b" },
      ] as never,
      threads: {
        ses_b_loaded: {
          loaded: true,
          index: {},
          blocks: [{ kind: "agent", markdown: "already loaded" }],
        },
      },
    });
    const mutationGate = deferred();
    const workspaceGate = deferred();
    mocks.setProxySetting.mockImplementationOnce(async () => {
      await mutationGate.promise;
      return { runtimeUrl: mocks.restartUrl };
    });
    mocks.setWorkspace.mockImplementationOnce(async (path: string) => {
      await workspaceGate.promise;
      mocks.workspacePathValue = path;
      return path;
    });

    const mutation = useRuntimeStore.getState().setProxySetting("none", "");
    await vi.waitFor(() => expect(mocks.setProxySetting).toHaveBeenCalledTimes(1));
    await useRuntimeStore.getState().openSession("ses_b_loaded");
    mutationGate.resolve();
    await mutation;
    await vi.waitFor(() => expect(mocks.setWorkspace).toHaveBeenCalledWith("/ws/b"));

    // The runtime mutation is complete, but its connect finalizer has started
    // a session-scoped workspace recovery that still owns the switching mask.
    expect(useRuntimeStore.getState().switching).toBe(true);
    await expect(useRuntimeStore.getState().sendPrompt("wrong workspace")).resolves.toBeNull();
    expect(mocks.sendPrompt).not.toHaveBeenCalled();

    workspaceGate.resolve();
    await vi.waitFor(() => expect(useRuntimeStore.getState().workspace).toBe("/ws/b"));
    await vi.waitFor(() => expect(useRuntimeStore.getState().switching).toBe(false));

    expect(useRuntimeStore.getState().currentId).toBe("ses_b_loaded");
    expect(mocks.clientOpts[mocks.clientOpts.length - 1]).toMatchObject({ directory: "/ws/b" });
    expect(useRuntimeStore.getState().status).toBe("ready");
  });

  it("disconnect wins over an in-flight committed mutation without losing its URL", async () => {
    const mutationGate = deferred();
    mocks.restartUrl = "http://127.0.0.1:43129";
    mocks.setProxySetting.mockImplementationOnce(async () => {
      mocks.mutationSawClosedClient = !mocks.clientOpen;
      await mutationGate.promise;
      return { runtimeUrl: mocks.restartUrl };
    });
    const before = mocks.clientOpts.length;

    const mutation = useRuntimeStore.getState().setProxySetting("none", "");
    await vi.waitFor(() => expect(mocks.setProxySetting).toHaveBeenCalledTimes(1));
    const queuedMutation = useRuntimeStore.getState().removeConfigEntry("mcp", "queued");
    expect(mocks.removeConfigEntry).not.toHaveBeenCalled();
    useRuntimeStore.getState().disconnect();
    // The gated mutation is still pending, but disconnect must make the UI
    // offline immediately instead of leaving the composer enabled via switching.
    expect(useRuntimeStore.getState().status).toBe("offline");
    expect(useRuntimeStore.getState().switching).toBe(false);
    mutationGate.resolve();

    await expect(Promise.all([mutation, queuedMutation])).resolves.toEqual([undefined, undefined]);
    const state = useRuntimeStore.getState();
    expect(state.status).toBe("offline");
    expect(state.serverUrl).toBe(mocks.restartUrl);
    expect(state.error).toBe(null);
    expect(state.switching).toBe(false);
    expect(mocks.clientOpts).toHaveLength(before);
    expect(mocks.removeConfigEntry).toHaveBeenCalledWith("mcp", "queued");
  });

  it("reports retry exhaustion as a committed handoff failure, not a rollback", async () => {
    vi.useFakeTimers();
    mocks.failConnects = 999;
    try {
      const mutation = useRuntimeStore.getState().setProxySetting("none", "");
      const outcome = mutation.then(
        () => null,
        (error: unknown) => error,
      );
      await vi.runAllTimersAsync();
      const error = await outcome;

      expect(error).toBeInstanceOf(Error);
      expect((error as Error).message).toMatch(/was saved.*could not reconnect/i);
      expect(mocks.setProxySetting).toHaveBeenCalledTimes(1);
      expect(useRuntimeStore.getState().error).toMatch(/Use Connect to retry.*not rolled back/i);
      expect(useRuntimeStore.getState().status).toBe("error");
      expect(useRuntimeStore.getState().switching).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("serializes the model mutation behind a sidecar-restarting mutation", async () => {
    const mutationGate = deferred();
    mocks.setProxySetting.mockImplementationOnce(async () => {
      mocks.mutationSawClosedClient = !mocks.clientOpen;
      await mutationGate.promise;
      return { runtimeUrl: mocks.restartUrl };
    });

    const proxy = useRuntimeStore.getState().setProxySetting("none", "");
    await vi.waitFor(() => expect(mocks.setProxySetting).toHaveBeenCalledTimes(1));
    const model = useRuntimeStore.getState().setDefaultModel("anthropic/claude-sonnet-5");
    await Promise.resolve();
    expect(mocks.setDefaultModelSpy).not.toHaveBeenCalled();

    mutationGate.resolve();
    await Promise.all([proxy, model]);
    expect(mocks.setDefaultModelSpy).toHaveBeenCalledWith("anthropic/claude-sonnet-5");
    expect(useRuntimeStore.getState().status).toBe("ready");
  });

  it("file-backed removal and login import use the same safe restart handoff", async () => {
    mocks.restartUrl = "http://127.0.0.1:43125";
    await useRuntimeStore.getState().removeConfigEntry("mcp", "demo");
    expect(mocks.removeConfigEntry).toHaveBeenCalledWith("mcp", "demo");
    expect(mocks.mutationSawClosedClient).toBe(true);
    expect(mocks.clientOpts[mocks.clientOpts.length - 1]).toMatchObject({
      baseUrl: mocks.restartUrl,
    });

    mocks.restartUrl = "http://127.0.0.1:43126";
    expect(await useRuntimeStore.getState().importOpenCodeLogin()).toBe(true);
    expect(mocks.mutationSawClosedClient).toBe(true);
    expect(mocks.clientOpts[mocks.clientOpts.length - 1]).toMatchObject({
      baseUrl: mocks.restartUrl,
    });
  });

  it("provider keychain mutations use the same safe restart handoff", async () => {
    mocks.restartUrl = "http://127.0.0.1:43131";
    await useRuntimeStore.getState().saveProviderApiKey("anthropic", "sk-test");
    expect(mocks.saveProviderApiKey).toHaveBeenCalledWith("anthropic", "sk-test");
    expect(mocks.mutationSawClosedClient).toBe(true);

    mocks.restartUrl = "http://127.0.0.1:43132";
    await useRuntimeStore.getState().removeProviderApiKey("custom-lab", true);
    expect(mocks.removeProviderApiKey).toHaveBeenCalledWith("custom-lab", true);
    expect(useRuntimeStore.getState().serverUrl).toBe(mocks.restartUrl);

    mocks.restartUrl = "http://127.0.0.1:43133";
    await useRuntimeStore.getState().finalizeProviderLogin("openrouter");
    expect(mocks.finalizeProviderLogin).toHaveBeenCalledWith("openrouter");
    expect(useRuntimeStore.getState().serverUrl).toBe(mocks.restartUrl);
    expect(useRuntimeStore.getState().status).toBe("ready");
  });

  it("curated connector keychain mutations use the safe restart handoff", async () => {
    mocks.restartUrl = "http://127.0.0.1:43134";
    await useRuntimeStore.getState().saveScienceConnectorApiKey("fred", "fred-secret");
    expect(mocks.saveScienceConnectorApiKey).toHaveBeenCalledWith("fred", "fred-secret");
    expect(mocks.mutationSawClosedClient).toBe(true);
    expect(useRuntimeStore.getState().serverUrl).toBe(mocks.restartUrl);

    mocks.restartUrl = "http://127.0.0.1:43135";
    await useRuntimeStore.getState().removeScienceConnector("fred");
    expect(mocks.removeScienceConnector).toHaveBeenCalledWith("fred");
    expect(useRuntimeStore.getState().serverUrl).toBe(mocks.restartUrl);
    expect(useRuntimeStore.getState().status).toBe("ready");
  });

  it("restores an authoritative connection before surfacing a mutation failure", async () => {
    mocks.startRuntimeUrl = "http://127.0.0.1:43127";
    mocks.setProxySetting.mockImplementationOnce(async () => {
      mocks.mutationSawClosedClient = !mocks.clientOpen;
      throw new Error("config rejected");
    });

    await expect(useRuntimeStore.getState().setProxySetting("none", "")).rejects.toThrow(
      "config rejected",
    );

    expect(mocks.mutationSawClosedClient).toBe(true);
    expect(useRuntimeStore.getState().serverUrl).toBe(mocks.startRuntimeUrl);
    expect(mocks.clientOpts[mocks.clientOpts.length - 1]).toMatchObject({
      baseUrl: mocks.startRuntimeUrl,
    });
    expect(useRuntimeStore.getState().status).toBe("ready");
    expect(useRuntimeStore.getState().switching).toBe(false);
  });

  it("shows an error state when both a mutation and runtime recovery fail", async () => {
    mocks.setProxySetting.mockImplementationOnce(async () => {
      mocks.mutationSawClosedClient = !mocks.clientOpen;
      throw new Error("config rejected");
    });
    mocks.startRuntime.mockRejectedValueOnce(new Error("runtime restart failed"));

    await expect(useRuntimeStore.getState().setProxySetting("none", "")).rejects.toThrow(
      "config rejected",
    );

    const state = useRuntimeStore.getState();
    expect(mocks.mutationSawClosedClient).toBe(true);
    expect(mocks.clientOpen).toBe(false);
    expect(state.status).toBe("error");
    expect(state.error).toMatch(/could not restore the runtime connection.*runtime restart failed/i);
    expect(state.switching).toBe(false);
  });

  it("setDefaultModel applies the model and reconnects seamlessly (no manual Connect)", async () => {
    const before = mocks.clientOpts.length;
    await useRuntimeStore.getState().setDefaultModel("anthropic/claude-sonnet-5");
    expect(mocks.setDefaultModelSpy).toHaveBeenCalledWith("anthropic/claude-sonnet-5");
    // A fresh client/event stream replaces the one the config change closed —
    // exactly one reconnect, so switching models never strands the app offline.
    expect(mocks.clientOpts.length - before).toBe(1);
    const s = useRuntimeStore.getState();
    expect(s.status).toBe("ready");
    expect(s.switching).toBe(false);
    expect(s.defaultModel).toBe("anthropic/claude-sonnet-5");
  });

  it("setDefaultModel masks the reconnect with `switching` (no disconnect flash)", async () => {
    const p = useRuntimeStore.getState().setDefaultModel("anthropic/claude-sonnet-5");
    expect(useRuntimeStore.getState().switching).toBe(true);
    await p;
    expect(useRuntimeStore.getState().switching).toBe(false);
    expect(useRuntimeStore.getState().status).toBe("ready");
  });
});
