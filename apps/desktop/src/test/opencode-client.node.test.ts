// @vitest-environment node
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { OpenCodeClient, type OpenCodeEvent } from "@ai4s/sdk";
import { startMockOpenCode, type MockOpenCode } from "@ai4s/sdk/mock-server";

let server: MockOpenCode;

beforeAll(async () => {
  server = await startMockOpenCode(0);
});
afterAll(async () => {
  await server.close();
});

async function waitFor(pred: () => boolean, timeout = 3000) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > timeout) throw new Error("timeout");
    await new Promise((r) => setTimeout(r, 10));
  }
}

describe("OpenCodeClient ↔ OpenCode server", () => {
  it("connects, creates a session, sends a prompt, and streams normalized events", async () => {
    const events: OpenCodeEvent[] = [];
    const client = new OpenCodeClient({ baseUrl: `http://127.0.0.1:${server.port}` });
    client.onEvent((e) => events.push(e));

    await client.connect();
    expect(client.getStatus()).toBe("ready");

    const sessionId = await client.createSession();
    expect(sessionId).toBe("ses_mock");

    await client.sendPrompt(sessionId, "run a literature review");
    await waitFor(() => events.some((e) => e.type === "session.idle"));

    const types = events.map((e) => e.type);
    expect(types).toContain("text.updated");
    expect(types).toContain("tool.updated");

    // Text streams live: each message.part.delta yields the accumulated text,
    // it does not sit silent until the full part arrives at text-end.
    const p1 = events
      .filter((e): e is Extract<OpenCodeEvent, { type: "text.updated" }> =>
        e.type === "text.updated" && e.partId === "p1",
      )
      .map((e) => e.text);
    expect(p1).toContain("Planning ");
    expect(p1[p1.length - 1]).toBe("Planning the analysis. ");

    const toolDone = events.find(
      (e): e is Extract<OpenCodeEvent, { type: "tool.updated" }> =>
        e.type === "tool.updated" && e.status === "success",
    );
    expect(toolDone?.title).toContain("literature-search");

    client.close();
    expect(client.getStatus()).toBe("offline");
  });

  it("lists slash commands (config commands + skills, one merged list)", async () => {
    const client = new OpenCodeClient({ baseUrl: `http://127.0.0.1:${server.port}` });
    const commands = await client.listCommands();
    expect(commands.map((c) => c.name)).toEqual(["init", "analyze-data"]);
    expect(commands[1].source).toBe("skill");
  });

  it("runs a shell command: bash tool part + session.idle stream back", async () => {
    const events: OpenCodeEvent[] = [];
    const client = new OpenCodeClient({ baseUrl: `http://127.0.0.1:${server.port}` });
    client.onEvent((e) => events.push(e));
    await client.connect();
    await client.runShell("ses_mock", "pwd");
    await waitFor(() => events.some((e) => e.type === "session.idle"));
    const bash = events.find(
      (e): e is Extract<OpenCodeEvent, { type: "tool.updated" }> =>
        e.type === "tool.updated" && e.tool === "bash",
    );
    expect(bash?.status).toBe("success");
    expect(bash?.output).toContain("/ws/mock");
    client.close();
  });

  it("runs a slash command: a normal agent turn streams back", async () => {
    const events: OpenCodeEvent[] = [];
    const client = new OpenCodeClient({ baseUrl: `http://127.0.0.1:${server.port}` });
    client.onEvent((e) => events.push(e));
    await client.connect();
    await client.runCommand("ses_mock", "init", "focus on tests");
    await waitFor(() => events.some((e) => e.type === "session.idle"));
    expect(events.map((e) => e.type)).toContain("text.updated");
    client.close();
  });

  it("maps time.completed onto history messages and aborts a session", async () => {
    const client = new OpenCodeClient({ baseUrl: `http://127.0.0.1:${server.port}` });
    await client.connect();
    const sessionId = await client.createSession();
    await client.sendPrompt(sessionId, "run a literature review");
    const messages = await client.getMessages(sessionId);
    const last = messages[messages.length - 1];
    expect(last.role).toBe("assistant");
    expect(last.completed).toBe(2); // the turn is over — the reconcile signal
    await expect(client.abortSession(sessionId)).resolves.toBeUndefined();
    client.close();
  });

  it("reports an error status when the server is unreachable", async () => {
    const client = new OpenCodeClient({ baseUrl: "http://127.0.0.1:1" });
    await expect(client.connect()).rejects.toBeTruthy();
    expect(client.getStatus()).toBe("error");
  });

  it("disposes the cached instance after credential changes, so providers refresh", async () => {
    // The server caches its provider list per instance; PUT/DELETE /auth alone
    // leaves it stale (the new provider never appears in the UI). Verified on
    // opencode 1.17.13: POST /instance/dispose makes the change visible.
    const client = new OpenCodeClient({ baseUrl: `http://127.0.0.1:${server.port}` });

    server.requests.length = 0;
    await client.setProviderApiKey("mock", "sk-123");
    expect(server.requests).toEqual(["PUT /auth/mock", "POST /instance/dispose"]);

    server.requests.length = 0;
    await client.removeProviderAuth("mock");
    expect(server.requests).toEqual(["DELETE /auth/mock", "POST /instance/dispose"]);

    server.requests.length = 0;
    await client.oauthCallback("mock", 0);
    expect(server.requests).toEqual([
      "POST /provider/mock/oauth/callback",
      "POST /instance/dispose",
    ]);
  });

  it("disposes the workspace instance too when scoped to a directory", async () => {
    // Sessions run on the per-directory instance — if only the default one
    // were disposed, chats would keep a stale provider list until restart.
    const client = new OpenCodeClient({
      baseUrl: `http://127.0.0.1:${server.port}`,
      directory: "/ws/dir",
    });
    server.requests.length = 0;
    await client.setProviderApiKey("mock", "sk-123");
    expect(server.requests).toEqual([
      "PUT /auth/mock",
      "POST /instance/dispose",
      "POST /instance/dispose?directory=%2Fws%2Fdir",
    ]);
  });

  it("cancels a pending browser-login wait via the AbortSignal", async () => {
    // "auto" OAuth callbacks wait for the browser redirect — cancelling in
    // the UI must abort the request, not leak it on the sidecar.
    const client = new OpenCodeClient({ baseUrl: `http://127.0.0.1:${server.port}` });
    server.requests.length = 0;
    const abort = new AbortController();
    const pending = client.oauthCallback("slow", 0, undefined, abort.signal);
    await waitFor(() => server.requests.includes("POST /provider/slow/oauth/callback"));
    abort.abort();
    await expect(pending).rejects.toThrow();
    // An aborted login must not dispose the instance (nothing changed).
    expect(server.requests.filter((r) => r.includes("dispose"))).toEqual([]);
  });

  it("surfaces the server's diagnostic message when saving a key fails", async () => {
    const client = new OpenCodeClient({ baseUrl: `http://127.0.0.1:${server.port}` });
    await expect(client.setProviderApiKey("bad", "nope")).rejects.toThrow(/invalid key format/);
  });

  it("sends Basic auth on API calls when a password is set", async () => {
    // The sidecar now REQUIRES auth (OPENCODE_SERVER_PASSWORD) — every fetch
    // must carry the Authorization header or the server answers 401.
    const seen: (string | undefined)[] = [];
    const capturing: typeof fetch = (input, init) => {
      seen.push((init?.headers as Record<string, string> | undefined)?.["Authorization"]);
      return fetch(input, init);
    };
    const client = new OpenCodeClient({
      baseUrl: `http://127.0.0.1:${server.port}`,
      password: "pw-secret",
      fetchImpl: capturing,
    });
    await client.createSession();
    expect(seen[0]).toBe("Basic " + Buffer.from("opencode:pw-secret").toString("base64"));
  });

  it("keeps the EventSource stream when a password is set, authenticating via auth_token", async () => {
    // EventSource cannot set headers, but it is the reliable SSE path in the
    // WKWebView — the server accepts the same Basic payload as ?auth_token=.
    const urls: string[] = [];
    class FakeEventSource {
      onopen: (() => void) | null = null;
      onmessage: unknown = null;
      onerror: unknown = null;
      constructor(url: string) {
        urls.push(url);
        setTimeout(() => this.onopen?.(), 0);
      }
      close() {}
    }
    (globalThis as { EventSource?: unknown }).EventSource = FakeEventSource;
    try {
      const client = new OpenCodeClient({
        baseUrl: `http://127.0.0.1:${server.port}`,
        password: "pw-secret",
        directory: "/ws/dir",
      });
      await client.connect();
      expect(client.getStatus()).toBe("ready");
      const token = Buffer.from("opencode:pw-secret").toString("base64");
      expect(urls[0]).toContain(`auth_token=${encodeURIComponent(token)}`);
      expect(urls[0]).toContain(`directory=${encodeURIComponent("/ws/dir")}`);
      client.close();
    } finally {
      delete (globalThis as { EventSource?: unknown }).EventSource;
    }
  });

  it("immediately rejects a hanging EventSource handshake when closed", async () => {
    const sources: HangingEventSource[] = [];
    class HangingEventSource {
      onopen: (() => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null;
      close = vi.fn();
      constructor(_url: string) {
        sources.push(this);
      }
    }
    const globals = globalThis as { EventSource?: unknown };
    const previousEventSource = globals.EventSource;
    globals.EventSource = HangingEventSource;
    vi.useFakeTimers();
    try {
      const statuses: string[] = [];
      const client = new OpenCodeClient({
        baseUrl: `http://127.0.0.1:${server.port}`,
        connectTimeoutMs: 60_000,
      });
      client.onStatus((status) => statuses.push(status));

      let rejection: unknown;
      const pending = client.connect();
      void pending.catch((error: unknown) => {
        rejection = error;
      });
      const staleOpen = sources[0].onopen;
      const staleError = sources[0].onerror;

      client.close();
      await Promise.resolve();

      expect(rejection).toEqual(new Error("OpenCode event stream was closed"));
      expect(sources[0].close).toHaveBeenCalledOnce();
      expect(sources[0].onopen).toBeNull();
      expect(sources[0].onmessage).toBeNull();
      expect(sources[0].onerror).toBeNull();
      expect(vi.getTimerCount()).toBe(0);
      expect(client.getStatus()).toBe("offline");

      // Already-queued browser callbacks must be inert after close().
      staleOpen?.();
      staleError?.();
      expect(client.getStatus()).toBe("offline");
      expect(statuses[statuses.length - 1]).toBe("offline");
    } finally {
      vi.clearAllTimers();
      vi.useRealTimers();
      if (previousEventSource === undefined) delete globals.EventSource;
      else globals.EventSource = previousEventSource;
    }
  });

  it("does not start a handshake after a connecting listener closes synchronously", async () => {
    const sources: ReentrantEventSource[] = [];
    class ReentrantEventSource {
      onopen: (() => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null;
      close = vi.fn();
      constructor(_url: string) {
        sources.push(this);
      }
    }
    const globals = globalThis as { EventSource?: unknown };
    const previousEventSource = globals.EventSource;
    globals.EventSource = ReentrantEventSource;
    vi.useFakeTimers();
    try {
      const statuses: string[] = [];
      const client = new OpenCodeClient({
        baseUrl: `http://127.0.0.1:${server.port}`,
        connectTimeoutMs: 60_000,
      });
      client.onStatus((status) => {
        statuses.push(status);
        if (status === "connecting") client.close();
      });

      const outcome = client.connect().then(
        () => null,
        (error: unknown) => error,
      );
      await vi.runAllTimersAsync();

      expect(await outcome).toEqual(new Error("OpenCode event stream was closed"));
      expect(sources).toHaveLength(0);
      expect(vi.getTimerCount()).toBe(0);
      expect(client.getStatus()).toBe("offline");
      expect(statuses).toEqual(["connecting", "offline"]);
    } finally {
      vi.clearAllTimers();
      vi.useRealTimers();
      if (previousEventSource === undefined) delete globals.EventSource;
      else globals.EventSource = previousEventSource;
    }
  });

  it("lets a newer EventSource connection supersede a hanging handshake", async () => {
    const sources: HangingEventSource[] = [];
    class HangingEventSource {
      onopen: (() => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null;
      close = vi.fn();
      constructor(_url: string) {
        sources.push(this);
      }
    }
    const globals = globalThis as { EventSource?: unknown };
    const previousEventSource = globals.EventSource;
    globals.EventSource = HangingEventSource;
    vi.useFakeTimers();
    try {
      const client = new OpenCodeClient({
        baseUrl: `http://127.0.0.1:${server.port}`,
        connectTimeoutMs: 60_000,
      });
      let firstRejection: unknown;
      const first = client.connect();
      void first.catch((error: unknown) => {
        firstRejection = error;
      });
      const staleOpen = sources[0].onopen;
      const staleError = sources[0].onerror;

      const second = client.connect();
      await Promise.resolve();

      expect(firstRejection).toEqual(
        new Error("OpenCode event stream connection was superseded"),
      );
      expect(sources[0].close).toHaveBeenCalledOnce();
      expect(sources[0].onopen).toBeNull();
      expect(sources).toHaveLength(2);

      sources[1].onopen?.();
      await expect(second).resolves.toBeUndefined();
      expect(client.getStatus()).toBe("ready");

      // Callbacks captured before supersession cannot disturb the new stream.
      staleOpen?.();
      staleError?.();
      expect(client.getStatus()).toBe("ready");
      expect(vi.getTimerCount()).toBe(0);
      client.close();
    } finally {
      vi.clearAllTimers();
      vi.useRealTimers();
      if (previousEventSource === undefined) delete globals.EventSource;
      else globals.EventSource = previousEventSource;
    }
  });

  it("does not restart auto-recovery after a manual connect supersedes its handshake", async () => {
    const sources: RecoveringEventSource[] = [];
    class RecoveringEventSource {
      onopen: (() => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null;
      close = vi.fn();
      constructor(_url: string) {
        sources.push(this);
      }
    }
    const globals = globalThis as { EventSource?: unknown };
    const previousEventSource = globals.EventSource;
    globals.EventSource = RecoveringEventSource;
    vi.useFakeTimers();
    try {
      const client = new OpenCodeClient({
        baseUrl: `http://127.0.0.1:${server.port}`,
        connectTimeoutMs: 60_000,
      });
      const initial = client.connect();
      sources[0].onopen?.();
      await initial;

      // Drop the opened stream and let the first automatic retry begin its
      // handshake. It intentionally remains pending.
      sources[0].onerror?.();
      await vi.advanceTimersByTimeAsync(250);
      expect(sources).toHaveLength(2);

      // A manual connect wins. Rejecting the pending automatic handshake must
      // not be interpreted as another network failure that queues retry #2.
      const automatic = sources[1];
      const manual = client.connect();
      await Promise.resolve();
      expect(automatic.close).toHaveBeenCalledOnce();
      expect(sources).toHaveLength(3);
      const manualSource = sources[2];
      manualSource.onopen?.();
      await manual;
      await Promise.resolve();

      expect(client.getStatus()).toBe("ready");
      expect(vi.getTimerCount()).toBe(0);
      await vi.advanceTimersByTimeAsync(5_000);
      expect(sources).toHaveLength(3);
      expect(manualSource.close).not.toHaveBeenCalled();
      expect(client.getStatus()).toBe("ready");
      client.close();
    } finally {
      vi.clearAllTimers();
      vi.useRealTimers();
      if (previousEventSource === undefined) delete globals.EventSource;
      else globals.EventSource = previousEventSource;
    }
  });

  it("keeps the fetch fallback offline when closed during a hanging handshake", async () => {
    const hangingFetch = ((_input, init) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener(
          "abort",
          () => reject(new DOMException("Aborted", "AbortError")),
          { once: true },
        );
      })) as typeof fetch;
    const statuses: string[] = [];
    const client = new OpenCodeClient({
      baseUrl: `http://127.0.0.1:${server.port}`,
      fetchImpl: hangingFetch,
      connectTimeoutMs: 60_000,
    });
    client.onStatus((status) => statuses.push(status));

    const pending = client.connect();
    client.close();
    expect(client.getStatus()).toBe("offline");

    await expect(pending).rejects.toThrow("Aborted");
    expect(client.getStatus()).toBe("offline");
    expect(statuses).toEqual(["connecting", "offline"]);
  });

  it("ignores late frames and EOF from superseded fetch fallback streams", async () => {
    const streams = Array.from({ length: 3 }, () => {
      let controller!: ReadableStreamDefaultController<Uint8Array>;
      const body = new ReadableStream<Uint8Array>({
        start(next) {
          controller = next;
        },
      });
      return { body, controller: () => controller };
    });
    let response = 0;
    const controlledFetch = (async () =>
      new Response(streams[response++].body, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      })) as typeof fetch;
    const statuses: string[] = [];
    const events: OpenCodeEvent[] = [];
    const client = new OpenCodeClient({
      baseUrl: `http://127.0.0.1:${server.port}`,
      fetchImpl: controlledFetch,
      connectTimeoutMs: 60_000,
    });
    client.onStatus((status) => statuses.push(status));
    client.onEvent((event) => events.push(event));

    await client.connect();
    await client.connect();
    expect(client.getStatus()).toBe("ready");

    // The first response ignores AbortSignal and delivers a queued frame after
    // connection #2 is already ready. It must never reach normalize()/listeners.
    streams[0]
      .controller()
      .enqueue(
        new TextEncoder().encode(
          'data: {"type":"session.idle","properties":{"sessionID":"stale"}}\n\n',
        ),
      );
    await Promise.resolve();
    await Promise.resolve();
    expect(events).toEqual([]);
    expect(client.getStatus()).toBe("ready");

    await client.connect();
    expect(client.getStatus()).toBe("ready");
    // EOF from connection #2 arrives only after connection #3 is ready. Its
    // readStream.finally must not publish "offline" for the current stream.
    streams[1].controller().close();
    await Promise.resolve();
    await Promise.resolve();
    expect(client.getStatus()).toBe("ready");
    expect(statuses[statuses.length - 1]).toBe("ready");
    expect(statuses).not.toContain("offline");

    client.close();
    streams[2].controller().close();
    await Promise.resolve();
  });

  it("times out a hanging EventSource handshake so boot retry can continue", async () => {
    class HangingEventSource {
      onopen: (() => void) | null = null;
      onmessage: unknown = null;
      onerror: unknown = null;
      close = vi.fn();
      constructor(_url: string) {}
    }
    (globalThis as { EventSource?: unknown }).EventSource = HangingEventSource;
    try {
      const client = new OpenCodeClient({
        baseUrl: `http://127.0.0.1:${server.port}`,
        connectTimeoutMs: 10,
      });
      await expect(client.connect()).rejects.toThrow("Timed out opening OpenCode event stream");
      expect(client.getStatus()).toBe("error");
    } finally {
      delete (globalThis as { EventSource?: unknown }).EventSource;
    }
  });

  it("times out a hanging session creation request", async () => {
    const hangingFetch = ((_input, init) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      })) as typeof fetch;
    const client = new OpenCodeClient({
      baseUrl: `http://127.0.0.1:${server.port}`,
      fetchImpl: hangingFetch,
      requestTimeoutMs: 10,
    });
    await expect(client.createSession()).rejects.toThrow("Timed out waiting for OpenCode");
  });

  it("times out a hanging history request", async () => {
    const hangingFetch = ((_input, init) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      })) as typeof fetch;
    const client = new OpenCodeClient({
      baseUrl: `http://127.0.0.1:${server.port}`,
      fetchImpl: hangingFetch,
      requestTimeoutMs: 10,
    });
    await expect(client.getMessages("ses_hung")).rejects.toThrow("Timed out waiting for OpenCode");
  });
});
