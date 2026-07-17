import type { ArtifactBlock } from "@ai4s/shared";
import { isDiscoverableArtifactName, refToArtifactBlock } from "./artifacts";
import { listDir, type DirEntry } from "./artifactFile";

/**
 * Files worth surfacing as research outputs. This intentionally mirrors the
 * existing preview registry instead of requiring a database registration: a
 * general OpenCode turn may create these through Python, shell, or a notebook.
 */
/** Directories that are implementation state, dependency caches, or VCS data. */
const IGNORED_DIRECTORIES = new Set([
  ".git",
  ".opencode",
  ".openscience",
  ".spark",
  ".venv",
  "__pycache__",
  "node_modules",
]);

function isIgnoredDirectory(path: string): boolean {
  return path.split(/[\\/]/).some((part) => IGNORED_DIRECTORIES.has(part));
}

export interface DiscoveredWorkspaceArtifact {
  block: ArtifactBlock;
  modified: number;
  size: number;
}

/** Artifacts whose project-relative path did not exist at turn start. */
export function createdWorkspaceArtifacts(
  before: Iterable<string>,
  after: DiscoveredWorkspaceArtifact[],
): DiscoveredWorkspaceArtifact[] {
  const existing = new Set(before);
  return after.filter((artifact) => !existing.has(artifact.block.path));
}

interface DiscoveryOptions {
  /** Injectable for focused tests; production uses the existing Tauri file bridge. */
  list?: (dir: string, root: "workspace") => Promise<DirEntry[]>;
  maxDepth?: number;
  maxEntries?: number;
}

/**
 * Best-effort bounded workspace scan. Results are newest first and can be
 * reconstructed after restart, so general research artifacts never depend on
 * an in-memory tool event or a science-core workflow record.
 */
export async function discoverWorkspaceArtifacts(
  options: DiscoveryOptions = {},
): Promise<DiscoveredWorkspaceArtifact[]> {
  const list = options.list ?? listDir;
  const maxDepth = options.maxDepth ?? 4;
  const maxEntries = options.maxEntries ?? 200;
  const pending: Array<{ dir: string; depth: number }> = [{ dir: "", depth: 0 }];
  const artifacts: DiscoveredWorkspaceArtifact[] = [];
  let visited = 0;

  while (pending.length > 0 && visited < maxEntries) {
    const next = pending.shift();
    if (!next) break;
    let entries: DirEntry[];
    try {
      entries = await list(next.dir, "workspace");
    } catch {
      // A file can disappear while the agent is writing it. Discovery is a
      // convenience surface and must never turn that race into a failed turn.
      continue;
    }
    for (const entry of entries) {
      if (visited++ >= maxEntries) break;
      if (entry.isDir) {
        if (next.depth < maxDepth && !isIgnoredDirectory(entry.path)) {
          pending.push({ dir: entry.path, depth: next.depth + 1 });
        }
        continue;
      }
      if (!isDiscoverableArtifactName(entry.name)) continue;
      artifacts.push({
        block: { ...refToArtifactBlock(entry.path), tool: "workspace" },
        modified: entry.modified,
        size: entry.size,
      });
    }
  }

  return artifacts.sort(
    (a, b) => b.modified - a.modified || a.block.path.localeCompare(b.block.path),
  );
}
