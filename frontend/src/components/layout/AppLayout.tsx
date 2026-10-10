import type { ReactNode } from "react";
import { Navigate, Outlet, useLocation } from "react-router";
import { useSetupStatus } from "@/api/queries";
import { QueryError } from "@/components/common/QueryError";
import { Spinner } from "@/components/ui/spinner";
import { Header } from "./Header";

/** Header on every page, plus the first-start redirect to the setup wizard. */
export function AppLayout() {
  const setup = useSetupStatus();
  const location = useLocation();

  let body: ReactNode;
  if (setup.isPending) {
    body = (
      <div className="flex justify-center py-24">
        <Spinner className="size-6 text-muted-foreground" />
      </div>
    );
  } else if (setup.isError) {
    body = (
      <div className="mx-auto max-w-xl py-16">
        <QueryError
          title="Can't reach Trustbook"
          error={setup.error}
          onRetry={() => void setup.refetch()}
        />
      </div>
    );
  } else if (!setup.data.completed && location.pathname !== "/setup") {
    body = <Navigate to="/setup" replace />;
  } else {
    body = <Outlet />;
  }

  return (
    <div className="flex min-h-svh flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-card focus:px-3 focus:py-2 focus:shadow"
      >
        Skip to content
      </a>
      <Header />
      <main id="main" className="flex flex-1 flex-col">
        {body}
      </main>
    </div>
  );
}
