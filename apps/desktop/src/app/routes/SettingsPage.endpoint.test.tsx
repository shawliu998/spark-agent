import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

interface FakeClient {
  listProviders: ReturnType<typeof vi.fn>;
  listAuthMethods: ReturnType<typeof vi.fn>;
  listProviderCatalog: ReturnType<typeof vi.fn>;
  listCustomProviderIds: ReturnType<typeof vi.fn>;
  listMcpServers: ReturnType<typeof vi.fn>;
}

const mocks = vi.hoisted(() => ({
  client: null as FakeClient | null,
  runtimeStore: null as null | {
    setState: (partial: Record<string, unknown>) => void;
  },
}));

vi.mock("@/lib/runtime", async () => {
  const { create } = await import("zustand");
  const useRuntimeStore = create(() => ({
    status: "offline",
    serverUrl: "http://127.0.0.1:41001",
    setServerUrl: vi.fn(),
    connect: vi.fn(async () => {}),
    disconnect: vi.fn(),
    defaultModel: null,
    loadCatalog: vi.fn(async () => {}),
    removeConfigEntry: vi.fn(async () => {}),
    importOpenCodeLogin: vi.fn(async () => false),
  }));
  mocks.runtimeStore = useRuntimeStore;
  return {
    getClient: () => mocks.client,
    useRuntimeStore,
  };
});

vi.mock("@/lib/tauri", () => ({
  isTauri: false,
  jupyterStatus: vi.fn(async () => null),
  openExternal: vi.fn(async () => {}),
  openWorkspaceBase: vi.fn(async () => {}),
  pickFolder: vi.fn(async () => null),
  pythonInterpreter: vi.fn(async () => null),
  setPythonPath: vi.fn(async () => {}),
  setWorkspaceBase: vi.fn(async (path: string) => path),
  workspaceBase: vi.fn(async () => null),
  getProxySetting: vi.fn(async () => null),
  setupJupyter: vi.fn(async () => {}),
  startJupyter: vi.fn(async () => null),
  setupScienceMcp: vi.fn(async () => ""),
  watchSetupProgress: vi.fn(async () => () => {}),
}));

import { SettingsPage } from "./SettingsPage";

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function fakeClient(
  providers: Promise<Array<{ id: string; name: string; models: never[] }>>,
): FakeClient {
  return {
    listProviders: vi.fn(() => providers),
    listAuthMethods: vi.fn(async () => ({})),
    listProviderCatalog: vi.fn(async () => ({ all: [], connected: [] })),
    listCustomProviderIds: vi.fn(async () => []),
    listMcpServers: vi.fn(async () => []),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.client = null;
  mocks.runtimeStore!.setState({
    status: "offline",
    serverUrl: "http://127.0.0.1:41001",
    defaultModel: null,
  });
});

describe("Settings endpoint ownership", () => {
  it("does not let endpoint A's late catalog overwrite endpoint B", async () => {
    const oldProviders = deferred<Array<{ id: string; name: string; models: never[] }>>();
    const clientA = fakeClient(oldProviders.promise);
    const clientB = fakeClient(
      Promise.resolve([{ id: "provider-b", name: "Provider B", models: [] }]),
    );
    mocks.client = clientA;
    mocks.runtimeStore!.setState({ status: "ready" });
    render(<SettingsPage />);
    await waitFor(() => expect(clientA.listProviders).toHaveBeenCalledTimes(1));

    act(() => {
      mocks.client = null;
      mocks.runtimeStore!.setState({
        status: "offline",
        serverUrl: "http://127.0.0.1:41002",
      });
    });
    act(() => {
      mocks.client = clientB;
      mocks.runtimeStore!.setState({ status: "ready" });
    });
    expect(await screen.findAllByText("Provider B")).not.toHaveLength(0);

    await act(async () => {
      oldProviders.resolve([{ id: "provider-a", name: "Provider A", models: [] }]);
      await oldProviders.promise;
    });

    await waitFor(() => expect(screen.queryByText("Provider A")).not.toBeInTheDocument());
    expect(screen.getAllByText("Provider B")).not.toHaveLength(0);
  });

  it("clears endpoint-scoped credentials before connecting endpoint B", async () => {
    const clientA = fakeClient(Promise.resolve([]));
    const clientB = fakeClient(Promise.resolve([]));
    mocks.client = clientA;
    mocks.runtimeStore!.setState({ status: "ready" });
    render(<SettingsPage />);
    await waitFor(() => expect(clientA.listProviders).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: /Custom endpoint/i }));
    const key = screen.getByPlaceholderText(/API key.*optional/i);
    fireEvent.change(key, { target: { value: "endpoint-a-secret" } });
    expect(key).toHaveValue("endpoint-a-secret");

    act(() => {
      mocks.client = null;
      mocks.runtimeStore!.setState({
        status: "offline",
        serverUrl: "http://127.0.0.1:41002",
      });
    });
    act(() => {
      mocks.client = clientB;
      mocks.runtimeStore!.setState({ status: "ready" });
    });
    await waitFor(() => expect(clientB.listProviders).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: /Custom endpoint/i }));
    expect(screen.getByPlaceholderText(/API key.*optional/i)).toHaveValue("");
  });
});
