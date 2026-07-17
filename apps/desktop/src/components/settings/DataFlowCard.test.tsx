import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DataFlowCard } from "./DataFlowCard";

describe("DataFlowCard", () => {
  it("states both sides of the data flow with the active model", () => {
    render(<DataFlowCard model="anthropic/claude" workspace="/Users/x/OpenScience" />);
    expect(screen.getByText("Stays on this machine")).toBeInTheDocument();
    expect(screen.getByText(/Sent to your model provider/)).toBeInTheDocument();
    expect(screen.getByText("anthropic/claude")).toBeInTheDocument();
    expect(screen.getByText(/\/Users\/x\/OpenScience/)).toBeInTheDocument();
    expect(screen.getByText(/Materials Project\/FRED connector keys/)).toBeInTheDocument();
    expect(screen.getByText(/migration and private-broker infrastructure are implemented/i)).toBeInTheDocument();
    expect(screen.getByText(/legacy DYLD-sensitive Spark launcher is removed/i)).toBeInTheDocument();
    expect(screen.getByText(/managed entries are disabled by default.*execution fails closed.*Security gated/i)).toBeInTheDocument();
    expect(screen.getByText(/Apple platform-signed \/usr\/bin\/nc -U.*private Unix-domain socket/i)).toBeInTheDocument();
    expect(screen.getByText(/Tauri broker.*currently owned OpenCode PID\/start time\/generation/i)).toBeInTheDocument();
    expect(screen.getByText(/staged defense in depth, not a delivered claim/i)).toBeInTheDocument();
    expect(screen.getByText(/P0.*same-UID mutation.*native per-call approval.*config-dependency approval bypass/i)).toBeInTheDocument();
    expect(screen.getByText(/P1.*fully hashed transitive lock with staged atomic install/i)).toBeInTheDocument();
    expect(screen.getByText(/P1.*packaged macOS E2E/i)).toBeInTheDocument();
    expect(screen.getByText(/OAuth records.*Jupyter token/)).toBeInTheDocument();
    expect(screen.getByText(/custom\/BYO MCP credentials.*outside this boundary/i)).toBeInTheDocument();
    expect(screen.getByText(/broader execution-time isolation remain open/i)).toBeInTheDocument();
    expect(screen.queryByText(/key never crosses|enabled runtime still uses the connector/i)).not.toBeInTheDocument();
    // The copy must never promise perfection — it states scope, not guarantees.
    expect(screen.queryByText(/no errors|zero hallucination/i)).not.toBeInTheDocument();
  });

  it("shows the unconfigured state without a workspace path", () => {
    render(<DataFlowCard model={null} workspace={null} />);
    expect(screen.getByText("no model configured")).toBeInTheDocument();
  });
});
