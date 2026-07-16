import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  providers: [] as Array<{ id: string; name: string; models: never[] }>,
  customIds: [] as string[],
  authMethods: {} as Record<string, unknown[]>,
  saveProviderApiKey: vi.fn(async () => {}),
  removeProviderApiKey: vi.fn(async () => {}),
  finalizeProviderLogin: vi.fn(async () => {}),
  setProviderApiKey: vi.fn(async () => {}),
  removeProviderAuth: vi.fn(async () => {}),
  addCustomProvider: vi.fn(async () => {}),
  oauthAuthorize: vi.fn(async () => ({
    url: "https://provider.example/login",
    method: "code" as const,
    instructions: "Paste the provider code.",
  })),
  oauthCallback: vi.fn(async () => {}),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
  client: null as Record<string, unknown> | null,
  runtimeStore: null as null | { setState: (partial: Record<string, unknown>) => void },
}));

vi.mock("@/lib/runtime", async () => {
  const { create } = await import("zustand");
  const useRuntimeStore = create(() => ({
    status: "ready",
    serverUrl: "http://127.0.0.1:41001",
    setServerUrl: vi.fn(),
    connect: vi.fn(async () => {}),
    disconnect: vi.fn(),
    defaultModel: null,
    loadCatalog: vi.fn(async () => {}),
    removeConfigEntry: vi.fn(async () => {}),
    saveProviderApiKey: mocks.saveProviderApiKey,
    removeProviderApiKey: mocks.removeProviderApiKey,
    finalizeProviderLogin: mocks.finalizeProviderLogin,
    importOpenCodeLogin: vi.fn(async () => false),
  }));
  mocks.runtimeStore = useRuntimeStore;
  return {
    getClient: () => mocks.client,
    useRuntimeStore,
  };
});

vi.mock("@/lib/tauri", () => ({
  isTauri: true,
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

vi.mock("@/lib/toast", () => ({
  toast: { success: mocks.toastSuccess, error: mocks.toastError },
}));

vi.mock("@/components/settings/RemoteComputeCard", () => ({
  RemoteComputeCard: () => null,
}));
vi.mock("@/components/settings/ModalCard", () => ({ ModalCard: () => null }));
vi.mock("@/components/settings/DataFlowCard", () => ({ DataFlowCard: () => null }));

import { SettingsPage } from "./SettingsPage";

function makeClient() {
  return {
    listProviders: vi.fn(async () => mocks.providers),
    listAuthMethods: vi.fn(async () => mocks.authMethods),
    listProviderCatalog: vi.fn(async () => ({
      all: [
        { id: "anthropic", name: "Anthropic", env: ["ANTHROPIC_API_KEY"] },
      ],
      connected: mocks.providers.map((provider) => provider.id),
    })),
    listCustomProviderIds: vi.fn(async () => mocks.customIds),
    listMcpServers: vi.fn(async () => []),
    setProviderApiKey: mocks.setProviderApiKey,
    removeProviderAuth: mocks.removeProviderAuth,
    addCustomProvider: mocks.addCustomProvider,
    oauthAuthorize: mocks.oauthAuthorize,
    oauthCallback: mocks.oauthCallback,
  };
}

async function selectAnthropic() {
  const search = await screen.findByPlaceholderText(/Connect a provider/i);
  fireEvent.change(search, { target: { value: "anthropic" } });
  return screen.findByPlaceholderText(/Anthropic API key/i);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.providers = [];
  mocks.customIds = [];
  mocks.authMethods = {};
  mocks.client = makeClient();
  mocks.runtimeStore!.setState({
    status: "ready",
    serverUrl: "http://127.0.0.1:41001",
    defaultModel: null,
  });
});

describe("Settings provider credential custody (desktop)", () => {
  it("saves a built-in provider key through the native keychain transaction", async () => {
    render(<SettingsPage />);
    const keyInput = await selectAnthropic();
    fireEvent.change(keyInput, { target: { value: "sk-native" } });
    fireEvent.click(within(keyInput.parentElement!).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(mocks.saveProviderApiKey).toHaveBeenCalledWith("anthropic", "sk-native"),
    );
    expect(mocks.setProviderApiKey).not.toHaveBeenCalled();
  });

  it("adds custom endpoint metadata without a secret before saving its key natively", async () => {
    render(<SettingsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Custom endpoint/i }));
    fireEvent.change(screen.getByPlaceholderText(/Name — e.g. Ollama/i), {
      target: { value: "My Lab" },
    });
    fireEvent.change(screen.getByPlaceholderText(/Base URL/i), {
      target: { value: "https://lab.example/v1" },
    });
    fireEvent.change(screen.getByPlaceholderText(/API key — optional/i), {
      target: { value: "lab-secret" },
    });
    fireEvent.change(screen.getByPlaceholderText(/Model ids/i), {
      target: { value: "lab-model" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add endpoint" }));

    await waitFor(() => expect(mocks.addCustomProvider).toHaveBeenCalledTimes(1));
    expect(mocks.addCustomProvider).toHaveBeenCalledWith(
      "my-lab",
      expect.objectContaining({ apiKey: undefined, baseURL: "https://lab.example/v1" }),
    );
    expect(mocks.saveProviderApiKey).toHaveBeenCalledWith("my-lab", "lab-secret");
  });

  it("finalizes a completed OAuth callback before showing connected", async () => {
    mocks.authMethods = {
      anthropic: [{ type: "oauth", label: "Sign in with Anthropic" }],
    };
    mocks.finalizeProviderLogin.mockRejectedValueOnce(new Error("keychain migration rejected"));
    render(<SettingsPage />);
    await selectAnthropic();
    fireEvent.click(screen.getByRole("button", { name: "Sign in with Anthropic" }));
    const code = await screen.findByPlaceholderText("Paste the code from the browser");
    fireEvent.change(code, { target: { value: "oauth-code" } });
    fireEvent.click(screen.getByRole("button", { name: "Complete login" }));

    await waitFor(() =>
      expect(mocks.finalizeProviderLogin).toHaveBeenCalledWith("anthropic"),
    );
    expect(mocks.oauthCallback).toHaveBeenCalledWith("anthropic", 0, "oauth-code");
    expect(mocks.toastSuccess).not.toHaveBeenCalled();
    expect(mocks.toastError).toHaveBeenCalledWith(
      expect.stringContaining("keychain migration rejected"),
    );
  });

  it("removes built-in and custom credentials through the native cleanup paths", async () => {
    mocks.providers = [{ id: "anthropic", name: "Anthropic", models: [] }];
    const builtIn = render(<SettingsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
    await waitFor(() =>
      expect(mocks.removeProviderApiKey).toHaveBeenCalledWith("anthropic", false),
    );
    expect(mocks.removeProviderAuth).toHaveBeenCalledWith("anthropic");

    builtIn.unmount();
    vi.clearAllMocks();
    mocks.providers = [{ id: "my-lab", name: "My Lab", models: [] }];
    mocks.customIds = ["my-lab"];
    mocks.client = makeClient();
    render(<SettingsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
    await waitFor(() =>
      expect(mocks.removeProviderApiKey).toHaveBeenCalledWith("my-lab", true),
    );
    expect(mocks.removeProviderAuth).not.toHaveBeenCalled();
  });
});
