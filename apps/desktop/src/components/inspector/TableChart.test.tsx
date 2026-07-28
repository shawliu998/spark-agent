import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { TableChart } from "./TableChart";
import type { ParsedTable } from "@/lib/csv";

const T: ParsedTable = {
  columns: ["month", "sales", "returns"],
  rows: [
    ["Jan", "100", "5"],
    ["Feb", "120", "8"],
    ["Mar", "90", "3"],
  ],
  truncated: false,
};

describe("TableChart", () => {
  it("renders chart-type controls, an X picker, and the numeric series", () => {
    const { container } = render(<TableChart table={T} />);
    for (const t of ["line", "bar", "scatter"]) {
      expect(screen.getByRole("button", { name: t })).toBeInTheDocument();
    }
    expect(screen.getByRole("group", { name: "Chart type" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "bar" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "line" })).toHaveAttribute("aria-pressed", "false");
    // numeric series toggles present (sales, returns); the categorical "month" is not a series
    expect(screen.getByRole("button", { name: /sales/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /returns/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sales/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("img", { name: /Dataset chart/ })).toBeInTheDocument();
    // default is a bar chart (categorical X) → <rect> marks drawn
    expect(container.querySelectorAll("rect").length).toBeGreaterThan(0);
  });

  it("switches to a line chart, drawing polylines", async () => {
    const { container } = render(<TableChart table={T} />);
    await userEvent.click(screen.getByRole("button", { name: "line" }));
    expect(screen.getByRole("button", { name: "line" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "bar" })).toHaveAttribute("aria-pressed", "false");
    expect(container.querySelector("path")).not.toBeNull();
  });

  it("keeps missing values as line gaps and series colors bound to column identity", async () => {
    const table: ParsedTable = {
      columns: ["month", "sales", "returns"],
      rows: [["Jan", "100", "5"], ["Feb", "", "8"], ["Mar", "90", "3"]],
      truncated: false,
    };
    const { container } = render(<TableChart table={table} />);
    const returnsButton = screen.getByRole("button", { name: /returns/ });
    const returnsColor = returnsButton.querySelector("span")?.style.background;
    await userEvent.click(screen.getByRole("button", { name: "line" }));
    const salesPath = container.querySelectorAll("path")[0];
    expect(salesPath?.getAttribute("d")?.match(/M/g)).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: /sales/ }));
    expect(returnsButton.querySelector("span")?.style.background).toBe(returnsColor);
  });

  it("shows a message when there is nothing numeric to plot", () => {
    const t: ParsedTable = { columns: ["a", "b"], rows: [["x", "y"]], truncated: false };
    render(<TableChart table={t} />);
    expect(screen.getByText(/No numeric columns to chart/)).toBeInTheDocument();
  });
});
