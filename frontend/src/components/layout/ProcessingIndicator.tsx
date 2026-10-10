import { IconLoader2, IconPlayerPause } from "@tabler/icons-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { activeJobCount, useJobs } from "@/api/queries";
import { formatEta, plural } from "@/lib/format";

/** "Processing: N jobs, about M min left" — hidden when the queue is idle. */
export function ProcessingIndicator() {
  const { data } = useJobs();
  const active = activeJobCount(data);
  const paused = data?.summary.paused ?? 0;

  let content: ReactNode = null;
  if (active > 0) {
    const eta = formatEta(data?.summary.eta_seconds);
    content = (
      <Link
        to="/queue"
        className="inline-flex items-center gap-1.5 rounded-full bg-info-soft px-3 py-1 text-xs font-medium text-foreground transition-colors hover:bg-accent"
      >
        <IconLoader2
          className="size-3.5 animate-spin text-primary motion-reduce:animate-none"
          aria-hidden
        />
        <span>
          Processing: {plural(active, "job")}
          {eta ? `, ${eta}` : ""}
        </span>
      </Link>
    );
  } else if (paused > 0) {
    content = (
      <Link
        to="/queue"
        className="inline-flex items-center gap-1.5 rounded-full bg-muted px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent"
      >
        <IconPlayerPause className="size-3.5" aria-hidden />
        <span>{plural(paused, "job")} paused</span>
      </Link>
    );
  }

  return (
    <div aria-live="polite" data-testid="processing-indicator">
      {content}
    </div>
  );
}
