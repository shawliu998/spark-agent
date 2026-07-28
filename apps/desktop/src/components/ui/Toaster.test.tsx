import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { useToastStore } from "@/lib/toast";
import { Toaster } from "./Toaster";

describe("Toaster accessibility", () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [] });
  });

  it("keeps a live region mounted before dynamically inserting the first message", () => {
    render(<Toaster />);
    const region = screen.getByRole("status");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toHaveAttribute("aria-atomic", "false");
    expect(region).toBeEmptyDOMElement();
    act(() => useToastStore.getState().push("info", "Save canceled"));
    expect(screen.getByRole("button", { name: "Save canceled" })).not.toHaveAttribute("aria-live");
  });
});
