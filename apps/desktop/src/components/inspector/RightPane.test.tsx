import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { INSPECTOR_MIN, useUiStore } from "@/lib/store";
import { MaximizePaneButton, RightPane } from "./RightPane";

describe("RightPane resize separator", () => {
  beforeEach(() => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1200,
    });
    useUiStore.setState({
      inspectorMaximized: false,
      inspectorWidth: 480,
    });
  });

  it("exposes the persisted width and keyboard resize bounds", () => {
    render(
      <RightPane onClose={vi.fn()}>
        <div>Inspector content</div>
      </RightPane>,
    );

    const separator = screen.getByRole("separator", { name: "Resize inspector pane" });
    expect(separator).toHaveAttribute("aria-orientation", "vertical");
    expect(separator).toHaveAttribute("aria-valuemin", String(INSPECTOR_MIN));
    expect(separator).toHaveAttribute("aria-valuemax", "840");
    expect(separator).toHaveAttribute("aria-valuenow", "480");
    expect(separator).toHaveAttribute("aria-controls", "right-inspector-pane");
    expect(separator).toHaveAttribute("tabindex", "0");
    expect(separator.tagName).toBe("BUTTON");
    expect(separator.className).toContain("focus-visible:outline");

    fireEvent.keyDown(separator, { key: "ArrowLeft" });
    expect(separator).toHaveAttribute("aria-valuenow", "504");

    fireEvent.keyDown(separator, { key: "ArrowRight" });
    expect(separator).toHaveAttribute("aria-valuenow", "480");

    fireEvent.keyDown(separator, { key: "Home" });
    expect(separator).toHaveAttribute("aria-valuenow", String(INSPECTOR_MIN));

    fireEvent.keyDown(separator, { key: "End" });
    expect(separator).toHaveAttribute("aria-valuenow", "840");
  });

  it("ignores unrelated keys", () => {
    render(
      <RightPane onClose={vi.fn()}>
        <div>Inspector content</div>
      </RightPane>,
    );

    const separator = screen.getByRole("separator", { name: "Resize inspector pane" });
    fireEvent.keyDown(separator, { key: "Enter" });
    expect(separator).toHaveAttribute("aria-valuenow", "480");
  });

  it("keeps the maximize control at the desktop touch-target size", () => {
    render(<MaximizePaneButton />);
    expect(screen.getByRole("button", { name: "Maximize panel" })).toHaveClass("h-11", "w-11");
  });
});
