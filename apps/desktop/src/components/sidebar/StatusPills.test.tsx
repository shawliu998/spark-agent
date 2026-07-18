import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusPills } from "./StatusPills";
import { useRuntimeStore } from "@/lib/runtime";

describe("StatusPills", () => {
  it("shows a starting label for the model while the runtime is connecting and no model is set", () => {
    useRuntimeStore.setState({ status: "connecting", defaultModel: null });
    render(<StatusPills />);
    expect(screen.getByText("Starting…")).toBeInTheDocument();
  });

  it("shows the configured model name once the runtime is ready", () => {
    useRuntimeStore.setState({ status: "ready", defaultModel: "openai/gpt-5" });
    render(<StatusPills />);
    expect(screen.getByText("gpt-5")).toBeInTheDocument();
  });

  it("keeps the not-set label for other statuses when no model is configured", () => {
    useRuntimeStore.setState({ status: "offline", defaultModel: null });
    render(<StatusPills />);
    expect(screen.getByText("not set")).toBeInTheDocument();
  });
});
