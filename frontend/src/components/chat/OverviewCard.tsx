import { IconRefresh, IconSparkles } from "@tabler/icons-react";
import type { ReactNode } from "react";
import { useOverview, useRefreshOverview, useSetOverviewEnabled } from "@/api/queries";
import type { Overview } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { formatRelative } from "@/lib/format";
import { errorMessage } from "@/lib/utils";
import { CitedText } from "./AnswerParts";

function EstimateNote({ overview }: { overview: Overview }) {
  if (!overview.estimate?.remote) return null;
  return (
    <span className="text-xs text-muted-foreground">
      About {overview.estimate.tokens.toLocaleString("en-US")} tokens will be sent to your chat
      endpoint.
    </span>
  );
}

/** Short grounded summary of the space plus three suggested questions. */
export function OverviewCard({
  spaceId,
  hasReadySources,
  canAsk,
  onAsk,
}: {
  spaceId: string;
  hasReadySources: boolean;
  canAsk: boolean;
  onAsk: (question: string) => void;
}) {
  const overview = useOverview(spaceId);
  const toggle = useSetOverviewEnabled(spaceId);
  const refresh = useRefreshOverview(spaceId);

  if (overview.isPending) return <Skeleton className="h-28" />;
  if (overview.isError) {
    return (
      <p className="text-xs text-muted-foreground">
        The space overview could not be loaded. {errorMessage(overview.error)}
      </p>
    );
  }
  const o = overview.data;

  const generateButton = (label: string) => (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        size="xs"
        variant="outline"
        disabled={refresh.isPending || !canAsk}
        onClick={() => refresh.mutate()}
      >
        <IconRefresh aria-hidden />
        {label}
      </Button>
      <EstimateNote overview={o} />
    </div>
  );

  let body: ReactNode = null;
  if (o.enabled) {
    if (o.status === "pending") {
      body = (
        <div className="grid gap-2" aria-busy="true">
          <span className="sr-only" role="status">
            Writing an overview of your sources…
          </span>
          <Skeleton className="h-3.5" />
          <Skeleton className="h-3.5" />
          <Skeleton className="h-3.5 w-3/4" />
          <div className="mt-1 flex gap-2">
            <Skeleton className="h-7 w-40 rounded-full" />
            <Skeleton className="h-7 w-48 rounded-full" />
            <Skeleton className="h-7 w-36 rounded-full" />
          </div>
        </div>
      );
    } else if (o.status === "failed") {
      body = (
        <div className="grid gap-2">
          <p className="text-sm text-muted-foreground">The overview could not be written.</p>
          {generateButton("Try again")}
        </div>
      );
    } else if (o.status === "none") {
      body = hasReadySources ? (
        <div className="grid gap-2">
          <p className="text-sm text-muted-foreground">No overview yet.</p>
          {generateButton("Write overview")}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          An overview appears here when your first source is ready.
        </p>
      );
    } else {
      body = (
        <div className="grid gap-3">
          {o.sentences.length > 0 && <CitedText sentences={o.sentences} className="text-sm" />}
          {o.questions.length > 0 && (
            <div className="grid gap-1.5">
              <h4 className="text-xs font-medium text-muted-foreground">Suggested questions</h4>
              <ul className="flex flex-wrap gap-1.5">
                {o.questions.map((q) => (
                  <li key={q}>
                    <button
                      type="button"
                      disabled={!canAsk}
                      data-testid="suggested-question"
                      onClick={() => onAsk(q)}
                      className="rounded-full border bg-background px-3 py-1 text-left text-sm transition-colors hover:border-primary/50 hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {q}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-muted-foreground">
            {o.generated_at && <span>Written {formatRelative(o.generated_at)}</span>}
            {o.stale && (
              <>
                <span>Your sources changed since then.</span>
                {generateButton("Refresh")}
              </>
            )}
          </div>
        </div>
      );
    }
  }

  return (
    <Card className="gap-3 p-4" aria-labelledby="overview-title" data-testid="overview-card">
      <div className="flex items-center justify-between gap-3">
        <h3 id="overview-title" className="flex items-center gap-1.5 text-sm font-semibold">
          <IconSparkles className="size-4 text-primary" aria-hidden />
          Space overview
        </h3>
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          {o.enabled ? "On" : "Off"}
          <Switch
            checked={o.enabled}
            disabled={toggle.isPending}
            onCheckedChange={(v) => toggle.mutate(v)}
            aria-label="Show space overview"
          />
        </label>
      </div>
      {body}
      {(refresh.error || toggle.error) && (
        <p role="alert" className="text-xs text-destructive">
          {errorMessage(refresh.error ?? toggle.error)}
        </p>
      )}
    </Card>
  );
}
