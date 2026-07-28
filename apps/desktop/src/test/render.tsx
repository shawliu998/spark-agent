import { render } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { routes } from "@/app/router";
import { RouteLoading } from "@/app/routes/RouteLoading";

/** Render the whole app at a given route, using an in-memory router. */
export function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return Object.assign(
    render(<RouterProvider router={router} fallbackElement={<RouteLoading />} />),
    { router },
  );
}
