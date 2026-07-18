import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  bootstrap: vi.fn(async () => {}),
  reconcileJupyter: vi.fn<() => Promise<unknown>>(),
  resolveReconcile: (() => {}) as () => void,
  setRuntimeState: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("@/lib/runtime", () => ({
  useRuntimeStore: {
    getState: () => ({ bootstrap: mocks.bootstrap }),
    setState: mocks.setRuntimeState,
  },
}));
vi.mock("@/lib/tauri", () => ({
  reconcileJupyter: mocks.reconcileJupyter,
  openExternal: vi.fn(async () => {}),
  watchFullscreen: vi.fn(async () => () => {}),
}));
vi.mock("@/lib/setup", () => ({ ensureSetupProgressListener: vi.fn() }));
vi.mock("@/lib/toast", () => ({ toast: { error: mocks.toastError } }));
vi.mock("@/lib/update", () => ({
  useUpdateStore: { getState: () => ({ maybeAutoCheck: vi.fn() }) },
}));
vi.mock("@/lib/store", () => ({
  useOverlayTitlebar: () => false,
  useUiStore: Object.assign(
    () => ({ sidebarCollapsed: false, setSidebarCollapsed: vi.fn() }),
    { getState: () => ({ toggleSidebar: vi.fn(), setIsFullscreen: vi.fn() }) },
  ),
}));
vi.mock("@/components/sidebar/Sidebar", () => ({ Sidebar: () => null }));
vi.mock("@/components/command-palette/CommandPalette", () => ({
  CommandPalette: () => null,
}));
vi.mock("@/components/ui/Toaster", () => ({ Toaster: () => null }));
vi.mock("@/lib/mock", () => ({ mockProject: {} }));

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  mocks.reconcileJupyter.mockImplementation(
    () => new Promise<void>((resolve) => (mocks.resolveReconcile = resolve)),
  );
});

describe("desktop service bootstrap", () => {
  it("single-flights startup and waits for Jupyter reconciliation before OpenCode", async () => {
    const { ensureDesktopServicesStarted } = await import("./AppShell");

    const first = ensureDesktopServicesStarted();
    const second = ensureDesktopServicesStarted();

    expect(first).toBe(second);
    expect(mocks.reconcileJupyter).toHaveBeenCalledTimes(1);
    expect(mocks.bootstrap).not.toHaveBeenCalled();

    mocks.resolveReconcile();
    await first;

    expect(mocks.bootstrap).toHaveBeenCalledTimes(1);
    expect(mocks.reconcileJupyter.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.bootstrap.mock.invocationCallOrder[0],
    );
  });

  it("fails closed and surfaces reconciliation errors without starting OpenCode", async () => {
    mocks.reconcileJupyter.mockRejectedValueOnce(new Error("legacy config could not be scrubbed"));
    const { ensureDesktopServicesStarted } = await import("./AppShell");

    await ensureDesktopServicesStarted();

    expect(mocks.bootstrap).not.toHaveBeenCalled();
    expect(mocks.setRuntimeState).toHaveBeenCalledWith({
      status: "error",
      error: expect.stringMatching(/blocked runtime startup.*legacy config/i),
    });
    expect(mocks.toastError).toHaveBeenCalledWith(
      expect.stringMatching(/blocked runtime startup.*legacy config/i),
    );
  });
});
