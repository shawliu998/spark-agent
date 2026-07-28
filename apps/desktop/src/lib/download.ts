import { saveBinaryFile, saveTextFile } from "./tauri";
import { toast } from "./toast";

export interface SaveTextFeedbackMessages {
  saved: (path: string) => string;
  downloaded: (filename: string) => string;
  canceled?: (filename: string) => string;
  failed: (filename: string, error: string) => string;
}

const DEFAULT_MESSAGES: SaveTextFeedbackMessages = {
  saved: (path) => `Saved to ${path}`,
  downloaded: (filename) => `Downloaded ${filename}`,
  failed: (filename, error) => `Could not save ${filename}: ${error}`,
};

/** Save text as a file via a Blob download. No-op outside the browser. */
export function downloadText(filename: string, text: string, mime = "text/plain"): void {
  if (typeof document === "undefined" || typeof URL.createObjectURL !== "function") return;
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Save binary bytes as a browser download. No-op outside the browser. */
export function downloadBytes(
  filename: string,
  content: Uint8Array,
  mime = "application/octet-stream",
): void {
  if (typeof document === "undefined" || typeof URL.createObjectURL !== "function") return;
  const copy = new Uint8Array(content);
  const url = URL.createObjectURL(new Blob([copy.buffer], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/**
 * Save text with user feedback: native "Save As" dialog in the desktop app
 * (toast on success/failure/cancel), Blob download in the browser.
 */
export async function saveTextWithFeedback(
  filename: string,
  text: string,
  mime = "text/plain",
  messages: SaveTextFeedbackMessages = DEFAULT_MESSAGES,
): Promise<void> {
  try {
    const result = await saveTextFile(filename, text);
    if (result.kind === "saved") {
      toast.success(messages.saved(result.path));
    } else if (result.kind === "not-desktop") {
      downloadText(filename, text, mime);
      toast.success(messages.downloaded(filename));
    } else {
      const canceledMessage = messages.canceled?.(filename);
      if (canceledMessage) toast.info(canceledMessage);
    }
  } catch (err) {
    toast.error(messages.failed(filename, err instanceof Error ? err.message : String(err)));
  }
}

/** Save binary output with the same native/browser feedback contract as text. */
export async function saveBinaryWithFeedback(
  filename: string,
  content: Uint8Array,
  mime = "application/octet-stream",
  messages: SaveTextFeedbackMessages = DEFAULT_MESSAGES,
): Promise<void> {
  try {
    const result = await saveBinaryFile(filename, content);
    if (result.kind === "saved") {
      toast.success(messages.saved(result.path));
    } else if (result.kind === "not-desktop") {
      downloadBytes(filename, content, mime);
      toast.success(messages.downloaded(filename));
    } else {
      const canceledMessage = messages.canceled?.(filename);
      if (canceledMessage) toast.info(canceledMessage);
    }
  } catch (err) {
    toast.error(messages.failed(filename, err instanceof Error ? err.message : String(err)));
  }
}
