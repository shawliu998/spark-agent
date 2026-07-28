import { BarChart3, Library, type LucideIcon } from "lucide-react";
import type { RouteObject } from "react-router-dom";

export interface ProductNavigationItem {
  id: string;
  labelKey: "items.research" | "items.analysis";
  path: string;
  icon: LucideIcon;
}

export const productRoutes: RouteObject[] = [
  {
    path: "research",
    lazy: async () => ({ Component: (await import("@/app/routes/ResearchPage")).ResearchPage }),
  },
  {
    path: "analysis",
    lazy: async () => ({ Component: (await import("@/app/routes/AnalysisPage")).AnalysisPage }),
  },
];

export const productNavigation: ProductNavigationItem[] = [
  { id: "research", labelKey: "items.research", path: "/research", icon: Library },
  { id: "analysis", labelKey: "items.analysis", path: "/analysis", icon: BarChart3 },
];
