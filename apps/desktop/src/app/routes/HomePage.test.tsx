import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { HomePage } from "./HomePage";

describe("HomePage", () => {
  it("presents a real project entry point and the local demo", () => {
    window.localStorage.setItem("spark.onboarding.v1", "complete");
    render(<MemoryRouter><HomePage /></MemoryRouter>);
    expect(screen.getByRole("button", { name: "New Research Project" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Folder" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Climate Trends Demo" })).toBeInTheDocument();
  });
});
