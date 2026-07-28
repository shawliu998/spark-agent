import { beforeEach, describe, expect, it, vi } from "vitest";

const runtime = vi.hoisted(() => ({
  tauri: true,
  statuses: [] as Array<{
    state: string;
    endpoint: string | null;
    dockerReady: boolean;
    composeReady: boolean;
    message: string | null;
  }>,
  connections: [] as Array<{ endpoint: string; token: string }>,
  status: vi.fn(),
  connection: vi.fn(),
  retry: vi.fn(),
  clients: [] as Array<{ baseUrl?: string; token?: string }>,
  calls: [] as string[],
}));

vi.mock("@/lib/tauri", () => ({
  get isTauri() {
    return runtime.tauri;
  },
  scienceCoreStatus: runtime.status,
  scienceCoreConnection: runtime.connection,
  retryScienceCore: runtime.retry,
}));

vi.mock("@spark/research-sdk", () => ({
  ScienceCoreClient: class {
    private readonly options: { baseUrl?: string; token?: string };

    constructor(options: { baseUrl?: string; token?: string }) {
      this.options = options;
      runtime.clients.push(options);
    }

    async health() {
      runtime.calls.push(this.options.baseUrl ?? "missing");
      return { endpoint: this.options.baseUrl };
    }

    async listProjects() {
      return [{ endpoint: this.options.baseUrl }];
    }
  },
}));

function status(
  state: "starting" | "ready" | "failed" | "stopped" | "unavailable",
  message: string | null = null,
) {
  return {
    state,
    endpoint: state === "ready" ? "http://127.0.0.1:49152" : null,
    dockerReady: state === "ready",
    composeReady: state === "ready",
    message,
  };
}

describe("Science Core runtime handoff", () => {
  beforeEach(() => {
    vi.useRealTimers();
    vi.resetModules();
    vi.unstubAllEnvs();
    vi.clearAllMocks();
    runtime.tauri = true;
    runtime.statuses = [];
    runtime.connections = [];
    runtime.clients = [];
    runtime.calls = [];
    runtime.status.mockImplementation(async () => runtime.statuses.shift());
    runtime.connection.mockImplementation(async () => runtime.connections.shift());
    runtime.retry.mockResolvedValue(status("starting"));
  });

  it("waits from starting to ready and shares one connection/client across concurrent calls", async () => {
    vi.useFakeTimers();
    runtime.statuses.push(status("starting"), status("ready"));
    runtime.connections.push({
      endpoint: "http://127.0.0.1:49152",
      token: "a".repeat(64),
    });
    const { scienceCore } = await import("./scienceCore");

    const health = scienceCore.health();
    const projects = scienceCore.listProjects();
    await vi.advanceTimersByTimeAsync(250);

    await expect(health).resolves.toEqual({ endpoint: "http://127.0.0.1:49152" });
    await expect(projects).resolves.toEqual([
      { endpoint: "http://127.0.0.1:49152" },
    ]);
    expect(runtime.connection).toHaveBeenCalledTimes(1);
    expect(runtime.clients).toHaveLength(1);
  });

  it("propagates failed runtime truth without requesting or exposing a token", async () => {
    runtime.statuses.push(status("failed", "Docker Desktop is not available"));
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const { scienceCore } = await import("./scienceCore");

    await expect(scienceCore.health()).rejects.toThrow("Docker Desktop is not available");
    expect(runtime.connection).not.toHaveBeenCalled();
    expect(storage).not.toHaveBeenCalled();
  });

  it.each([
    ["unavailable", "Packaged offline runtime resources were not found"],
    ["stopped", "Science Core was stopped by the user"],
  ] as const)("propagates the %s runtime reason without claiming readiness", async (state, reason) => {
    runtime.statuses.push(status(state, reason));
    const { scienceCore } = await import("./scienceCore");

    await expect(scienceCore.health()).rejects.toThrow(reason);
    expect(runtime.connection).not.toHaveBeenCalled();
    expect(runtime.clients).toHaveLength(0);
  });

  it("invalidates the old client on retry and adopts the new dynamic endpoint", async () => {
    runtime.statuses.push(status("ready"));
    runtime.connections.push({
      endpoint: "http://127.0.0.1:49152",
      token: "a".repeat(64),
    });
    const { retryScienceCoreRuntime, scienceCore } = await import("./scienceCore");
    await expect(scienceCore.health()).resolves.toEqual({
      endpoint: "http://127.0.0.1:49152",
    });

    await retryScienceCoreRuntime();
    runtime.statuses.push(status("ready"));
    runtime.connections.push({
      endpoint: "http://127.0.0.1:49153",
      token: "b".repeat(64),
    });
    await expect(scienceCore.health()).resolves.toEqual({
      endpoint: "http://127.0.0.1:49153",
    });
    expect(runtime.clients.map((client) => client.baseUrl)).toEqual([
      "http://127.0.0.1:49152",
      "http://127.0.0.1:49153",
    ]);
  });

  it("never invokes a client resolved in the microtask immediately before retry", async () => {
    runtime.statuses.push(status("ready"));
    let resolveOldConnection!: (connection: { endpoint: string; token: string }) => void;
    runtime.connection.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveOldConnection = resolve;
      }),
    );
    const { retryScienceCoreRuntime, scienceCore } = await import("./scienceCore");
    const request = scienceCore.health();
    await vi.waitFor(() => expect(runtime.connection).toHaveBeenCalledTimes(1));

    resolveOldConnection({
      endpoint: "http://127.0.0.1:49152",
      token: "a".repeat(64),
    });
    const retry = retryScienceCoreRuntime();
    runtime.statuses.push(status("ready"));
    runtime.connections.push({
      endpoint: "http://127.0.0.1:49153",
      token: "b".repeat(64),
    });
    await retry;

    await expect(request).resolves.toEqual({ endpoint: "http://127.0.0.1:49153" });
    expect(runtime.calls).toEqual(["http://127.0.0.1:49153"]);
  });

  it("coalesces concurrent runtime retries into one IPC call", async () => {
    let resolveRetry!: (value: ReturnType<typeof status>) => void;
    runtime.retry.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveRetry = resolve;
      }),
    );
    const { retryScienceCoreRuntime } = await import("./scienceCore");

    const first = retryScienceCoreRuntime();
    const second = retryScienceCoreRuntime();
    expect(runtime.retry).toHaveBeenCalledTimes(1);
    resolveRetry(status("starting"));
    await expect(Promise.all([first, second])).resolves.toEqual([
      status("starting"),
      status("starting"),
    ]);
  });

  it("times out a hung retry, clears single-flight state, and allows a new IPC", async () => {
    vi.useFakeTimers();
    runtime.retry.mockReturnValueOnce(new Promise(() => {}));
    const { retryScienceCoreRuntime } = await import("./scienceCore");

    const first = retryScienceCoreRuntime();
    const rejection = expect(first).rejects.toMatchObject({
      name: "ScienceCoreRuntimeError",
      state: "timeout",
      message: expect.stringContaining("startup timed out"),
    });
    await vi.advanceTimersByTimeAsync(240_000);
    await rejection;
    expect(runtime.retry).toHaveBeenCalledTimes(1);

    runtime.retry.mockResolvedValueOnce(status("starting"));
    await expect(retryScienceCoreRuntime()).resolves.toEqual(status("starting"));
    expect(runtime.retry).toHaveBeenCalledTimes(2);
  });

  it("redacts an invalid connection payload from errors", async () => {
    const secret = "do-not-render-this-secret";
    runtime.statuses.push(status("ready"));
    runtime.connections.push({
      endpoint: "https://remote.example.test",
      token: secret,
    });
    const { scienceCore } = await import("./scienceCore");

    let error = "";
    try {
      await scienceCore.health();
    } catch (caught) {
      error = caught instanceof Error ? caught.message : String(caught);
    }
    expect(error).toContain("invalid local connection");
    expect(error).not.toContain(secret);
  });

  it.each([
    "http://127.0.0.1:0",
    "http://127.0.0.1:65536",
  ])("rejects the out-of-range packaged endpoint %s", async (endpoint) => {
    runtime.statuses.push(status("ready"));
    runtime.connections.push({ endpoint, token: "a".repeat(64) });
    const { scienceCore } = await import("./scienceCore");

    await expect(scienceCore.health()).rejects.toThrow("invalid local connection");
    expect(runtime.calls).toHaveLength(0);
  });

  it.each(["status", "connection"] as const)(
    "enforces the hard startup deadline around a hung %s IPC",
    async (stage) => {
      vi.useFakeTimers();
      if (stage === "status") {
        runtime.status.mockReturnValueOnce(new Promise(() => {}));
      } else {
        runtime.statuses.push(status("ready"));
        runtime.connection.mockReturnValueOnce(new Promise(() => {}));
      }
      const { scienceCore } = await import("./scienceCore");
      const request = scienceCore.health();
      const rejection = expect(request).rejects.toThrow("startup timed out");
      await vi.advanceTimersByTimeAsync(240_000);

      await rejection;
      expect(runtime.calls).toHaveLength(0);
    },
  );

  it("keeps browser development on the explicit env configuration", async () => {
    runtime.tauri = false;
    vi.stubEnv("VITE_SCIENCE_CORE_URL", "http://127.0.0.1:8765");
    vi.stubEnv("VITE_SCIENCE_CORE_TOKEN", "browser-development-token");
    const { scienceCore, scienceCoreConfigurationError } = await import("./scienceCore");

    expect(scienceCoreConfigurationError).toBeNull();
    await expect(scienceCore.health()).resolves.toEqual({
      endpoint: "http://127.0.0.1:8765",
    });
    expect(runtime.status).not.toHaveBeenCalled();
    expect(runtime.clients[0]).toEqual({
      baseUrl: "http://127.0.0.1:8765",
      token: "browser-development-token",
    });
  });
});
