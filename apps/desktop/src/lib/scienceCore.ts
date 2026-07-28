import { ScienceCoreClient } from "@spark/research-sdk";
import {
  isTauri,
  retryScienceCore,
  scienceCoreConnection,
  scienceCoreStatus,
  type ScienceCoreRuntimeState,
  type ScienceCoreRuntimeStatus,
} from "@/lib/tauri";

const configuredBaseUrl = import.meta.env.VITE_SCIENCE_CORE_URL?.trim();
const configuredToken = import.meta.env.VITE_SCIENCE_CORE_TOKEN?.trim();
const STARTUP_TIMEOUT_MS = 240_000;
const STATUS_POLL_MS = 250;

/** Browser development keeps its explicit env contract; packaged Tauri never uses it. */
export const scienceCoreConfigurationError = isTauri
  ? null
  : !configuredBaseUrl
    ? "Science core URL is not configured"
    : !configuredToken
      ? "Science core token is not configured"
      : null;

export class ScienceCoreRuntimeError extends Error {
  readonly state: ScienceCoreRuntimeState | "timeout" | "invalid-connection";

  constructor(
    state: ScienceCoreRuntimeState | "timeout" | "invalid-connection",
    message: string,
  ) {
    super(message);
    this.name = "ScienceCoreRuntimeError";
    this.state = state;
  }
}

let generation = 0;
interface ClientRecord {
  generation: number;
  client: ScienceCoreClient;
}

let cachedClient: ClientRecord | null = null;
let pendingClient: { generation: number; promise: Promise<ClientRecord> } | null = null;
let pendingRetry: Promise<ScienceCoreRuntimeStatus | null> | null = null;

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function timeoutError(): ScienceCoreRuntimeError {
  return new ScienceCoreRuntimeError(
    "timeout",
    "Science Core startup timed out. Verify Docker Desktop is running, then retry.",
  );
}

async function beforeDeadline<T>(deadline: number, operation: () => Promise<T>): Promise<T> {
  const remaining = deadline - Date.now();
  if (remaining <= 0) throw timeoutError();
  let timer: number | undefined;
  try {
    return await Promise.race([
      operation(),
      new Promise<T>((_resolve, reject) => {
        timer = window.setTimeout(() => reject(timeoutError()), remaining);
      }),
    ]);
  } finally {
    if (timer !== undefined) window.clearTimeout(timer);
  }
}

function stateError(status: ScienceCoreRuntimeStatus): ScienceCoreRuntimeError {
  const fallback =
    status.state === "failed"
      ? "Science Core failed to start. Verify Docker Desktop is running, then retry."
      : status.state === "stopped"
        ? "Science Core is stopped. Retry to start it."
        : "Packaged Science Core resources are unavailable. Reinstall Spark Agent, then retry.";
  return new ScienceCoreRuntimeError(status.state, status.message?.trim() || fallback);
}

function validPackagedConnection(endpoint: string, token: string): boolean {
  try {
    const url = new URL(endpoint);
    return (
      url.protocol === "http:" &&
      url.hostname === "127.0.0.1" &&
      /^\d+$/.test(url.port) &&
      Number(url.port) >= 1 &&
      Number(url.port) <= 65_535 &&
      url.pathname === "/" &&
      url.username === "" &&
      url.password === "" &&
      url.search === "" &&
      url.hash === "" &&
      /^[a-f0-9]{64}$/.test(token)
    );
  } catch {
    return false;
  }
}

async function readPackagedStatus(deadline: number): Promise<ScienceCoreRuntimeStatus> {
  try {
    const status = await beforeDeadline(deadline, scienceCoreStatus);
    if (status) return status;
  } catch (error) {
    if (error instanceof ScienceCoreRuntimeError) throw error;
    // Do not reflect IPC payloads into UI errors; the Rust status contract is secret-free.
  }
  throw new ScienceCoreRuntimeError(
    "failed",
    "Could not read the local Science Core runtime state. Retry from Research.",
  );
}

async function waitForPackagedClient(): Promise<ScienceCoreClient> {
  const deadline = Date.now() + STARTUP_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const status = await readPackagedStatus(deadline);
    if (status.state === "ready") {
      let connection;
      try {
        connection = await beforeDeadline(deadline, scienceCoreConnection);
      } catch (error) {
        if (error instanceof ScienceCoreRuntimeError) throw error;
        throw new ScienceCoreRuntimeError(
          "invalid-connection",
          "Science Core became unavailable during connection handoff. Retry from Research.",
        );
      }
      if (
        !connection ||
        !validPackagedConnection(connection.endpoint, connection.token)
      ) {
        throw new ScienceCoreRuntimeError(
          "invalid-connection",
          "Science Core returned an invalid local connection. Retry from Research.",
        );
      }
      return new ScienceCoreClient({
        baseUrl: connection.endpoint,
        token: connection.token,
      });
    }
    if (status.state !== "starting" && status.state !== "stopping") {
      throw stateError(status);
    }
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw timeoutError();
    await wait(Math.min(STATUS_POLL_MS, remaining));
  }
  throw timeoutError();
}

async function getScienceCoreClient(): Promise<ClientRecord> {
  if (!isTauri) {
    if (scienceCoreConfigurationError) throw new Error(scienceCoreConfigurationError);
    if (!cachedClient) {
      cachedClient = {
        generation,
        client: new ScienceCoreClient({
          baseUrl: configuredBaseUrl,
          token: configuredToken,
        }),
      };
    }
    return cachedClient;
  }

  if (cachedClient?.generation === generation) return cachedClient;
  if (pendingClient?.generation === generation) return pendingClient.promise;

  const requestGeneration = generation;
  const promise = waitForPackagedClient()
    .then((client) => {
      const record = { generation: requestGeneration, client };
      if (requestGeneration === generation) cachedClient = record;
      return record;
    })
    .finally(() => {
      if (pendingClient?.generation === requestGeneration) pendingClient = null;
    });
  pendingClient = { generation: requestGeneration, promise };
  return promise;
}

export async function getScienceCoreRuntimeStatus(): Promise<ScienceCoreRuntimeStatus | null> {
  return isTauri ? readPackagedStatus(Date.now() + STARTUP_TIMEOUT_MS) : null;
}

export async function retryScienceCoreRuntime(): Promise<ScienceCoreRuntimeStatus | null> {
  if (pendingRetry) return pendingRetry;
  generation += 1;
  cachedClient = null;
  pendingClient = null;
  if (!isTauri) return null;
  const deadline = Date.now() + STARTUP_TIMEOUT_MS;
  const retry = beforeDeadline(deadline, retryScienceCore)
    .catch((error) => {
      if (error instanceof ScienceCoreRuntimeError) throw error;
      throw new ScienceCoreRuntimeError(
        "failed",
        "Could not retry the local Science Core runtime. Verify Docker Desktop is running.",
      );
    })
    .finally(() => {
      if (pendingRetry === retry) pendingRetry = null;
    });
  pendingRetry = retry;
  return retry;
}

/**
 * Stable facade; each call resolves the current client generation. Packaged tokens
 * stay inside the client closure and are never exported, rendered, logged, or stored.
 */
export const scienceCore = new Proxy({} as ScienceCoreClient, {
  get(_target, property) {
    return (...args: unknown[]) => (async () => {
      for (;;) {
        const record = await getScienceCoreClient();
        if (record.generation !== generation) continue;
        const method = Reflect.get(record.client, property);
        if (typeof method !== "function") {
          throw new Error("Science Core client method is unavailable");
        }
        if (record.generation !== generation) continue;
        return Reflect.apply(method, record.client, args);
      }
    })();
  },
});
