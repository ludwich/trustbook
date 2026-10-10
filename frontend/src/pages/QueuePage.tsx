import {
  IconArrowDown,
  IconArrowUp,
  IconListCheck,
  IconPlayerPause,
  IconPlayerPlay,
  IconX,
} from "@tabler/icons-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { useJobAction, useJobs, useReorderJobs } from "@/api/queries";
import type { Job, JobStatus, QueueSummary } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { PageHeader } from "@/components/common/PageHeader";
import { QueryError } from "@/components/common/QueryError";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { formatEta, formatTime } from "@/lib/format";
import { errorMessage } from "@/lib/utils";

const UNFINISHED: JobStatus[] = ["running", "queued", "paused"];

const STATUS: Record<
  JobStatus,
  { label: string; variant: "info" | "muted" | "success" | "danger" | "warning" | "secondary" }
> = {
  running: { label: "Running", variant: "info" },
  queued: { label: "Waiting", variant: "secondary" },
  paused: { label: "Paused", variant: "warning" },
  done: { label: "Done", variant: "success" },
  failed: { label: "Failed", variant: "danger" },
  cancelled: { label: "Cancelled", variant: "muted" },
};

function sortUnfinished(a: Job, b: Job): number {
  const rank = (j: Job) => (j.status === "running" ? 0 : j.status === "queued" ? 1 : 2);
  return rank(a) - rank(b) || a.position - b.position;
}

export function QueuePage() {
  const jobs = useJobs();
  const action = useJobAction();
  const reorder = useReorderJobs();

  const all = jobs.data?.jobs ?? [];
  const unfinished = all.filter((j) => UNFINISHED.includes(j.status)).sort(sortUnfinished);
  const finished = all
    .filter((j) => !UNFINISHED.includes(j.status))
    .sort((a, b) => (b.finished_at ?? b.created_at).localeCompare(a.finished_at ?? a.created_at));
  const queued = unfinished.filter((j) => j.status === "queued");

  const move = (job: Job, delta: -1 | 1) => {
    const ids = queued.map((j) => j.id);
    const i = ids.indexOf(job.id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j]!, ids[i]!];
    reorder.mutate(ids);
  };

  const busy = action.isPending || reorder.isPending;
  const mutationError = action.error ?? reorder.error;

  return (
    <div className="mx-auto w-full max-w-5xl px-5 py-6">
      <PageHeader
        title="Queue"
        description="Work across all spaces. The space you have open goes first."
      />

      {jobs.isPending && <Skeleton className="h-32" />}
      {jobs.isError && (
        <QueryError
          title="Could not load the queue"
          error={jobs.error}
          onRetry={() => void jobs.refetch()}
        />
      )}

      {jobs.data && (
        <div className="grid gap-6">
          <Summary summary={jobs.data.summary} />

          {mutationError && (
            <p role="alert" className="text-sm text-destructive">
              {errorMessage(mutationError)}
            </p>
          )}

          <section aria-labelledby="queue-active">
            <h2 id="queue-active" className="mb-2 text-sm font-semibold">
              In progress and waiting
            </h2>
            {unfinished.length === 0 ? (
              <EmptyState icon={<IconListCheck />} title="Nothing to do">
                New work shows up here when you add sources.
              </EmptyState>
            ) : (
              <ul className="divide-y rounded-lg border bg-card" aria-label="Unfinished jobs">
                {unfinished.map((job) => {
                  const qi = queued.findIndex((q) => q.id === job.id);
                  return (
                    <JobRow key={job.id} job={job}>
                      {job.status === "queued" && (
                        <>
                          <Button
                            size="icon-xs"
                            variant="ghost"
                            aria-label={`Move ${job.title} up`}
                            title="Move up"
                            disabled={busy || qi <= 0}
                            onClick={() => move(job, -1)}
                          >
                            <IconArrowUp aria-hidden />
                          </Button>
                          <Button
                            size="icon-xs"
                            variant="ghost"
                            aria-label={`Move ${job.title} down`}
                            title="Move down"
                            disabled={busy || qi === queued.length - 1}
                            onClick={() => move(job, 1)}
                          >
                            <IconArrowDown aria-hidden />
                          </Button>
                        </>
                      )}
                      {job.status === "paused" ? (
                        <Button
                          size="xs"
                          variant="outline"
                          disabled={busy}
                          onClick={() => action.mutate({ id: job.id, action: "resume" })}
                        >
                          <IconPlayerPlay aria-hidden />
                          Resume
                        </Button>
                      ) : (
                        <Button
                          size="xs"
                          variant="outline"
                          disabled={busy}
                          onClick={() => action.mutate({ id: job.id, action: "pause" })}
                        >
                          <IconPlayerPause aria-hidden />
                          Pause
                        </Button>
                      )}
                      <Button
                        size="xs"
                        variant="ghost"
                        className="text-muted-foreground hover:text-destructive"
                        disabled={busy}
                        onClick={() => action.mutate({ id: job.id, action: "cancel" })}
                      >
                        <IconX aria-hidden />
                        Cancel
                      </Button>
                    </JobRow>
                  );
                })}
              </ul>
            )}
          </section>

          {finished.length > 0 && (
            <section aria-labelledby="queue-finished">
              <h2 id="queue-finished" className="mb-2 text-sm font-semibold">
                Recently finished
              </h2>
              <ul className="divide-y rounded-lg border bg-card" aria-label="Finished jobs">
                {finished.map((job) => (
                  <JobRow key={job.id} job={job} />
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
    </div>
  );
}

function Summary({ summary }: { summary: QueueSummary }) {
  const eta = formatEta(summary.eta_seconds);
  const items = [
    [`${summary.running}`, "running"],
    [`${summary.queued}`, "waiting"],
    [`${summary.paused}`, "paused"],
    [`${summary.workers}`, summary.workers === 1 ? "worker" : "workers"],
  ] as const;
  return (
    <div
      className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border bg-card px-4 py-3 text-sm"
      aria-label="Queue summary"
      role="group"
    >
      {items.map(([n, label]) => (
        <div key={label} className="flex items-baseline gap-1.5">
          <span className="text-base font-semibold tabular-nums">{n}</span>
          <span className="text-muted-foreground">{label}</span>
        </div>
      ))}
      {eta && <div className="text-muted-foreground">{eta[0]!.toUpperCase() + eta.slice(1)}</div>}
      <Link to="/settings#power" className="ml-auto text-xs text-primary hover:underline">
        Change processing power
      </Link>
    </div>
  );
}

function JobRow({ job, children }: { job: Job; children?: ReactNode }) {
  const status = STATUS[job.status];
  const showProgress = job.status === "running" || (job.progress !== null && job.status !== "done");
  const eta = UNFINISHED.includes(job.status) ? formatEta(job.eta_seconds) : null;
  return (
    <li className="flex items-center gap-4 px-4 py-3" data-testid={`job-${job.id}`}>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate font-medium">{job.title}</span>
          <Badge variant={status.variant}>{status.label}</Badge>
        </div>
        <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-muted-foreground">
          {job.space_id && job.space_name ? (
            <Link to={`/spaces/${encodeURIComponent(job.space_id)}`} className="hover:underline">
              {job.space_name}
            </Link>
          ) : (
            <span>All spaces</span>
          )}
          <span>{job.kind}</span>
          <span>
            {job.finished_at
              ? `Finished ${formatTime(job.finished_at)}`
              : job.started_at
                ? `Started ${formatTime(job.started_at)}`
                : `Added ${formatTime(job.created_at)}`}
          </span>
          {eta && <span data-testid={`job-eta-${job.id}`}>{eta}</span>}
        </div>
        {showProgress && (
          <div className="mt-2 flex items-center gap-2">
            <Progress
              value={job.progress}
              label={`Progress of ${job.title}`}
              className="max-w-md"
            />
            {job.progress !== null && (
              <span className="text-xs text-muted-foreground tabular-nums">
                {Math.round(job.progress * 100)}%
              </span>
            )}
          </div>
        )}
        {job.error && <p className="mt-1 text-xs text-destructive">{job.error}</p>}
      </div>
      {children && <div className="flex shrink-0 items-center gap-1">{children}</div>}
    </li>
  );
}
