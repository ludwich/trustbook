import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5_000,
        refetchOnWindowFocus: true,
        // Don't retry client errors (404 etc.); retry network/server errors briefly.
        retry: (count, error) =>
          !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
      },
    },
  });
}
