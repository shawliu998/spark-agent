import { BarChart3, Library, type LucideIcon } from "lucide-react";
import type { RouteObject } from "react-router-dom";
import { AnalysisPage } from "@/app/routes/AnalysisPage";
import { ResearchPage } from "@/app/routes/ResearchPage";

export interface ProductNavigationItem {
  id: string;
  labelKey: "items.research" | "items.analysis";
  path: string;
  icon: LucideIcon;
}

export const productRoutes: RouteObject[] = [
  { path: "research", element: <ResearchPage /> },
  { path: "analysis", element: <AnalysisPage /> },
];

export const productNavigation: ProductNavigationItem[] = [
  { id: "research", labelKey: "items.research", path: "/research", icon: Library },
  { id: "analysis", labelKey: "items.analysis", path: "/analysis", icon: BarChart3 },
];
