import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useRuntimeStore } from "@/lib/runtime";
import { useUiStore } from "@/lib/store";
import { renderAt } from "@/test/render";

// COPYCAT RULE: useUiStore is module-global; reset the locale after each test
// so this suite never bleeds a non-English locale into other test files.
afterEach(() => {
  useUiStore.getState().setLocale("en");
  useRuntimeStore.getState().startDraft();
});

describe("Sidebar i18n", () => {
  it("renders migrated nav labels and section heading in English", async () => {
    renderAt("/files");

    const nav = await screen.findByRole("navigation");
    expect(within(nav).getByText("Home")).toBeInTheDocument();
    expect(within(nav).getByText("Files")).toBeInTheDocument();
    expect(within(nav).getByText("Verified Workflows")).toBeInTheDocument();
    expect(screen.getByText("History")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();
  });

  it("keeps New as a new General Research session rather than opening Home", async () => {
    useRuntimeStore.setState({ currentId: "ses_open" });
    const view = renderAt("/home");
    const nav = await screen.findByRole("navigation");
    fireEvent.click(within(nav).getByText("New"));

    expect(view.router.state.location.pathname).toBe("/live");
    expect(useRuntimeStore.getState().currentId).toBeNull();
  });
});
