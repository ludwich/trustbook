import { IconRefresh } from "@tabler/icons-react";
import { useCalibration, useRunCalibration } from "@/api/queries";
import type { Calibration } from "@/api/types";
import { QueryError } from "@/components/common/QueryError";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { formatComputer, formatDateTime, formatNumber, formatRealtimeFactor } from "@/lib/format";
import { errorMessage } from "@/lib/utils";
import { SettingsSection } from "./SettingsSection";

function perSecond(n: number | null, unit: string): string {
  return n === null ? "Not measured" : `${formatNumber(n)} ${unit} per second`;
}

function rows(c: Calibration): [string, string][] {
  return [
    // Older results (before v3) have no CPU details; keep the plain core count for them.
    ...(c.cpu ? [] : [["CPU cores", String(c.cpu_count)] as [string, string]]),
    ["Indexing", perSecond(c.embedding_chunks_per_s, "chunks")],
    // Calibration v5+ measures code separately (bigger chunks, parallel streams).
    ...(c.embedding_chunks_per_s_code === undefined
      ? []
      : [
          ["Indexing code", perSecond(c.embedding_chunks_per_s_code, "chunks")] as [string, string],
        ]),
    ["Ranking", perSecond(c.rerank_pairs_per_s, "passages")],
    ["Reading documents", perSecond(c.parse_pages_per_s, "pages")],
    ["Speech-to-text", formatRealtimeFactor(c.stt_realtime_factor)],
  ];
}

function ComputerLine({ results }: { results: Calibration }) {
  const line = formatComputer(results.cpu, results.runtime);
  if (!line) return null;
  return (
    <p className="mt-2 text-xs text-muted-foreground" data-testid="calibration-computer">
      <span className="font-medium">This computer:</span> <span>{line}</span>
    </p>
  );
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
              <ComputerLine results={cal.data.results} />
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
