import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { HomePage } from "./HomePage";
import { useRuntimeStore } from "@/lib/runtime";

describe("HomePage", () => {
  it("presents a real project entry point and the local demo", () => {
    window.localStorage.setItem("spark.onboarding.v1", "complete");
    render(<MemoryRouter><HomePage /></MemoryRouter>);
    expect(screen.getByRole("button", { name: "New Research Project" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Folder" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Synthetic Research Demo" })).toBeInTheDocument();
  });

  it("shows a starting model label while the runtime is connecting", () => {
    useRuntimeStore.setState({ status: "connecting", defaultModel: null });
    window.localStorage.setItem("spark.onboarding.v1", "complete");
    render(<MemoryRouter><HomePage /></MemoryRouter>);
    expect(screen.getByText("Starting…")).toBeInTheDocument();
  });

  it("keeps the not-connected model label when the runtime is offline", () => {
    useRuntimeStore.setState({ status: "offline", defaultModel: null });
    window.localStorage.setItem("spark.onboarding.v1", "complete");
    render(<MemoryRouter><HomePage /></MemoryRouter>);
    expect(screen.getByText("Not connected")).toBeInTheDocument();
  });
});
