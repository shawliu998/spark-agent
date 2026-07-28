import { beforeEach, describe, expect, it, vi } from "vitest";
import { saveBinaryWithFeedback, saveTextWithFeedback } from "./download";
import { useToastStore } from "./toast";

const tauri = vi.hoisted(() => ({
  saveBinaryFile: vi.fn(),
  saveTextFile: vi.fn(),
}));
vi.mock("./tauri", () => ({
  saveBinaryFile: tauri.saveBinaryFile,
  saveTextFile: tauri.saveTextFile,
}));

describe("saveTextWithFeedback", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useToastStore.setState({ toasts: [] });
  });

  it("keeps native Save As cancellation silent for legacy callers without localized copy", async () => {
    tauri.saveTextFile.mockResolvedValue({ kind: "canceled" });
    await saveTextWithFeedback("report.md", "report");
    expect(useToastStore.getState().toasts).toEqual([]);
  });

  it("surfaces a native write failure", async () => {
    tauri.saveTextFile.mockRejectedValue(new Error("disk is read-only"));
    await saveTextWithFeedback("report.md", "report");
    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({ tone: "error", message: "Could not save report.md: disk is read-only" }),
    ]);
  });

  it("uses caller-provided localized Save As feedback", async () => {
    tauri.saveTextFile.mockResolvedValue({ kind: "canceled" });
    await saveTextWithFeedback("report.md", "report", "text/markdown", {
      saved: (path) => `已保存到 ${path}`,
      downloaded: (filename) => `已下载 ${filename}`,
      canceled: (filename) => `已取消保存 ${filename}`,
      failed: (filename, error) => `无法保存 ${filename}：${error}`,
    });
    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({ tone: "info", message: "已取消保存 report.md" }),
    ]);
  });

  it("saves binary output through the native Save As bridge", async () => {
    tauri.saveBinaryFile.mockResolvedValue({
      kind: "saved",
      path: "/tmp/report.docx",
    });

    await saveBinaryWithFeedback(
      "report.docx",
      new Uint8Array([80, 75, 3, 4]),
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    );

    expect(tauri.saveBinaryFile).toHaveBeenCalledWith(
      "report.docx",
      new Uint8Array([80, 75, 3, 4]),
    );
    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({
        tone: "success",
        message: "Saved to /tmp/report.docx",
      }),
    ]);
  });
});
