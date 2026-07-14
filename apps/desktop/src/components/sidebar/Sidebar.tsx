import { useState } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { Files, FlaskConical, FolderTree, NotebookPen, PanelLeft, Plus, Settings, Trash2 } from "lucide-react";
import type { Project } from "@ai4s/shared";
import { cn } from "@/lib/cn";
import { useRuntimeStore } from "@/lib/runtime";
import { SIDEBAR_MAX, SIDEBAR_MIN, useOverlayTitlebar, useUiStore } from "@/lib/store";
import { useUpdateStore } from "@/lib/update";
import { StatusPills } from "./StatusPills";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { PRODUCT } from "@/config/product";
import { productNavigation } from "@/product/manifest";
import logo from "@/assets/logo.webp";

interface Row {
  id: string;
  title: string;
  to: string;
  kind: "session" | "example";
}

/** Dragging the divider below this pointer x collapses the sidebar; dragging
 *  back past it re-expands. Sits below SIDEBAR_MIN so there is a clear "snap". */
const COLLAPSE_BELOW = 140;

export function Sidebar({ project }: { project: Project }) {
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

  const onDividerPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
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
    navigate("/live");
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

  const confirmDelete = () => {
    const row = pendingDelete;
    setPendingDelete(null);
    if (!row) return;
    if (row.kind === "session") void deleteSession(row.id);
    else hideExample(row.id);
    if (location.pathname === row.to) navigate("/live");
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
        !dragging && "transition-[width] duration-200 ease-out",
      )}
      style={{ width: sidebarCollapsed ? 0 : width }}
    >
      <aside
        className="flex h-full flex-col border-r border-border bg-surface"
        style={{ width }}
      >
      {/* The strip clears the traffic lights and hosts the collapse button just
          right of them — same spot the expand button lands when collapsed. */}
      {overlayTitlebar && (
        <div data-tauri-drag-region className="flex h-12 shrink-0 items-center pl-[78px]">
          <button
            onClick={toggleSidebar}
            aria-label={t("sidebar.collapse")}
            title={t("sidebar.collapseTitle", { shortcut: "⌘B" })}
            className="rounded p-1 text-text hover:bg-surface-2"
          >
            <PanelLeft size={14} strokeWidth={1.5} />
          </button>
        </div>
      )}
      <div className={cn("px-4 pb-3", overlayTitlebar ? "pt-1" : "pt-4")}>
        <div className="flex items-baseline gap-1.5">
          <img src={logo} alt="" className="h-[18px] w-auto self-center" />
          {/* eslint-disable-next-line i18next/no-literal-string -- product brand name, not translated across locales (see AGENTS.md) */}
          <div className="font-serif text-[17px] font-semibold leading-none tracking-tight text-text">
            {PRODUCT.name}
          </div>
          <span className="text-[10px] uppercase tracking-widest text-muted">{t("sidebar.betaBadge")}</span>
          {!overlayTitlebar && (
            <button
              onClick={toggleSidebar}
              aria-label={t("sidebar.collapse")}
              title={t("sidebar.collapseTitle", { shortcut: isMac ? "⌘B" : "Ctrl+B" })}
              className="ml-auto self-center rounded p-1 text-text hover:bg-surface-2"
            >
              <PanelLeft size={14} strokeWidth={1.5} />
            </button>
          )}
        </div>
      </div>

      <nav className="flex flex-col px-3">
        <NavRow icon={<Plus size={16} />} label={t("items.new")} onClick={startNew} />
        {productNavigation.map((item) => {
          const Icon = item.icon;
          return (
            <NavRow
              key={item.id}
              icon={<Icon size={16} />}
              label={t(item.labelKey)}
              onClick={() => navigate(item.path)}
            />
          );
        })}
        <NavRow icon={<NotebookPen size={16} />} label={t("items.notebooks")} onClick={() => navigate("/notebooks")} />
        <NavRow icon={<FolderTree size={16} />} label={t("items.files")} onClick={() => navigate("/files")} />
        <NavRow icon={<FlaskConical size={16} />} label={t("items.runs")} onClick={() => navigate("/runs")} />
        <NavRow icon={<Files size={16} />} label={t("items.skills")} onClick={() => navigate("/skills")} />
      </nav>

      <div className="mt-4 flex-1 overflow-y-auto px-3 pb-2">
        <div className="px-2 py-1 text-xs font-medium uppercase tracking-wider text-muted">{t("history.heading")}</div>
        {rows.length === 0 && (
          <div className="px-2 py-2 text-xs text-muted">{t("history.empty")}</div>
        )}
        {rows.map((row) => (
          <div key={row.to} className="group relative">
            <NavLink
              to={row.to}
              className={cn(
                "flex items-center gap-2 rounded-input py-1 pl-2 pr-8 text-[13px] hover:bg-surface-2",
                location.pathname === row.to ? "bg-surface-2 text-text" : "text-text/90",
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
                <span className="shrink-0 rounded-full bg-surface-2 px-1.5 text-[10px] uppercase tracking-wide text-muted ring-1 ring-border">
                  {t("history.exampleTag")}
                </span>
              )}
            </NavLink>
            <button
              onClick={() => setPendingDelete(row)}
              aria-label={t("history.deleteAria", { title: row.title })}
              className="absolute right-1.5 top-1/2 hidden -translate-y-1/2 rounded p-1 text-muted hover:bg-border hover:text-error group-hover:block"
            >
              <Trash2 size={13} />
            </button>
          </div>
        ))}
      </div>

      <div className="border-t border-border px-3 py-3">
        <StatusPills />
        <button
          className="relative mt-2 flex items-center gap-2 rounded-input px-2 py-1 text-[13px] text-muted hover:bg-surface-2 hover:text-text"
          onClick={() => navigate("/settings")}
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
          "group absolute inset-y-0 right-0 z-10 w-[5px] cursor-col-resize",
          sidebarCollapsed && !dragging && "pointer-events-none",
        )}
      >
        <div
          className={cn(
            "absolute inset-y-0 right-0 w-[2px] transition-colors",
            dragging ? "bg-accent/60" : "bg-transparent group-hover:bg-accent/40",
          )}
        />
      </div>
    </div>
  );
}

function NavRow({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-2 rounded-input px-2 py-1 text-[13px] text-text hover:bg-surface-2"
    >
      <span className="text-muted">{icon}</span>
      <span>{label}</span>
    </button>
  );
}
