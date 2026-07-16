import { describe, expect, it, vi } from "vitest";
import type { DirEntry } from "./artifactFile";
import { discoverWorkspaceArtifacts } from "./workspaceArtifacts";

function entry(
  path: string,
  options: Partial<DirEntry> & Pick<DirEntry, "isDir">,
): DirEntry {
  return {
    path,
    name: path.split("/").pop() ?? path,
    size: 10,
    modified: 1,
    ...options,
  };
}

describe("discoverWorkspaceArtifacts", () => {
  it("finds research outputs recursively and orders them newest first", async () => {
    const trees: Record<string, DirEntry[]> = {
      "": [
        entry("analysis", { isDir: true }),
        entry("paper.pdf", { isDir: false, modified: 2 }),
        entry("notes.txt", { isDir: false, modified: 9 }),
      ],
      analysis: [
        entry("analysis/summary.csv", { isDir: false, modified: 5 }),
        entry("analysis/figure.png", { isDir: false, modified: 8 }),
        entry("analysis/run.ipynb", { isDir: false, modified: 4 }),
      ],
    };
    const list = vi.fn(async (dir: string) => trees[dir] ?? []);

    const found = await discoverWorkspaceArtifacts({ list });

    expect(found.map((item) => item.block.path)).toEqual([
      "analysis/figure.png",
      "analysis/summary.csv",
      "analysis/run.ipynb",
      "paper.pdf",
    ]);
    expect(found.map((item) => item.block.artifact)).toEqual([
      "figure",
      "table",
      "notebook",
      "report",
    ]);
  });

  it("skips dependency and app-state directories and tolerates transient read errors", async () => {
    const list = vi.fn(async (dir: string) => {
      if (!dir) {
        return [
          entry("node_modules", { isDir: true }),
          entry("results", { isDir: true }),
          entry("report.md", { isDir: false }),
        ];
      }
      throw new Error("directory changed");
    });

    await expect(discoverWorkspaceArtifacts({ list })).resolves.toMatchObject([
      { block: { path: "report.md", artifact: "report", tool: "workspace" } },
    ]);
    expect(list).not.toHaveBeenCalledWith("node_modules", "workspace");
  });

  it("bounds traversal for large workspaces", async () => {
    const list = vi.fn(async () => [
      entry("a.csv", { isDir: false }),
      entry("b.csv", { isDir: false }),
      entry("c.csv", { isDir: false }),
    ]);

    const found = await discoverWorkspaceArtifacts({ list, maxEntries: 2 });

    expect(found).toHaveLength(2);
  });

  it("reconstructs existing scientific viewer formats after restart", async () => {
    const list = vi.fn(async () => [
      entry("protein.pdb", { isDir: false }),
      entry("variants.vcf", { isDir: false }),
      entry("volume.fits", { isDir: false }),
      entry("surface.glb", { isDir: false }),
      entry("DOSCAR", { isDir: false }),
      entry("notes.txt", { isDir: false }),
    ]);

    const found = await discoverWorkspaceArtifacts({ list });

    expect(found.map((item) => item.block.path).sort()).toEqual([
      "DOSCAR",
      "protein.pdb",
      "surface.glb",
      "variants.vcf",
      "volume.fits",
    ]);
  });
});
