import { createBrowserRouter, Navigate, type RouteObject } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { productRoutes } from "@/product/manifest";

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/research" replace /> },
      {
        path: "live",
        lazy: async () => ({ Component: (await import("./routes/LiveSessionPage")).LiveSessionPage }),
      },
      {
        path: "live/:sessionId",
        lazy: async () => ({ Component: (await import("./routes/LiveSessionPage")).LiveSessionPage }),
      },
      {
        path: "example/:sessionId",
        lazy: async () => ({ Component: (await import("./routes/SessionPage")).SessionPage }),
      },
      {
        path: "skills",
        lazy: async () => ({ Component: (await import("./routes/SkillsPage")).SkillsPage }),
      },
      {
        path: "notebooks",
        lazy: async () => ({ Component: (await import("./routes/NotebooksPage")).NotebooksPage }),
      },
      {
        path: "files",
        lazy: async () => ({ Component: (await import("./routes/FilesPage")).FilesPage }),
      },
      {
        path: "runs",
        lazy: async () => ({ Component: (await import("./routes/RunsPage")).RunsPage }),
      },
      ...productRoutes,
      {
        path: "settings",
        lazy: async () => ({ Component: (await import("./routes/SettingsPage")).SettingsPage }),
      },
      {
        path: "*",
        lazy: async () => ({ Component: (await import("./routes/NotFound")).NotFound }),
      },
    ],
  },
];

export const router = createBrowserRouter(routes);
