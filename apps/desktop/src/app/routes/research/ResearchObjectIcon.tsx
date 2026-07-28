import {
  Activity,
  Box,
  ClipboardCheck,
  File,
  FileChartColumn,
  FileText,
  History,
  Image,
  LibraryBig,
  NotebookTabs,
  PackageCheck,
  Quote,
  ScanText,
  ScrollText,
  Sigma,
  Table2,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/cn";

export type ResearchObjectKind =
  | "activity"
  | "artifact"
  | "citation"
  | "dataset"
  | "environment"
  | "evidence"
  | "figure"
  | "log"
  | "memory"
  | "method"
  | "notebook"
  | "pdf"
  | "reproducibility"
  | "result"
  | "review"
  | "run"
  | "table";

export const RESEARCH_OBJECT_KIND = {
  activity: "activity",
  artifact: "artifact",
  citation: "citation",
  dataset: "dataset",
  environment: "environment",
  evidence: "evidence",
  figure: "figure",
  log: "log",
  memory: "memory",
  method: "method",
  notebook: "notebook",
  pdf: "pdf",
  reproducibility: "reproducibility",
  result: "result",
  review: "review",
  run: "run",
  table: "table",
} as const satisfies Record<ResearchObjectKind, ResearchObjectKind>;

const icons: Record<ResearchObjectKind, LucideIcon> = {
  activity: History,
  artifact: File,
  citation: Quote,
  dataset: Table2,
  environment: Box,
  evidence: ScanText,
  figure: Image,
  log: ScrollText,
  memory: LibraryBig,
  method: Sigma,
  notebook: NotebookTabs,
  pdf: FileText,
  reproducibility: PackageCheck,
  result: FileChartColumn,
  review: ClipboardCheck,
  run: Activity,
  table: Table2,
};

export function ResearchObjectIcon({
  kind,
  size = 14,
  className,
}: {
  kind: ResearchObjectKind;
  size?: number;
  className?: string;
}) {
  const Icon = icons[kind];
  return (
    <Icon
      aria-hidden={true}
      size={size}
      className={cn("shrink-0 text-muted", className)}
    />
  );
}
