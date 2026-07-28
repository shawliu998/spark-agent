import { useTranslation } from "react-i18next";

export function RouteLoading() {
  const { t } = useTranslation("pages");
  return (
    <div
      className="flex h-full overflow-hidden bg-bg"
      role="status"
      aria-label={t("files.loading")}
    >
      <div
        aria-hidden="true"
        className="w-56 shrink-0 animate-pulse border-r border-border bg-surface p-4 motion-reduce:animate-none"
      >
        <div className="h-5 w-20 rounded-input bg-surface-2" />
        <div className="mt-8 space-y-3">
          <div className="h-9 rounded-input bg-surface-2" />
          <div className="h-9 rounded-input bg-surface-2" />
          <div className="h-9 rounded-input bg-surface-2" />
        </div>
      </div>
      <div aria-hidden="true" className="min-w-0 flex-1 animate-pulse motion-reduce:animate-none">
        <div className="h-12 border-b border-border bg-surface" />
        <div className="mx-auto max-w-3xl space-y-4 px-8 py-10">
          <div className="h-5 w-56 rounded-input bg-surface-2" />
          <div className="h-3 w-full rounded-input bg-surface-2" />
          <div className="h-3 w-4/5 rounded-input bg-surface-2" />
        </div>
      </div>
    </div>
  );
}
