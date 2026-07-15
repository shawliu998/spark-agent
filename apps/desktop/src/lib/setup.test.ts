// The setup store owns the long-running uv provisioning flows so they survive
// page navigation. These guard the two properties that broke before: a second
// concurrent start must not race the first into the same env dir, and the
// busy/generation lifecycle must be observable regardless of which page reads.
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  addMcpServer: vi.fn(async () => {}),
  loadCatalog: vi.fn(async () => {}),
  /** Resolver for the in-flight setupJupyter promise, so tests hold it open. */
  resolveSetup: (() => {}) as () => void,
  setupJupyter: vi.fn(),
  setupScienceMcp: vi.fn(async () => "/env/bin/python"),
  client: null as null | { addMcpServer: ReturnType<typeof vi.fn> },
}));

mocks.setupJupyter.mockImplementation(
  () => new Promise<void>((r) => (mocks.resolveSetup = () => r())),
);

vi.mock("./runtime", () => ({
  getClient: () => mocks.client,
  useRuntimeStore: { getState: () => ({ loadCatalog: mocks.loadCatalog }) },
}));
vi.mock("./tauri", () => ({
  setupJupyter: mocks.setupJupyter,
  startJupyter: async () => ({
    url: "http://127.0.0.1:9",
    token: "tok",
    mcp_command: "/env/bin/jupyter-mcp-server",
  }),
  setupScienceMcp: mocks.setupScienceMcp,
  watchSetupProgress: async () => () => {},
}));
vi.mock("./scienceConnectors", () => ({
  SCIENCE_CONNECTORS: [
    { id: "papers", label: "Papers", pkg: "paper-search-mcp" },
  ],
  connectorConfig: () => ({ type: "local", command: ["/env/bin/python"], enabled: true }),
}));
vi.mock("./toast", () => ({ toast: { success: () => {}, error: () => {} } }));

import { useSetupStore } from "./setup";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.client = { addMcpServer: mocks.addMcpServer };
  mocks.setupJupyter.mockImplementation(
    () => new Promise<void>((r) => (mocks.resolveSetup = () => r())),
  );
  useSetupStore.setState({ jupyterBusy: false, connectorId: null, line: null, generation: 0 });
});

describe("setup store", () => {
  it("marks busy while provisioning Jupyter and clears + bumps generation after", async () => {
    const gen0 = useSetupStore.getState().generation;
    const run = useSetupStore.getState().enableJupyter();
    expect(useSetupStore.getState().jupyterBusy).toBe(true); // set synchronously

    mocks.resolveSetup();
    await run;

    const s = useSetupStore.getState();
    expect(s.jupyterBusy).toBe(false);
    expect(s.line).toBeNull();
    expect(s.generation).toBe(gen0 + 1);
    expect(mocks.addMcpServer).toHaveBeenCalledWith("jupyter", expect.anything());
  });

  it("ignores a second concurrent enableJupyter — no colliding provisioning run", async () => {
    const p1 = useSetupStore.getState().enableJupyter();
    const p2 = useSetupStore.getState().enableJupyter(); // guarded: returns at once
    await p2; // the guarded call resolves without waiting on the first
    expect(mocks.setupJupyter).toHaveBeenCalledTimes(1);

    mocks.resolveSetup();
    await p1;
    expect(mocks.setupJupyter).toHaveBeenCalledTimes(1);
  });

  it("does not expose Jupyter credentials to a replacement endpoint", async () => {
    const run = useSetupStore.getState().enableJupyter();
    const replacementAdd = vi.fn(async () => {});
    mocks.client = { addMcpServer: replacementAdd };
    mocks.resolveSetup();

    await run;

    expect(mocks.addMcpServer).not.toHaveBeenCalled();
    expect(replacementAdd).not.toHaveBeenCalled();
    expect(useSetupStore.getState().jupyterBusy).toBe(false);
  });

  it("tracks the connector being provisioned and clears it when done", async () => {
    const run = useSetupStore.getState().enableConnector("papers", "key123");
    expect(useSetupStore.getState().connectorId).toBe("papers");
    await run;
    expect(useSetupStore.getState().connectorId).toBeNull();
    expect(mocks.addMcpServer).toHaveBeenCalledWith("papers", expect.anything());
  });

  it("does not register a provisioned connector on a replacement endpoint", async () => {
    const run = useSetupStore.getState().enableConnector("papers", "key123");
    mocks.client = { addMcpServer: vi.fn(async () => {}) };

    await run;

    expect(mocks.addMcpServer).not.toHaveBeenCalled();
    expect(mocks.client!.addMcpServer).not.toHaveBeenCalled();
    expect(useSetupStore.getState().connectorId).toBeNull();
  });
});
