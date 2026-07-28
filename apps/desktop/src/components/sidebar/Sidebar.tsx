import { useState } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  ChevronDown,
  Files,
  FlaskConical,
  FolderTree,
  MessageSquarePlus,
  NotebookPen,
  PanelLeft,
  Settings,
  Trash2,
} from "lucide-react";
import type { Project } from "@ai4s/shared";
import { cn } from "@/lib/cn";
import { useRuntimeStore } from "@/lib/runtime";
import { SIDEBAR_MAX, SIDEBAR_MIN, useOverlayTitlebar, useUiStore } from "@/lib/store";
import { useUpdateStore } from "@/lib/update";
import { StatusPills } from "./StatusPills";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { productNavigation } from "@/product/manifest";
import sparkWordmark from "@/assets/spark-wordmark.png";

interface Row {
  id: string;
  title: string;
  to: string;
  kind: "session" | "example";
}

/** Dragging the divider below this pointer x collapses the sidebar; dragging
 *  back past it re-expands. Sits below SIDEBAR_MIN so there is a clear "snap". */
const COLLAPSE_BELOW = 140;
const NOTEBOOKS_PATH = "/notebooks";
const FILES_PATH = "/files";
const RUNS_PATH = "/runs";
const SKILLS_PATH = "/skills";

interface SidebarProps {
  project: Project;
  compactOverlay?: boolean;
  compactOpen?: boolean;
  onCompactOpenChange?: (open: boolean) => void;
}

export function Sidebar({
  project,
  compactOverlay = false,
  compactOpen = false,
  onCompactOpenChange = () => {},
}: SidebarProps) {
  const { t } = useTranslation("nav");
  const navigate = useNavigate();
  const location = useLocation();
  const { sessions, hiddenExamples, startDraft, deleteSession, hideExample } = useRuntimeStore();
  const showUpdateBadge = useUpdateStore((s) => s.showBadge);
  const { sidebarCollapsed, sidebarWidth, setSidebarCollapsed, setSidebarWidth, toggleSidebar } =
    useUiStore();
  // While dragging, the live width lives here; the store (and localStorage)
  // are only written on pointer-up.
  const [dragWidth, setDragWidth] = useState<number | null>(null);
  const dragging = dragWidth !== null;
  const visuallyCollapsed = compactOverlay ? !compactOpen : sidebarCollapsed;
  const collapseSidebar = () => {
    if (compactOverlay) onCompactOpenChange(false);
    else toggleSidebar();
  };
  const navigateTo = (path: string) => {
    navigate(path);
    if (compactOverlay) onCompactOpenChange(false);
  };

  const onDividerPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (compactOverlay) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    setDragWidth(sidebarWidth);
  };

  const onDividerPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    // The sidebar starts at the window's left edge, so clientX is the width.
    const x = e.clientX;
    if (x < COLLAPSE_BELOW) {
      if (!sidebarCollapsed) setSidebarCollapsed(true);
      return;
    }
    if (sidebarCollapsed) setSidebarCollapsed(false);
    setDragWidth(Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, x)));
  };

  const onDividerPointerUp = () => {
    if (!dragging) return;
    setSidebarWidth(dragWidth);
    setDragWidth(null);
  };

  const startNew = () => {
    startDraft();
    navigateTo("/live");
  };

  const rows: Row[] = [
    // Subagent child sessions are internals of their parent conversation —
    // their asks and progress surface there, so they get no row of their own.
    ...sessions
      .filter((s) => !s.parentId)
      .map((s) => ({ id: s.id, title: s.title, to: `/live/${s.id}`, kind: "session" as const })),
    ...project.sessions
      .filter((e) => !hiddenExamples.includes(e.id))
      .map((e) => ({ id: e.id, title: e.title, to: `/example/${e.id}`, kind: "example" as const })),
  ];

  const [pendingDelete, setPendingDelete] = useState<Row | null>(null);
  const historyContext =
    location.pathname.startsWith("/live") || location.pathname.startsWith("/example")
      ? "agent"
      : "workspace";
  const [historyByContext, setHistoryByContext] = useState({ agent: true, workspace: false });
  const historyExpanded = historyByContext[historyContext];

  const confirmDelete = () => {
    const row = pendingDelete;
    setPendingDelete(null);
    if (!row) return;
    if (row.kind === "session") void deleteSession(row.id);
    else hideExample(row.id);
    if (location.pathname === row.to) navigateTo("/live");
  };

  // With the overlay titlebar (macOS), reserve a draggable strip at the top so
  // the traffic lights don't overlap the logo and the window stays movable.
  const isMac = navigator.userAgent.includes("Mac");
  const overlayTitlebar = useOverlayTitlebar();

  const width = dragWidth ?? sidebarWidth;

  return (
    <div
      className={cn(
        "relative h-full shrink-0 overflow-hidden",
        compactOverlay && "absolute inset-y-0 left-0 z-50 shadow-pop",
        !dragging && "transition-[width] duration-200 ease-out",
      )}
      style={{ width: visuallyCollapsed ? 0 : width }}
    >
      <aside
        className="global-sidebar flex h-full flex-col border-r border-border bg-surface"
        style={{ width }}
        aria-hidden={visuallyCollapsed || undefined}
        {...(visuallyCollapsed ? { inert: "" } : {})}
      >
      {/* The strip clears the traffic lights and hosts the collapse button just
          right of them — same spot the expand button lands when collapsed. */}
      {overlayTitlebar && (
        <div data-tauri-drag-region className="flex h-12 shrink-0 items-center pl-[76px]">
          <button
            onClick={collapseSidebar}
            aria-label={t("sidebar.collapse")}
            title={t("sidebar.collapseTitle", { shortcut: "⌘B" })}
            className="touch-target flex h-10 w-10 items-center justify-center rounded-input text-muted hover:bg-surface-2 hover:text-text"
          >
            <PanelLeft size={16} strokeWidth={1.6} />
          </button>
        </div>
      )}
      <div className={cn("px-4 pb-4", overlayTitlebar ? "pt-1" : "pt-4")}>
        <div className="flex items-center gap-2">
          <img
            src={sparkWordmark}
            alt={t("sidebar.productName", { defaultValue: "Spark" })}
            className="h-[19px] w-auto shrink-0 object-contain dark:invert"
          />
          <span className="text-caption font-medium text-muted">{t("sidebar.betaBadge")}</span>
          {!overlayTitlebar && (
            <button
              onClick={collapseSidebar}
              aria-label={t("sidebar.collapse")}
              title={t("sidebar.collapseTitle", { shortcut: isMac ? "⌘B" : "Ctrl+B" })}
              className="touch-target ml-auto flex h-10 w-10 items-center justify-center rounded-input text-muted hover:bg-surface-2 hover:text-text"
            >
              <PanelLeft size={16} strokeWidth={1.6} />
            </button>
          )}
        </div>
      </div>

      <nav className="global-sidebar-nav flex min-h-0 shrink flex-col gap-5 overflow-y-auto px-3" aria-label={t("groups.primary")}>
        <NavGroup label={t("groups.workspace")}>
          {productNavigation.map((item) => {
            const Icon = item.icon;
            return (
              <NavRow
                key={item.id}
                icon={<Icon size={16} />}
                label={t(item.labelKey)}
                onClick={() => navigateTo(item.path)}
                active={location.pathname.startsWith(item.path)}
              />
            );
          })}
        </NavGroup>

        <NavGroup label={t("groups.resources")}>
          <NavRow
            icon={<NotebookPen size={16} />}
            label={t("items.notebooks")}
            onClick={() => navigateTo(NOTEBOOKS_PATH)}
            active={location.pathname.startsWith("/notebooks")}
          />
          <NavRow
            icon={<FolderTree size={16} />}
            label={t("items.files")}
            onClick={() => navigateTo(FILES_PATH)}
            active={location.pathname.startsWith("/files")}
          />
          <NavRow
            icon={<FlaskConical size={16} />}
            label={t("items.runs")}
            onClick={() => navigateTo(RUNS_PATH)}
            active={location.pathname.startsWith("/runs")}
          />
        </NavGroup>

        <NavGroup label={t("groups.agent")}>
          <NavRow
            icon={<MessageSquarePlus size={16} />}
            label={t("items.agentSession")}
            onClick={startNew}
            active={location.pathname === "/live"}
          />
          <NavRow
            icon={<Files size={16} />}
            label={t("items.skills")}
            onClick={() => navigateTo(SKILLS_PATH)}
            active={location.pathname.startsWith("/skills")}
          />
        </NavGroup>
      </nav>

      <div className="mt-6 flex min-h-14 flex-1 flex-col overflow-hidden border-t border-border-faint px-3 pb-2 pt-4">
        <button
          type="button"
          onClick={() =>
            setHistoryByContext((expanded) => ({
              ...expanded,
              [historyContext]: !expanded[historyContext],
            }))
          }
          aria-expanded={historyExpanded}
          className="touch-target flex min-h-10 shrink-0 items-center gap-1.5 rounded-input px-2 text-xs font-medium text-muted hover:bg-surface-2 hover:text-text"
        >
          <ChevronDown
            size={12}
            className={cn("transition-transform", !historyExpanded && "-rotate-90")}
          />
          <span>{t("history.heading")}</span>
          {rows.length > 0 && <span className="ml-auto tabular-nums">{rows.length}</span>}
        </button>
        {historyExpanded && (
          <div className="min-h-0 flex-1 overflow-y-auto pt-1">
            {rows.length === 0 && (
              <div className="px-2 py-2 text-xs text-muted">{t("history.empty")}</div>
            )}
            {rows.map((row) => (
              <div key={row.to} className="group relative">
                <NavLink
                  to={row.to}
                  onClick={() => compactOverlay && onCompactOpenChange(false)}
                  className={cn(
                    "touch-target flex min-h-10 items-center gap-2 rounded-input py-2 pl-2 pr-10 text-xs hover:bg-surface-2",
                    location.pathname === row.to
                      ? "bg-surface-2 text-text"
                      : "text-text/90",
                  )}
                >
                  <span
                    className={cn(
                      "h-1.5 w-1.5 shrink-0 rounded-full",
                      row.kind === "example" ? "bg-muted" : "bg-ok",
                    )}
                  />
                  <span className="flex-1 truncate">{row.title}</span>
                  {row.kind === "example" && (
                    <span className="shrink-0 rounded-full bg-surface-2 px-1.5 text-caption text-muted ring-1 ring-border">
                      {t("history.exampleTag")}
                    </span>
                  )}
                </NavLink>
                <button
                  onClick={() => setPendingDelete(row)}
                  aria-label={t("history.deleteAria", { title: row.title })}
                  className="touch-target absolute right-1 top-1/2 hidden h-10 w-10 -translate-y-1/2 items-center justify-center rounded-input text-muted hover:bg-border hover:text-error focus:flex group-focus-within:flex group-hover:flex"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="border-t border-border px-3 py-3">
        <StatusPills />
        <button
          className="touch-target relative mt-2 flex min-h-10 w-full items-center gap-2 rounded-input px-2 py-2 text-xs text-muted hover:bg-surface-2 hover:text-text"
          onClick={() => navigateTo("/settings")}
          aria-label={t("sidebar.settings")}
        >
          <Settings size={15} />
          <span>{t("sidebar.settings")}</span>
          {showUpdateBadge && (
            <span
              aria-hidden="true"
              className="ml-auto h-2 w-2 rounded-full bg-error shadow-[0_0_0_2px_var(--color-surface)]"
            />
          )}
        </button>
      </div>

      {pendingDelete && (
        <ConfirmDialog
          title={
            pendingDelete.kind === "session"
              ? t("confirmDelete.sessionTitle")
              : t("confirmDelete.exampleTitle")
          }
          body={
            pendingDelete.kind === "session"
              ? t("confirmDelete.sessionBody", { title: pendingDelete.title })
              : t("confirmDelete.exampleBody", { title: pendingDelete.title })
          }
          confirmLabel={
            pendingDelete.kind === "session"
              ? t("confirmDelete.deleteAction")
              : t("confirmDelete.hideAction")
          }
          onConfirm={confirmDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
      </aside>

      {/* Drag divider: resize within [SIDEBAR_MIN, SIDEBAR_MAX]; dragging far
          left snaps the sidebar closed. Kept mounted while collapsed so an
          in-flight drag (pointer capture) can re-open it. */}
      <div
        onPointerDown={onDividerPointerDown}
        onPointerMove={onDividerPointerMove}
        onPointerUp={onDividerPointerUp}
        onPointerCancel={onDividerPointerUp}
        className={cn(
          "group absolute inset-y-0 right-0 z-10 w-2 cursor-col-resize",
          compactOverlay && "hidden",
          sidebarCollapsed && !dragging && "pointer-events-none",
        )}
      >
        <div
          className={cn(
            "absolute inset-y-0 right-0 w-px transition-colors",
            dragging ? "bg-accent/60" : "bg-transparent group-hover:bg-accent/40",
          )}
        />
      </div>
    </div>
  );
}

function NavRow({
  icon,
  label,
  onClick,
  active = false,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  active?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={cn(
        "global-sidebar-row touch-target flex min-h-12 w-full items-center gap-2 rounded-input px-2 py-2.5 text-left text-xs transition-colors",
        active
          ? "bg-surface-2 font-medium text-text"
          : "text-text hover:bg-surface-2",
      )}
    >
      <span className={active ? "text-accent" : "text-muted"}>{icon}</span>
      <span>{label}</span>
    </button>
  );
}

function NavGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section aria-label={label}>
      <p className="global-sidebar-group-label px-2 pb-3 text-xs font-medium text-muted">
        {label}
      </p>
      <div className="flex flex-col gap-0.5">{children}</div>
    </section>
  );
}
