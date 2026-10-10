import { IconRefresh } from "@tabler/icons-react";
import { useCalibration, useRunCalibration } from "@/api/queries";
import type { Calibration } from "@/api/types";
import { QueryError } from "@/components/common/QueryError";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { formatDateTime, formatNumber } from "@/lib/format";
import { errorMessage } from "@/lib/utils";
import { SettingsSection } from "./SettingsSection";

function rows(c: Calibration): [string, string][] {
  return [
    ["CPU cores", String(c.cpu_count)],
    [
      "Indexing",
      c.embedding_chunks_per_s === null
        ? "Not measured"
        : `${formatNumber(c.embedding_chunks_per_s)} chunks per second`,
    ],
    [
      "Ranking",
      c.rerank_pairs_per_s === null
        ? "Not measured"
        : `${formatNumber(c.rerank_pairs_per_s)} pairs per second`,
    ],
    [
      "Reading documents",
      c.parse_pages_per_s === null
        ? "Not measured"
        : `${formatNumber(c.parse_pages_per_s)} pages per second`,
    ],
    [
      "Speech to text",
      c.stt_realtime_factor === null
        ? "Not measured"
        : `${formatNumber(c.stt_realtime_factor, 2)} × real time`,
    ],
  ];
}

export function CalibrationSection() {
  const cal = useCalibration();
  const run = useRunCalibration();
  const status = cal.data?.status;
  const running = status === "running" || status === "pending" || run.isPending;

  return (
    <SettingsSection
      id="calibration"
      title="Speed test"
      description="Measured on first start. Used to estimate how long new sources take."
      actions={
        <Button size="sm" variant="outline" onClick={() => run.mutate()} disabled={running}>
          {running ? <Spinner label="Running" /> : <IconRefresh aria-hidden />}
          Run again
        </Button>
      }
    >
      {cal.isPending && <Skeleton className="h-28" />}
      {cal.isError && <QueryError error={cal.error} onRetry={() => void cal.refetch()} />}
      {run.isError && (
        <p role="alert" className="mb-2 text-sm text-destructive">
          Could not start the speed test: {errorMessage(run.error)}
        </p>
      )}
      {cal.data && (
        <div className="rounded-lg border bg-card px-4 py-3">
          <div className="mb-2 flex items-center gap-2 text-sm">
            {status === "done" && <Badge variant="success">Done</Badge>}
            {status === "running" && <Badge variant="info">Running…</Badge>}
            {status === "pending" && <Badge variant="secondary">Waiting to run</Badge>}
            {status === "failed" && <Badge variant="danger">Failed</Badge>}
            {cal.data.results && (
              <span className="text-xs text-muted-foreground">
                Measured {formatDateTime(cal.data.results.measured_at)}
              </span>
            )}
          </div>
          {cal.data.results ? (
            <>
              <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">
                {rows(cal.data.results).map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-muted-foreground">{k}</dt>
                    <dd className="tabular-nums">{v}</dd>
                  </div>
                ))}
              </dl>
              {cal.data.results.notes.length > 0 && (
                <ul className="mt-2 list-disc pl-5 text-xs text-muted-foreground">
                  {cal.data.results.notes.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            <p className="text-sm text-muted-foreground">No results yet.</p>
          )}
        </div>
      )}
    </SettingsSection>
  );
}
