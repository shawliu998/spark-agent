import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const liveSessionSource = readFileSync(
  resolve(process.cwd(), "src/app/routes/LiveSessionPage.tsx"),
  "utf8",
);

describe("General Research API boundary", () => {
  it("uses the OpenCode session store without importing the autonomous workflow client", () => {
    expect(liveSessionSource).toContain("sendPrompt");
    expect(liveSessionSource).toContain("ResearchSessionControls");
    expect(liveSessionSource).not.toContain("scienceCore");
    expect(liveSessionSource).not.toContain("useResearchWorkflow");
  });

  it("routes Verified mode to the existing structured workflow surface", () => {
    expect(liveSessionSource).toContain('mode === "verified"');
    expect(liveSessionSource).toContain('navigate("/research")');
  });

  it("keeps project templates outside the Verified workflow boundary", () => {
    const projectsSource = readFileSync(resolve(process.cwd(), "src/lib/projects.ts"), "utf8");
    expect(projectsSource).not.toContain("scienceCore");
    expect(projectsSource).not.toContain("useResearchWorkflow");
  });
});
