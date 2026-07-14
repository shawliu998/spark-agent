import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useSourcePdfBlob } from "./useSourcePdfBlob";

const core = vi.hoisted(() => ({ fetchSourceBlob: vi.fn() }));

vi.mock("@/lib/scienceCore", () => ({ scienceCore: core }));

const createObjectUrl = vi.fn();
const revokeObjectUrl = vi.fn();
const originalCreateObjectUrl = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const originalRevokeObjectUrl = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: createObjectUrl,
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: revokeObjectUrl,
  });
  createObjectUrl
    .mockReturnValueOnce("blob:source-one")
    .mockReturnValueOnce("blob:source-two");
  core.fetchSourceBlob.mockResolvedValue(
    new Blob(["pdf"], { type: "application/pdf" }),
  );
});

afterEach(() => {
  if (originalCreateObjectUrl) {
    Object.defineProperty(URL, "createObjectURL", originalCreateObjectUrl);
  } else {
    Reflect.deleteProperty(URL, "createObjectURL");
  }
  if (originalRevokeObjectUrl) {
    Object.defineProperty(URL, "revokeObjectURL", originalRevokeObjectUrl);
  } else {
    Reflect.deleteProperty(URL, "revokeObjectURL");
  }
});

describe("useSourcePdfBlob", () => {
  it("reuses a source blob across pages and revokes URLs when ownership ends", async () => {
    const { result, rerender, unmount } = renderHook(
      ({ sourceId, pageIndex }) => useSourcePdfBlob(sourceId, pageIndex),
      { initialProps: { sourceId: "source-1", pageIndex: 2 } },
    );

    await waitFor(() => expect(result.current.url).toBe("blob:source-one#page=3"));
    expect(core.fetchSourceBlob).toHaveBeenCalledTimes(1);

    rerender({ sourceId: "source-1", pageIndex: 4 });
    expect(result.current.url).toBe("blob:source-one#page=5");
    expect(core.fetchSourceBlob).toHaveBeenCalledTimes(1);

    rerender({ sourceId: "source-2", pageIndex: 0 });
    await waitFor(() => expect(result.current.url).toBe("blob:source-two#page=1"));
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:source-one");

    unmount();
    expect(revokeObjectUrl).toHaveBeenLastCalledWith("blob:source-two");
  });
});
