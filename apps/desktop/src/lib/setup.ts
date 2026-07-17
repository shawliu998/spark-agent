// App-lifetime owner of the long-running uv provisioning flows (isolated
// Jupyter env, science-MCP connectors). This state lived inside SettingsPage
// before, so navigating away — clicking a chat or a history session —
// unmounted the page, discarded the "setting up…" flags, and (worse) severed
// the setup-progress listener, making a still-running download look frozen and
// inviting a second click that collided on the same env dir. Owning it here
// means the download is unaffected by which page is open.
import { create } from "zustand";
import { getClient, useRuntimeStore } from "./runtime";
import { setupJupyter, startJupyter, setupScienceMcp, watchSetupProgress } from "./tauri";
import { SCIENCE_CONNECTORS, connectorConfig } from "./scienceConnectors";
import { toast } from "./toast";

interface SetupState {
  /** True while the isolated Jupyter env is being provisioned. */
  jupyterBusy: boolean;
  /** The science connector currently provisioning, by id (null = none). */
  connectorId: string | null;
  /** Latest live uv output line — reassurance during a hundreds-of-MB download. */
  line: string | null;
  /** Bumped when any provisioning run finishes, so open pages re-read status. */
  generation: number;
  enableJupyter: () => Promise<void>;
  enableConnector: (id: string, apiKey?: string) => Promise<boolean>;
}

export const useSetupStore = create<SetupState>((set, get) => ({
  jupyterBusy: false,
  connectorId: null,
  line: null,
  generation: 0,

  enableJupyter: async () => {
    // One provisioning run at a time: a second `uv venv` / `pip install` into
    // the same env dir races the first and fails.
    if (get().jupyterBusy) return;
    set({ jupyterBusy: true, line: null });
    try {
      toast.success("Setting up Jupyter — first run downloads a few hundred MB, please wait…");
      await setupJupyter();
      const s = await startJupyter();
      if (!s.installed || !s.running) throw new Error("setup finished incomplete");
      toast.success(
        "Local JupyterLab is ready. Agent MCP access remains security-gated until the native broker passes its release gates.",
      );
    } catch (e) {
      toast.error(`Jupyter setup failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      set((st) => ({ jupyterBusy: false, line: null, generation: st.generation + 1 }));
    }
  },

  enableConnector: async (id, apiKey) => {
    if (get().connectorId) return false; // one connector provisioning at a time
    const c = SCIENCE_CONNECTORS.find((x) => x.id === id);
    if (!c) return false;
    if (c.securityGated) {
      toast.error(
        `${c.label} remains disabled until native per-call approval and immutable connector targets are enforced.`,
      );
      return false;
    }
    const connectorApiKey = apiKey?.trim();
    if (c.apiKeyEnv && !connectorApiKey) {
      toast.error(`${c.label} requires an API key.`);
      return false;
    }
    const setupClient = getClient();
    if (!setupClient) {
      toast.error("Connect the runtime before enabling a science connector.");
      return false;
    }
    set({ connectorId: id, line: null });
    try {
      toast.success(`Setting up ${c.label} — first run downloads a managed Python, please wait…`);
      const python = await setupScienceMcp(c.id);
      if (getClient() !== setupClient) {
        toast.error(
          `${c.label} was installed locally, but the runtime endpoint changed before MCP registration. Reconnect and enable it again.`,
        );
        return false;
      }
      if (c.apiKeyEnv) {
        // Native code owns the whole credential/config/restart transaction and
        // reconnects to the authoritative endpoint. The secret never enters
        // the OpenCode config API.
        await useRuntimeStore
          .getState()
          .saveScienceConnectorApiKey(c.id, connectorApiKey!);
      } else {
        const config = connectorConfig(c, python);
        await setupClient.addMcpServer(c.id, config);
        if (getClient() !== setupClient) return false;
      }
      toast.success(`${c.label} enabled — the agent can now use it from chat.`);
      await useRuntimeStore.getState().loadCatalog();
      return true;
    } catch (e) {
      toast.error(`${c.label} setup failed: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    } finally {
      set((st) => ({ connectorId: null, line: null, generation: st.generation + 1 }));
    }
  },
}));

/** Ensure one curated connector is present on the current runtime endpoint.
 * Provisioning remains inside the native allowlist; callers only select an id. */
export async function ensureScienceConnector(id: string): Promise<void> {
  const setupClient = getClient();
  if (!setupClient) throw new Error("Connect the runtime before setting up a science connector.");

  const isPresent = async () =>
    (await setupClient.listMcpServers()).some((server) => server.name === id);
  if (await isPresent()) return;

  const enabled = await useSetupStore.getState().enableConnector(id);
  if (!enabled) throw new Error(`Science connector ${id} setup did not complete.`);
  if (getClient() !== setupClient) {
    throw new Error("The runtime endpoint changed during connector setup. Reconnect and try again.");
  }
  if (!(await isPresent())) {
    throw new Error(`Science connector ${id} was not registered after setup.`);
  }
}

// A SINGLE app-lifetime uv-progress listener. Registered once from AppShell so
// a page unmount can never sever it — the old per-page listener died with
// SettingsPage and made a running download look frozen.
let progressUnlisten: (() => void) | null = null;

/** Start the shared uv-progress listener (idempotent). Call once from AppShell. */
export function ensureSetupProgressListener(): void {
  if (progressUnlisten) return;
  progressUnlisten = () => {}; // claim the slot synchronously against a double call
  void watchSetupProgress((p) => useSetupStore.setState({ line: p.line })).then((u) => {
    progressUnlisten = u;
  });
}
