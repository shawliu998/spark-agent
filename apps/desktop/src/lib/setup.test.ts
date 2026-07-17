// The setup store owns the long-running uv provisioning flows so they survive
// page navigation. These guard the two properties that broke before: a second
// concurrent start must not race the first into the same env dir, and the
// busy/generation lifecycle must be observable regardless of which page reads.
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  addMcpServer: vi.fn(async (_name: string, _config?: unknown) => {}),
  listMcpServers: vi.fn(async () => [] as Array<{ name: string }>),
  loadCatalog: vi.fn(async () => {}),
  saveScienceConnectorApiKey: vi.fn(async (_connectorId: string, _apiKey: string) => {}),
  /** Resolver for the in-flight setupJupyter promise, so tests hold it open. */
  resolveSetup: (() => {}) as () => void,
  setupJupyter: vi.fn(),
  setupScienceMcp: vi.fn(async () => "/env/bin/python"),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
  client: null as null | {
    addMcpServer: ReturnType<typeof vi.fn>;
    listMcpServers: ReturnType<typeof vi.fn>;
  },
}));

mocks.setupJupyter.mockImplementation(
  () => new Promise<void>((r) => (mocks.resolveSetup = () => r())),
);

vi.mock("./runtime", () => ({
  getClient: () => mocks.client,
  useRuntimeStore: {
    getState: () => ({
      loadCatalog: mocks.loadCatalog,
      saveScienceConnectorApiKey: mocks.saveScienceConnectorApiKey,
    }),
  },
}));
vi.mock("./tauri", () => ({
  setupJupyter: mocks.setupJupyter,
  startJupyter: async () => ({
    installed: true,
    running: true,
    registered: false,
  }),
  setupScienceMcp: mocks.setupScienceMcp,
  watchSetupProgress: async () => () => {},
}));
vi.mock("./scienceConnectors", () => ({
  SCIENCE_CONNECTORS: [
    { id: "papers", label: "Papers" },
    {
      id: "fred",
      label: "FRED",
      apiKeyEnv: "FRED_API_KEY",
    },
    {
      id: "materials-project",
      label: "Materials Project",
      apiKeyEnv: "MP_API_KEY",
      securityGated: true,
    },
  ],
  connectorConfig: (c: { id: string }, python: string) => ({
    type: "local",
    command: [python, c.id],
    enabled: true,
  }),
}));
vi.mock("./toast", () => ({
  toast: { success: mocks.toastSuccess, error: mocks.toastError },
}));

import { ensureScienceConnector, useSetupStore } from "./setup";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listMcpServers.mockResolvedValue([]);
  mocks.addMcpServer.mockImplementation(async (name: string) => {
    mocks.listMcpServers.mockResolvedValue([{ name }]);
  });
  mocks.setupScienceMcp.mockResolvedValue("/env/bin/python");
  mocks.client = {
    addMcpServer: mocks.addMcpServer,
    listMcpServers: mocks.listMcpServers,
  };
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
    expect(mocks.addMcpServer).not.toHaveBeenCalled();
    expect(mocks.toastSuccess).toHaveBeenCalledWith(
      expect.stringMatching(/Local JupyterLab is ready.*Agent MCP access remains security-gated/i),
    );
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

  it("finishes local Jupyter setup even when the runtime endpoint changes", async () => {
    const run = useSetupStore.getState().enableJupyter();
    const replacementAdd = vi.fn(async () => {});
    mocks.client = { addMcpServer: replacementAdd, listMcpServers: mocks.listMcpServers };
    mocks.resolveSetup();

    await run;

    expect(mocks.addMcpServer).not.toHaveBeenCalled();
    expect(replacementAdd).not.toHaveBeenCalled();
    expect(useSetupStore.getState().jupyterBusy).toBe(false);
    expect(mocks.toastSuccess).toHaveBeenCalledWith(
      expect.stringMatching(/Local JupyterLab is ready/i),
    );
  });

  it("does not require a connected runtime for local Jupyter setup", async () => {
    mocks.client = null;
    const run = useSetupStore.getState().enableJupyter();
    mocks.resolveSetup();
    await run;

    expect(mocks.setupJupyter).toHaveBeenCalledTimes(1);
    expect(mocks.toastSuccess).toHaveBeenCalledWith(
      expect.stringMatching(/Local JupyterLab is ready/i),
    );
    expect(mocks.toastError).not.toHaveBeenCalled();
  });

  it("tracks the connector being provisioned and clears it when done", async () => {
    const run = useSetupStore.getState().enableConnector("papers", "key123");
    expect(useSetupStore.getState().connectorId).toBe("papers");
    await run;
    expect(useSetupStore.getState().connectorId).toBeNull();
    expect(mocks.setupScienceMcp).toHaveBeenCalledWith("papers");
    expect(mocks.addMcpServer).toHaveBeenCalledWith("papers", expect.anything());
  });

  it("skips provisioning when the requested connector is already configured", async () => {
    mocks.listMcpServers.mockResolvedValue([{ name: "papers" }]);

    await ensureScienceConnector("papers");

    expect(mocks.setupScienceMcp).not.toHaveBeenCalled();
    expect(mocks.addMcpServer).not.toHaveBeenCalled();
  });

  it("provisions and verifies a missing connector", async () => {
    await ensureScienceConnector("papers");

    expect(mocks.setupScienceMcp).toHaveBeenCalledWith("papers");
    expect(mocks.addMcpServer).toHaveBeenCalledTimes(1);
    expect(mocks.listMcpServers).toHaveBeenCalledTimes(2);
  });

  it("throws and stays fail-closed when connector setup fails", async () => {
    mocks.setupScienceMcp.mockRejectedValueOnce(new Error("install failed"));

    await expect(ensureScienceConnector("papers")).rejects.toThrow("setup did not complete");
    expect(mocks.addMcpServer).not.toHaveBeenCalled();
  });

  it("sends keyed connectors only through the native credential transaction", async () => {
    await useSetupStore.getState().enableConnector("fred", "  fred-secret  ");

    expect(mocks.addMcpServer).not.toHaveBeenCalled();
    expect(mocks.setupScienceMcp).toHaveBeenCalledWith("fred");
    expect(mocks.saveScienceConnectorApiKey).toHaveBeenCalledWith("fred", "fred-secret");
    expect(mocks.saveScienceConnectorApiKey.mock.calls[0]).toHaveLength(2);
  });

  it("fails closed before provisioning a security-gated keyed connector", async () => {
    await useSetupStore
      .getState()
      .enableConnector("materials-project", "materials-secret");

    expect(mocks.setupScienceMcp).not.toHaveBeenCalled();
    expect(mocks.saveScienceConnectorApiKey).not.toHaveBeenCalled();
    expect(mocks.addMcpServer).not.toHaveBeenCalled();
  });

  it("rejects a missing keyed-connector secret before provisioning", async () => {
    await useSetupStore.getState().enableConnector("fred", "   ");

    expect(mocks.setupScienceMcp).not.toHaveBeenCalled();
    expect(mocks.saveScienceConnectorApiKey).not.toHaveBeenCalled();
  });

  it("does not register a provisioned connector on a replacement endpoint", async () => {
    const run = useSetupStore.getState().enableConnector("papers", "key123");
    mocks.client = {
      addMcpServer: vi.fn(async () => {}),
      listMcpServers: mocks.listMcpServers,
    };

    await run;

    expect(mocks.addMcpServer).not.toHaveBeenCalled();
    expect(mocks.client!.addMcpServer).not.toHaveBeenCalled();
    expect(useSetupStore.getState().connectorId).toBeNull();
  });

  it("does not expose a keyed connector secret after the endpoint is replaced", async () => {
    const run = useSetupStore.getState().enableConnector("fred", "fred-secret");
    mocks.client = {
      addMcpServer: vi.fn(async () => {}),
      listMcpServers: mocks.listMcpServers,
    };

    await run;

    expect(mocks.saveScienceConnectorApiKey).not.toHaveBeenCalled();
    expect(mocks.addMcpServer).not.toHaveBeenCalled();
  });
});
