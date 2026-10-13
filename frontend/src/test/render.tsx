import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { AppRoutes } from "@/App";
import { ThemeProvider } from "@/components/layout/ThemeProvider";

export function testQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

/** Shows the current path (with any query) so tests can assert navigation. */
export function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="location">{loc.pathname + loc.search + loc.hash}</div>;
}

function Providers({
  children,
  client,
  route,
}: {
  children: ReactNode;
  client: QueryClient;
  route: string;
}) {
  return (
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

/** Render a single component with providers. */
export function renderWithProviders(
  ui: ReactElement,
  {
    route = "/",
    client = testQueryClient(),
    ...opts
  }: { route?: string; client?: QueryClient } & RenderOptions = {},
) {
  return {
    client,
    ...render(
      <Providers client={client} route={route}>
        {ui}
        <Routes>
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </Providers>,
      opts,
    ),
  };
}

/** Render the whole app (routes + layout) at a path. */
export function renderApp(route = "/", client = testQueryClient()) {
  return {
    client,
    ...render(
      <Providers client={client} route={route}>
        <AppRoutes />
        <LocationProbe />
      </Providers>,
    ),
  };
}
