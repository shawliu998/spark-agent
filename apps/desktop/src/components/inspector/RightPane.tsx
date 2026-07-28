import { useEffect, useState } from "react";
import { Maximize2, Minimize2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { INSPECTOR_MAX, INSPECTOR_MIN, useOverlayTitlebar, useUiStore } from "@/lib/store";
import { cn } from "@/lib/cn";

/** Dragging the divider below this pane width closes the pane — the same
 *  snap-shut behaviour as the sidebar. Sits below INSPECTOR_MIN for a clear snap. */
const COLLAPSE_BELOW = 280;

/** The pane may never squeeze the conversation out on small windows. */
const MAX_FRACTION = 0.7;

/**
 * Resizable right pane hosting an inspector or the session Files browser.
 * The left-edge divider drags within [INSPECTOR_MIN, INSPECTOR_MAX] (persisted);
 * dragging it far right snaps the pane closed. Maximized, the pane covers the
 * whole window — sidebar and conversation stay mounted underneath.
 */
export function RightPane({
  children,
  onClose,
}: {
  children: React.ReactNode;
  onClose: () => void;
}) {
  const { t } = useTranslation(["inspector", "common"]);
  const { inspectorWidth, inspectorMaximized, setInspectorWidth, setInspectorMaximized } =
    useUiStore();
  // While dragging, the live width lives here; the store (and localStorage)
  // are only written on pointer-up.
  const [dragWidth, setDragWidth] = useState<number | null>(null);
  const dragging = dragWidth !== null;

  // Maximized never outlives the pane — closing it returns the next pane
  // (possibly for a different artifact or session) to the normal split.
  useEffect(() => () => setInspectorMaximized(false), [setInspectorMaximized]);

  const clamp = (w: number) =>
    Math.max(
      INSPECTOR_MIN,
      Math.min(w, INSPECTOR_MAX, Math.round(window.innerWidth * MAX_FRACTION)),
    );

  const onDividerPointerDown = (e: React.PointerEvent<HTMLButtonElement>) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    setDragWidth(inspectorWidth);
  };

  const onDividerPointerMove = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (!dragging) return;
    // The pane ends at the window's right edge, so the width is whatever is
    // right of the pointer.
    const w = window.innerWidth - e.clientX;
    if (w < COLLAPSE_BELOW) {
      // Snap closed — the pane unmounts, which also ends the drag.
      setDragWidth(null);
      onClose();
      return;
    }
    setDragWidth(clamp(w));
  };

  const onDividerPointerUp = () => {
    if (!dragging) return;
    setInspectorWidth(dragWidth);
    setDragWidth(null);
  };

  const maxWidth = () => clamp(INSPECTOR_MAX);
  const onDividerKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    const step = 24;
    const current = clamp(inspectorWidth);
    const next =
      event.key === "ArrowLeft" ? clamp(current + step) :
      event.key === "ArrowRight" ? clamp(current - step) :
      event.key === "Home" ? INSPECTOR_MIN :
      event.key === "End" ? maxWidth() :
      null;
    if (next === null) return;
    event.preventDefault();
    setInspectorWidth(next);
  };

  if (inspectorMaximized) {
    // The pane header stays the top row — PaneTitlebarInset (rendered inside
    // each header) clears the macOS traffic lights, so no extra strip here.
    return <div className="fixed inset-0 z-40 bg-surface">{children}</div>;
  }

  return (
    <div
      className="relative hidden h-full shrink-0 lg:block"
      style={{ width: dragWidth ?? inspectorWidth }}
    >
      <div id="right-inspector-pane" className="h-full">{children}</div>
      {/* Drag divider: resize within [INSPECTOR_MIN, INSPECTOR_MAX]; dragging
          far right snaps the pane closed. */}
      <button
        type="button"
        role="separator"
        aria-orientation="vertical"
        aria-label={t("shell.resizePanel")}
        aria-controls="right-inspector-pane"
        aria-valuemin={INSPECTOR_MIN}
        aria-valuemax={maxWidth()}
        aria-valuenow={clamp(inspectorWidth)}
        tabIndex={0}
        onKeyDown={onDividerKeyDown}
        onPointerDown={onDividerPointerDown}
        onPointerMove={onDividerPointerMove}
        onPointerUp={onDividerPointerUp}
        onPointerCancel={onDividerPointerUp}
        className="group absolute inset-y-0 left-0 z-10 w-[5px] cursor-col-resize border-0 bg-transparent p-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <span
          aria-hidden="true"
          className={cn(
            "absolute inset-y-0 left-0 w-[2px] transition-colors",
            dragging ? "bg-accent/60" : "bg-transparent group-hover:bg-accent/40",
          )}
        />
      </button>
    </div>
  );
}

/** Spacer at the start of a pane header row: when the pane is maximized on
 *  macOS its header becomes the window's top row, so this clears the native
 *  traffic lights (keeping everything on one line) and lets them drag the
 *  window. Renders nothing otherwise. */
export function PaneTitlebarInset() {
  const inspectorMaximized = useUiStore((s) => s.inspectorMaximized);
  const overlayTitlebar = useOverlayTitlebar();
  if (!inspectorMaximized || !overlayTitlebar) return null;
  // Headers pad 16px (px-4); the lights need ~78px clear in total.
  return <div data-tauri-drag-region className="w-[62px] shrink-0 self-stretch" />;
}

/** Maximize / restore toggle for the pane's header row (session pages only —
 *  full-page viewers like the Files page have nothing to maximize over). */
export function MaximizePaneButton() {
  const { t } = useTranslation(["inspector", "common"]);
  const inspectorMaximized = useUiStore((s) => s.inspectorMaximized);
  const setInspectorMaximized = useUiStore((s) => s.setInspectorMaximized);
  const label = inspectorMaximized ? t("shell.restorePanel") : t("shell.maximizePanel");
  return (
    <button
      className="flex h-11 w-11 items-center justify-center text-text hover:opacity-60"
      aria-label={label}
      title={label}
      onClick={() => setInspectorMaximized(!inspectorMaximized)}
    >
      {inspectorMaximized ? (
        <Minimize2 size={14} strokeWidth={1.5} />
      ) : (
        <Maximize2 size={14} strokeWidth={1.5} />
      )}
    </button>
  );
}
