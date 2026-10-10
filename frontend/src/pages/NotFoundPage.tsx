import { Link } from "react-router";
import { EmptyState } from "@/components/common/EmptyState";
import { Button } from "@/components/ui/button";

export function NotFoundPage() {
  return (
    <div className="mx-auto w-full max-w-xl px-5 py-16">
      <EmptyState
        title="Page not found"
        action={
          <Button asChild variant="outline">
            <Link to="/">Back to spaces</Link>
          </Button>
        }
      >
        The address may be wrong or the page has moved.
      </EmptyState>
    </div>
  );
}
