// Thin bridge to the Tauri Rust side. In a plain browser these are no-ops so the
// app still runs in `pnpm dev`; in the packaged desktop app they invoke Rust commands.

export const isTauri =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export interface OpenCodeCredentials {
  provider: string;
  apiKey: string;
  model: string;
  baseUrl?: string;
}

export type ConfigureResult =
  | { ok: true; path: string; runtimeUrl: string | null }
  | { ok: false; reason: "not-desktop" }
  | { ok: false; reason: "error"; message: string };

export interface RuntimeRestartResult {
  /** Authoritative loopback URL after the mutation; null when no runtime was running. */
  runtimeUrl: string | null;
}

export interface ImportOpenCodeLoginResult extends RuntimeRestartResult {
  imported: boolean;
}

/** Start the bundled OpenCode sidecar (desktop only). Returns its base URL. */
export async function startRuntime(): Promise<string | null> {
  if (!isTauri) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string>("start_runtime");
}

/**
 * Per-run password the sidecar requires on every request (desktop only —
 * browser dev talks to a user-run, passwordless `opencode serve`). Held in
 * memory on both sides; never persisted.
 */
export async function runtimePassword(): Promise<string | null> {
  if (!isTauri) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string>("runtime_password");
}

/**
 * Pick local files via the native dialog and copy them into the agent
 * workspace (desktop only). Returns the workspace file names; [] on cancel.
 */
export async function addFilesToWorkspace(): Promise<string[]> {
  if (!isTauri) return [];
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string[]>("add_files_to_workspace");
}

/**
 * Write text into the workspace as a file (desktop only), deduplicating the
 * name on collision. Returns the actual file name written.
 */
export async function addTextToWorkspace(filename: string, content: string): Promise<string> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string>("add_text_to_workspace", { filename, content });
}

/**
 * Explicitly import the user's OpenCode CLI login into the app's private
 * runtime (desktop only). Returns false when no CLI login exists; the sidecar
 * is restarted on success.
 */
export async function importOpenCodeLogin(): Promise<ImportOpenCodeLoginResult> {
  if (!isTauri) return { imported: false, runtimeUrl: null };
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<ImportOpenCodeLoginResult>("import_opencode_login");
}

/** Persisted approval state reported by the bundled OpenCode runtime. Custom
 * means a user-authored policy that Spark preserves but cannot summarize as a
 * built-in preset. Full and custom are report-only; only approve is writable. */
export type ApprovalMode = "approve" | "full" | "custom";

/** The approval mode OpenCode's config currently holds ("approve" until changed). */
export async function getApprovalMode(): Promise<ApprovalMode> {
  if (!isTauri) return "approve";
  const { invoke } = await import("@tauri-apps/api/core");
  const mode = await invoke<string>("get_approval_mode");
  if (mode === "approve" || mode === "full" || mode === "custom") return mode;
  throw new Error(`Unsupported approval mode returned by the runtime: ${mode}`);
}

/** Switch the approval mode; the sidecar restarts — the caller must reconnect. */
export async function setApprovalMode(mode: ApprovalMode): Promise<RuntimeRestartResult> {
  if (mode !== "approve") {
    throw new Error(
      mode === "full"
        ? "Full Access is a legacy unsafe mode; switch to Manual approval."
        : "Custom approval policies are report-only and cannot be selected in Spark Agent.",
    );
  }
  if (!isTauri) return { runtimeUrl: null };
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<RuntimeRestartResult>("set_approval_mode", { mode });
}

/** Network proxy for the sidecar: follow the OS, a fixed URL, or direct. */
export type ProxyMode = "system" | "custom" | "none";
export interface ProxySetting {
  mode: ProxyMode;
  /** The custom URL (empty unless mode is "custom"). */
  url: string;
  /** The proxy the sidecar would use right now; null ⇒ direct. */
  effective: string | null;
}

/** The persisted proxy setting (desktop only; null in browser). */
export async function getProxySetting(): Promise<ProxySetting | null> {
  if (!isTauri) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return await invoke<ProxySetting>("get_proxy_setting");
}

/** Persist the proxy setting; the sidecar restarts — the caller must reconnect. */
export async function setProxySetting(mode: ProxyMode, url: string): Promise<RuntimeRestartResult> {
  if (!isTauri) return { runtimeUrl: null };
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<RuntimeRestartResult>("set_proxy_setting", { mode, url });
}

/** Remove a provider/mcp entry from the global OpenCode config (restarts the sidecar). */
export async function removeConfigEntry(
  section: "provider" | "mcp",
  key: string,
): Promise<RuntimeRestartResult> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<RuntimeRestartResult>("remove_config_entry", { section, key });
}

/** Save a provider API key in the OS credential manager and reconnect the
 * bundled runtime with only an environment placeholder in OpenCode config. */
export async function saveProviderApiKey(
  providerId: string,
  apiKey: string,
): Promise<RuntimeRestartResult> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<RuntimeRestartResult>("save_provider_api_key", { providerId, apiKey });
}

/** Remove a provider's keychain item and every live file-backed API-key
 * reference. Custom endpoints may request removal of their entire config. */
export async function removeProviderApiKey(
  providerId: string,
  removeProviderConfig: boolean,
): Promise<RuntimeRestartResult> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<RuntimeRestartResult>("remove_provider_api_key", {
    providerId,
    removeProviderConfig,
  });
}

/** Save an allowlisted curated connector key in the OS credential manager.
 * Native code derives the exact managed executable and complete MCP config. */
export async function saveScienceConnectorApiKey(
  connectorId: string,
  apiKey: string,
): Promise<RuntimeRestartResult> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<RuntimeRestartResult>("save_science_connector_api_key", {
    connectorId,
    apiKey,
  });
}

/** Remove an allowlisted curated connector's config references and credential. */
export async function removeScienceConnector(
  connectorId: string,
): Promise<RuntimeRestartResult> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<RuntimeRestartResult>("remove_science_connector", { connectorId });
}

/** Finalize a login written by OpenCode: API keys move to the keychain while
 * OAuth records remain OpenCode-owned in its app-private auth file. */
export async function finalizeProviderLogin(
  providerId: string,
): Promise<RuntimeRestartResult> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<RuntimeRestartResult>("finalize_provider_login", { providerId });
}

export interface JupyterStatus {
  installed: boolean;
  running: boolean;
  /** Native-validated Spark-owned registration; no connection secret crosses IPC. */
  registered: boolean;
}

/** State of the app-managed Jupyter environment (desktop only). */
export async function jupyterStatus(): Promise<JupyterStatus | null> {
  if (!isTauri) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<JupyterStatus>("jupyter_status");
}

/** Provision the isolated Jupyter env via bundled uv (first run: minutes, ~hundreds of MB). */
export async function setupJupyter(): Promise<void> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("setup_jupyter");
}

/** Start the managed headless jupyter-lab (idempotent). */
export async function startJupyter(): Promise<JupyterStatus> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<JupyterStatus>("start_jupyter");
}

/** Reconcile legacy Jupyter config and the managed process before the bundled
 * runtime boots. The caller must not start OpenCode until this succeeds: native
 * reconciliation is the pre-launch boundary that removes unsafe legacy state. */
export async function reconcileJupyter(): Promise<RuntimeRestartResult> {
  if (!isTauri) return { runtimeUrl: null };
  const { invoke } = await import("@tauri-apps/api/core");
  const result = await invoke<RuntimeRestartResult>("reconcile_jupyter");
  // Restoring the optional local Lab is intentionally best-effort. Config
  // reconciliation above is the security boundary and must succeed before
  // OpenCode starts; a broken optional Python environment must not strand the
  // rest of the desktop after that boundary has passed.
  try {
    const status = await jupyterStatus();
    if (status?.installed && !status.running) await startJupyter();
  } catch {
    /* Settings reports and repairs optional Jupyter setup failures. */
  }
  return result;
}

/** Ask native code to start and open the app-managed JupyterLab. Returns false
 * when Jupyter has not been set up yet (the caller should point the user at
 * Settings). Same environment and files, with all authorization kept native.
 *
 *  `notebook` is a path RELATIVE TO THE LAB ROOT (the active workspace) — pass
 *  it to open that file directly; omit to land on the lab home. Native code
 *  validates that it remains under the workspace root. */
export async function openJupyterLab(notebook?: string): Promise<boolean> {
  if (!isTauri) return false;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<boolean>("open_jupyter_lab", {
    notebook: notebook?.trim() || null,
  });
}

/** The interpreter local Python kernels resolve to, and where it came from. */
export interface PythonInterpreter {
  /** The manual override, if one is set (even when it no longer runs). */
  configured: string | null;
  /** What cells would actually run on right now. */
  resolved: string | null;
  source: "manual" | "system" | "jupyter-env" | null;
  error: string | null;
}

export async function pythonInterpreter(): Promise<PythonInterpreter | null> {
  if (!isTauri) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<PythonInterpreter>("python_interpreter");
}

/** Set (empty clears) the manual Python interpreter override. Validated on save. */
export async function setPythonPath(path: string): Promise<void> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("set_python_path", { path });
}

/** One live output line from a uv provisioning run (jupyter / science MCP). */
export interface SetupProgress {
  task: "jupyter" | "science";
  line: string;
}

/** Subscribe to setup progress lines; returns the unlisten function. */
export async function watchSetupProgress(
  cb: (p: SetupProgress) => void,
): Promise<() => void> {
  if (!isTauri) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen<SetupProgress>("setup-progress", (e) => cb(e.payload));
}

/** Managed interpreter path for the shared science-MCP env, or null if not yet
 *  provisioned (desktop only). */
export async function scienceMcpPython(): Promise<string | null> {
  if (!isTauri) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string | null>("science_mcp_python");
}

/** Provision one native-allowlisted science connector and return its managed
 * Python path (desktop only). The renderer never supplies a package spec. */
export async function setupScienceMcp(connectorId: string): Promise<string> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string>("setup_science_mcp", { connectorId });
}

/** Open an http(s) URL in the system browser (never navigates the webview). */
export async function openExternal(url: string): Promise<void> {
  if (!/^https?:\/\//i.test(url)) return;
  if (isTauri) {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("open_url", { url });
    } catch {
      /* opening a link must never break the app */
    }
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

export interface LatestRelease {
  version: string;
  url: string;
  name: string | null;
  publishedAt: string | null;
}

export async function latestRelease(): Promise<LatestRelease | null> {
  if (!isTauri) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<LatestRelease>("latest_release");
}

export type SaveResult =
  | { kind: "saved"; path: string }
  | { kind: "canceled" }
  | { kind: "not-desktop" };

/** Save text via the native "Save As" dialog (desktop only). Throws on write failure. */
export async function saveTextFile(filename: string, content: string): Promise<SaveResult> {
  if (!isTauri) return { kind: "not-desktop" };
  const { invoke } = await import("@tauri-apps/api/core");
  const path = await invoke<string | null>("save_text_file", { filename, content });
  return path ? { kind: "saved", path } : { kind: "canceled" };
}

/** The active workspace directory (desktop only; null in browser). */
export async function workspacePath(): Promise<string | null> {
  if (!isTauri) return null;
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<string>("workspace_path");
  } catch {
    return null;
  }
}

/** The base folder new dated workspaces are created under (desktop only). */
export async function workspaceBase(): Promise<string | null> {
  if (!isTauri) return null;
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<string>("workspace_base");
  } catch {
    return null;
  }
}

/** Choose the base folder new session workspaces are created under.
 *  Returns the canonical path. Throws in the browser. */
export async function setWorkspaceBase(path: string): Promise<string> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string>("set_workspace_base", { path });
}

/** Reveal the base workspace folder in the OS file manager. */
export async function openWorkspaceBase(): Promise<void> {
  if (!isTauri) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("open_workspace_base");
}

/** Switch the active workspace folder (creates it if needed; the runtime
 *  rescopes via `?directory=` — no restart). Returns the canonical path.
 *  Throws in the browser. */
export async function setWorkspace(path: string): Promise<string> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string>("set_workspace", { path });
}

/** Fail closed unless the pinned runtime's resolved agents retain Spark's
 *  effective permission floor for the active workspace. Called immediately
 *  before every desktop turn; browser development has no native sidecar gate. */
export async function validateRuntimePermissions(path: string | null): Promise<void> {
  if (!isTauri) return;
  if (!path) throw new Error("The active workspace is unavailable for permission validation.");
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("validate_runtime_permissions", { path });
}

/** Record which session owns the active workspace (written to
 *  `.openscience/session.txt`) so skill helpers can attribute remote runs. */
export async function markSession(sessionId: string): Promise<void> {
  if (!isTauri) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("mark_session", { sessionId });
}

/** Best-effort local git checkpoint for the active workspace. Returns false
 *  when there were no changes. Never configures a remote or pushes. */
export async function commitWorkspaceSnapshot(message: string): Promise<boolean> {
  if (!isTauri) return false;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<boolean>("commit_workspace_snapshot", { message });
}

/** Create a new dated folder under the base workspace and switch to it. */
export async function newDatedWorkspace(name: string): Promise<string> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string>("new_dated_workspace", { name });
}

/** Native folder picker; null on cancel or in the browser. */
export async function pickFolder(): Promise<string | null> {
  if (!isTauri) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string | null>("pick_folder");
}

export interface ToolStatus {
  name: string;
  found: boolean;
  version?: string | null;
}

/** Detect scientific/runtime tools on the user's system (desktop only). */
export async function detectTools(): Promise<ToolStatus[]> {
  if (!isTauri) return [];
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<ToolStatus[]>("detect_tools");
}

/** Host aliases from the user's ~/.ssh/config (desktop only). */
export async function listSshHosts(): Promise<string[]> {
  if (!isTauri) return [];
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string[]>("list_ssh_hosts");
}

export interface GpuInfo {
  name: string;
  mem_total_mib: number;
  mem_used_mib: number;
  util_pct: number;
}

/** One live SSH probe of a remote machine (capabilities + usage snapshot). */
export interface ComputeProbe {
  reachable: boolean;
  message: string | null;
  os: string | null;
  cores: number | null;
  load1: number | null;
  mem_total_bytes: number | null;
  mem_avail_bytes: number | null;
  disk_total_bytes: number | null;
  disk_free_bytes: number | null;
  gpus: GpuInfo[];
  slurm: string | null;
}

/** Static capability cache the agent reads to pick a machine. */
export interface MachineCaps {
  cores: number | null;
  mem_total_bytes: number | null;
  gpus: string[];
  slurm: string | null;
}

export interface Machine {
  host: string;
  label: string | null;
  caps: MachineCaps | null;
}

/** A Slurm queue entry. */
export interface ComputeJob {
  id: string;
  state: string;
  time: string;
  partition: string;
  name: string;
}

/** Saved remote machines (migrates a legacy hpc.json on first read). */
export async function computeMachines(): Promise<Machine[]> {
  if (!isTauri) return [];
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<Machine[]>("compute_machines");
}

/** Save (or update the label of) a remote machine. */
export async function addComputeMachine(host: string, label?: string): Promise<void> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("add_compute_machine", { host, label: label ?? null });
}

export async function removeComputeMachine(host: string): Promise<void> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("remove_compute_machine", { host });
}

/** Probe a machine over SSH; also caches its static caps for the agent. */
export async function computeProbe(host: string): Promise<ComputeProbe> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<ComputeProbe>("compute_probe", { host });
}

/** A Slurm host's queue. */
export async function computeJobs(host: string): Promise<ComputeJob[]> {
  if (!isTauri) return [];
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<ComputeJob[]>("compute_jobs", { host });
}

export async function computeCancel(host: string, jobId: string): Promise<void> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("compute_cancel", { host, jobId });
}

export interface ModalStatus {
  installed: boolean;
  version: string | null;
  authenticated: boolean;
  hint: string | null;
}

/** Detect whether the user's Modal CLI is installed and authenticated. */
export async function modalStatus(): Promise<ModalStatus | null> {
  if (!isTauri) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<ModalStatus>("modal_status");
}

/** Copy a bundled example project into the workspace (idempotent; never
 *  overwrites user edits). Returns the workspace directory name. */
export async function installExample(name: string): Promise<string> {
  if (!isTauri) throw new Error("not running in the desktop app");
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string>("install_example", { name });
}

/** Append a diagnostic line to <app-data>/debug.log (desktop only; no-op in browser). */
export async function logDebug(message: string): Promise<void> {
  if (!isTauri) return;
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("log_debug", { message });
  } catch {
    /* never let diagnostics break the app */
  }
}

/** True when the current UA is macOS (traffic lights live in the window chrome). */
export function isMacUA(): boolean {
  return typeof navigator !== "undefined" && navigator.userAgent.includes("Mac");
}

/** Whether the macOS traffic lights overlap our content and need a left inset.
 *  Only in the packaged macOS webview (overlay titlebar) AND when not fullscreen
 *  — native fullscreen slides the lights away, so the inset would be an empty
 *  gap (the sidebar/expand buttons floated oddly indented in fullscreen). */
export function trafficLightsPresent(tauri: boolean, mac: boolean, fullscreen: boolean): boolean {
  return tauri && mac && !fullscreen;
}

/** Watch the window's fullscreen state (desktop only). Reports the current
 *  value immediately and on every enter/leave — fullscreen resizes the window,
 *  so a resize listener catches it. Returns an unlisten fn; in a plain browser
 *  it reports `false` once and unlisten is a no-op. */
export async function watchFullscreen(cb: (fullscreen: boolean) => void): Promise<() => void> {
  if (!isTauri) {
    cb(false);
    return () => {};
  }
  const { getCurrentWindow } = await import("@tauri-apps/api/window");
  const win = getCurrentWindow();
  const sync = async () => {
    try {
      cb(await win.isFullscreen());
    } catch {
      // Window gone or API unavailable — keep the last known value.
    }
  };
  await sync();
  return win.onResized(() => void sync());
}

/** Legacy onboarding bridge. Rust stores the key in the OS credential manager
 * and writes only its env placeholder alongside non-secret model settings. */
export async function configureOpenCode(
  creds: OpenCodeCredentials,
): Promise<ConfigureResult> {
  if (!isTauri) return { ok: false, reason: "not-desktop" };
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    const result = await invoke<{ path: string; runtimeUrl: string | null }>("configure_opencode", {
      provider: creds.provider,
      apiKey: creds.apiKey,
      model: creds.model,
      baseUrl: creds.baseUrl ?? null,
    });
    return { ok: true, ...result };
  } catch (e) {
    return { ok: false, reason: "error", message: e instanceof Error ? e.message : String(e) };
  }
}
