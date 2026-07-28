import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useUiStore } from "@/lib/store";
import { renderAt } from "@/test/render";

// COPYCAT RULE: useUiStore is module-global; reset the locale after each test
// so this suite never bleeds a non-English locale into other test files.
afterEach(() => {
  useUiStore.getState().setLocale("en");
  vi.restoreAllMocks();
});

describe("Sidebar i18n", () => {
  it("renders migrated nav labels and section heading in English", async () => {
    renderAt("/files");

    const nav = await screen.findByRole("navigation");
    expect(within(nav).getByText("Workspace")).toBeInTheDocument();
    expect(within(nav).getByText("Research tools")).toBeInTheDocument();
    expect(within(nav).getByText("New agent session")).toBeInTheDocument();
    expect(within(nav).getByText("Files")).toBeInTheDocument();
    expect(screen.getByText("History")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();
  });

  it("keeps the global navigation visible on compact research windows", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "matchMedia").mockImplementation((query) => ({
      matches: query.includes("max-width"),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    useUiStore.setState({ sidebarCollapsed: false });

    renderAt("/research");

    const expandButton = await screen.findByRole("button", { name: "Expand sidebar" });
    const sidebar = document.querySelector("aside");
    expect(sidebar).toHaveAttribute("aria-hidden", "true");
    expect(sidebar).toHaveAttribute("inert");
    expect(screen.queryByRole("navigation", { name: "Primary navigation" })).not.toBeInTheDocument();

    await user.click(expandButton);

    const nav = await screen.findByRole("navigation", { name: "Primary navigation" });
    expect(within(nav).getByRole("button", { name: "Research" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(sidebar as HTMLElement).getByRole("button", { name: "Collapse sidebar" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Expand sidebar" })).not.toBeInTheDocument();
  });
});
