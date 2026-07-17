import { useCallback, useEffect, useMemo, useState } from "react";
import { Download, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { readArtifact } from "@/lib/artifactFile";
import {
  LAB_NOTEBOOK_PATH,
  labNotebookMarkdown,
  labNotebookTypes,
  parseLabNotebook,
  type LabNotebookType,
} from "@/lib/labNotebook";

function download(filename: string, content: string, mime: string) {
  const anchor = document.createElement("a");
  anchor.href = URL.createObjectURL(new Blob([content], { type: mime }));
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(anchor.href);
}

export function LabNotebookPage() {
  const { t } = useTranslation("pages");
  const [source, setSource] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [type, setType] = useState<LabNotebookType | "all">("all");
  const [sessionId, setSessionId] = useState("all");

  const refresh = useCallback(async () => {
    const file = await readArtifact(LAB_NOTEBOOK_PATH, "workspace");
    setSource(file?.encoding === "utf8" ? file.data : "");
    setLoaded(true);
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const parsed = useMemo(() => parseLabNotebook(source), [source]);
  const sessions = useMemo(
    () =>
      [...new Set(parsed.entries.map((entry) => entry.sessionId).filter((id): id is string => Boolean(id)))].sort(),
    [parsed.entries],
  );
  const entries = useMemo(
    () =>
      parsed.entries.filter(
        (entry) =>
          (type === "all" || entry.type === type) &&
          (sessionId === "all" || entry.sessionId === sessionId),
      ),
    [parsed.entries, sessionId, type],
  );

  const buttonClass = "flex items-center gap-1.5 rounded-input border border-border bg-surface px-2.5 py-1.5 text-xs text-text hover:bg-surface-2";
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl px-8 py-6">
        <div className="flex items-center gap-3">
          <div>
            <h1 className="font-serif text-xl text-text">{t("labNotebook.title")}</h1>
            <p className="mt-1 text-sm text-muted">{t("labNotebook.description")}</p>
          </div>
          <div className="flex-1" />
          <button className={buttonClass} onClick={() => void refresh()} aria-label={t("labNotebook.refreshAria")}>
            <RefreshCw size={13} /> {t("labNotebook.refresh")}
          </button>
          <button className={buttonClass} disabled={!entries.length} onClick={() => download("lab-notebook.md", labNotebookMarkdown(entries), "text/markdown;charset=utf-8")}>
            <Download size={13} /> {t("labNotebook.exportMarkdown")}
          </button>
          <button className={buttonClass} disabled={!entries.length} onClick={() => download("lab-notebook.json", JSON.stringify(entries, null, 2), "application/json;charset=utf-8")}>
            <Download size={13} /> {t("labNotebook.exportJson")}
          </button>
        </div>
        <p className="mt-2 text-xs text-muted">{t("labNotebook.path")}: <code>{LAB_NOTEBOOK_PATH}</code></p>
        <div className="mt-5 flex flex-wrap gap-2">
          <label className="text-xs text-muted">{t("labNotebook.type")} <select value={type} onChange={(event) => setType(event.target.value as LabNotebookType | "all")} className="ml-1 rounded border border-border bg-surface px-2 py-1 text-text"><option value="all">{t("labNotebook.all")}</option>{labNotebookTypes.map((kind) => <option key={kind} value={kind}>{kind}</option>)}</select></label>
          <label className="text-xs text-muted">{t("labNotebook.session")} <select value={sessionId} onChange={(event) => setSessionId(event.target.value)} className="ml-1 rounded border border-border bg-surface px-2 py-1 text-text"><option value="all">{t("labNotebook.all")}</option>{sessions.map((id) => <option key={id} value={id}>{id}</option>)}</select></label>
        </div>
        {parsed.warnings.map((warning) => <p key={warning} role="alert" className="mt-3 rounded border border-warning/40 bg-warning/10 p-2 text-xs text-text">{warning}</p>)}
        <div className="mt-4 space-y-3">{loaded && entries.length === 0 && <div className="rounded-card border border-border bg-surface p-5 text-sm text-muted">{t("labNotebook.empty")}</div>}{entries.map((entry) => <article key={entry.id} className="rounded-card border border-border bg-surface p-4"><div className="flex gap-2 text-xs"><span className="rounded bg-surface-2 px-1.5 py-0.5 text-text">{entry.type}</span><time className="text-muted">{new Date(entry.timestamp).toLocaleString()}</time>{entry.sessionId && <span className="ml-auto text-muted">{entry.sessionId}</span>}</div><p className="mt-2 whitespace-pre-wrap text-sm text-text">{entry.content}</p>{entry.evidence?.length ? <ul className="mt-2 text-xs text-muted">{entry.evidence.map((item) => <li key={item.path}>{item.label ?? item.path} <code>{item.path}</code></li>)}</ul> : null}</article>)}</div>
      </div>
    </div>
  );
}
