import { ProjectArtifactContinuity } from "./ProjectArtifactContinuity";

/** Project-bound, read-only notebooks recovered from durable Science Core state. */
export function NotebooksPage() {
  // eslint-disable-next-line i18next/no-literal-string -- internal view discriminant
  return <ProjectArtifactContinuity mode="notebooks" />;
}
