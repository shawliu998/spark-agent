import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Outlet, useLocation } from "react-router-dom";
import { PanelLeft } from "lucide-react";
import { cn } from "@/lib/cn";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { CommandPalette } from "@/components/command-palette/CommandPalette";
import { Toaster } from "@/components/ui/Toaster";
import { mockProject } from "@/lib/mock";
import { useRuntimeStore } from "@/lib/runtime";
import { ensureSetupProgressListener } from "@/lib/setup";
import { useOverlayTitlebar, useUiStore } from "@/lib/store";
import { openExternal, reconcileJupyter, watchFullscreen } from "@/lib/tauri";
import { toast } from "@/lib/toast";
import { useUpdateStore } from "@/lib/update";

let desktopServicesStart: Promise<void> | null = null;

/** Reconcile native-owned Jupyter state exactly once before OpenCode can read
 * its config. Sharing this promise also makes React StrictMode remounts safe. */
export function ensureDesktopServicesStarted(): Promise<void> {
  if (!desktopServicesStart) {
    desktopServicesStart = (async () => {
      try {
        await reconcileJupyter();
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        const message =
          `Spark Agent blocked runtime startup because Jupyter security reconciliation failed: ${detail}`;
        useRuntimeStore.setState({ status: "error", error: message });
        toast.error(message);
        return;
      }
      await useRuntimeStore.getState().bootstrap();
    })();
  }
  return desktopServicesStart;
}

export function AppShell() {
  const { t } = useTranslation("nav");
  const { sidebarCollapsed, setSidebarCollapsed } = useUiStore();

  // Cmd/Ctrl+B toggles the sidebar, matching the button's tooltip.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "b") {
        e.preventDefault();
        useUiStore.getState().toggleSidebar();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  // Native reconciliation must finish before OpenCode can read its config.
  // The shared startup promise prevents StrictMode from racing two launches.
  useEffect(() => {
    void ensureDesktopServicesStarted();
    // One app-lifetime listener for uv provisioning progress, so a running
    // download's live output survives navigating between pages.
    ensureSetupProgressListener();
    if (!import.meta.env.TEST) {
      void useUpdateStore.getState().maybeAutoCheck();
    }
  }, []);

  // Track native fullscreen: macOS hides the traffic lights there, so headers
  // must drop their traffic-light inset (see useOverlayTitlebar).
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    void watchFullscreen((fs) => useUiStore.getState().setIsFullscreen(fs)).then((u) => {
      if (cancelled) u();
      else unlisten = u;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  // External links open in the system browser. Navigating the webview away
  // from the app would strand the user — there is no back button.
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      const anchor = (e.target as HTMLElement).closest?.("a[href]");
      const href = anchor?.getAttribute("href") ?? "";
      if (/^https?:\/\//i.test(href)) {
        e.preventDefault();
        void openExternal(href);
      }
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);

  // The live session page's own header doubles as the titlebar when the
  // sidebar is collapsed; every other route gets this fallback strip so the
  // macOS traffic lights don't overlap content, the window stays draggable,
  // and the sidebar can be re-expanded.
  const isMac = navigator.userAgent.includes("Mac");
  const overlayTitlebar = useOverlayTitlebar();
  const pageOwnsTitlebar = useLocation().pathname.startsWith("/live");

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-bg text-text">
      <Sidebar project={mockProject} />
      <main className="flex min-w-0 flex-1 flex-col">
        {sidebarCollapsed && !pageOwnsTitlebar && (
          <div
            data-tauri-drag-region={overlayTitlebar || undefined}
            className={cn(
              "flex h-12 shrink-0 items-center",
              overlayTitlebar ? "pl-[78px]" : "pl-2",
            )}
          >
            <button
              onClick={() => setSidebarCollapsed(false)}
              aria-label={t("sidebar.expand")}
              title={t("sidebar.expandTitle", { shortcut: isMac ? "⌘B" : "Ctrl+B" })}
              className="fade-in rounded p-1 text-text hover:bg-surface-2"
            >
              <PanelLeft size={14} strokeWidth={1.5} />
            </button>
          </div>
        )}
        <div className="min-h-0 flex-1">
          <Outlet />
        </div>
      </main>
      <CommandPalette />
      <Toaster />
    </div>
  );
}
