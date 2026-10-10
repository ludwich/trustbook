import {
  IconAlertCircle,
  IconAlertTriangle,
  IconGitCommit,
  IconLock,
  IconRefresh,
  IconTrash,
} from "@tabler/icons-react";
import { useState } from "react";
import { isSourceBusy, useDeleteSource, useRefreshSource } from "@/api/queries";
import type { Source } from "@/api/types";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Progress } from "@/components/ui/progress";
import { formatDate, plural } from "@/lib/format";
import { cn, errorMessage } from "@/lib/utils";
import { SecretsDialog } from "./SecretsDialog";
import {
  aheadText,
  canRefresh,
  isWebSnapshot,
  refreshStatsText,
  STATUS_TEXT,
  statsLine,
  TYPE_LABELS,
  visibleQualityWarning,
} from "./sourceMeta";
import { SourceTypeIcon } from "./SourceTypeIcon";

const FAILED_TOOLTIP = "This source failed to load";

export function SourceRow({
  spaceId,
  source,
  onToggle,
}: {
  spaceId: string;
  source: Source;
  onToggle: (enabled: boolean) => void;
}) {
  const refresh = useRefreshSource(spaceId);
  const remove = useDeleteSource(spaceId);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [secretsOpen, setSecretsOpen] = useState(false);
  const busy = isSourceBusy(source);
  const failed = source.status === "failed";
  const stats = source.status === "ready" ? statsLine(source) : "";
  const skipped = source.stats.skipped_secrets ?? 0;
  const warning = visibleQualityWarning(source);
  const actionError = refresh.error ?? remove.error;
  const snapshot =
    isWebSnapshot(source) && source.snapshot_at ? `Snapshot ${formatDate(source.snapshot_at)}` : "";
  const meta = [stats, snapshot].filter(Boolean).join(" · ");
  const ready = source.status === "ready";
  // Linked folder whose HEAD moved past the indexed commit (SPEC §7.5).
  const ahead = ready ? aheadText(source) : null;
  const lastRefresh = ready ? refreshStatsText(source) : null;

  return (
    <li
      className="group/row relative rounded-md px-2 py-1.5 hover:bg-accent/50"
      data-source-status={source.status}
      data-testid={`source-${source.id}`}
    >
      <div className="flex items-start gap-2">
        {/* A failed source can't be used: unticked and disabled (G7). The wrapper carries the
            tooltip because browsers don't show one for a disabled input reliably. */}
        <span className="mt-0.5 inline-flex" title={failed ? FAILED_TOOLTIP : undefined}>
          <Checkbox
            checked={failed ? false : source.enabled}
            disabled={failed}
            onChange={(e) => onToggle(e.target.checked)}
            aria-label={`Use ${source.title}`}
            title={failed ? FAILED_TOOLTIP : undefined}
          />
        </span>
        <SourceTypeIcon source={source} className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <p
            className={cn(
              "truncate text-sm leading-snug",
              (failed || !source.enabled) && "text-muted-foreground",
            )}
            title={source.origin ? `${source.title}\n${source.origin}` : source.title}
          >
            <span className="sr-only">{TYPE_LABELS[source.type]}: </span>
            {source.title}
          </p>

          {busy && (
            <div className="mt-1 grid gap-1">
              <span className="text-xs text-muted-foreground">
                {source.status === "queued"
                  ? "Waiting in the queue"
                  : `${STATUS_TEXT[source.status]}…`}
              </span>
              {source.status !== "queued" && (
                <Progress
                  value={source.progress}
                  label={`${STATUS_TEXT[source.status]} ${source.title}`}
                />
              )}
            </div>
          )}

          {failed && (
            <div className="mt-1 grid gap-0.5 text-xs">
              <p className="flex items-start gap-1 text-destructive">
                <IconAlertCircle className="mt-px size-3.5 shrink-0" aria-hidden />
                <span>{source.failure_reason ?? "Trustbook could not read this source."}</span>
              </p>
              {source.failure_next_step && (
                <p className="pl-4.5 text-muted-foreground">{source.failure_next_step}</p>
              )}
            </div>
          )}

          {warning && (
            <p className="mt-1 flex items-start gap-1 text-xs text-warning">
              <IconAlertTriangle className="mt-px size-3.5 shrink-0" aria-hidden />
              <span>{warning}</span>
            </p>
          )}

          {skipped > 0 && (
            <button
              type="button"
              onClick={() => setSecretsOpen(true)}
              aria-haspopup="dialog"
              data-testid="secrets-button"
              className="mt-1 flex items-start gap-1 rounded-sm text-left text-xs text-warning underline-offset-2 hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
            >
              <IconLock className="mt-px size-3.5 shrink-0" aria-hidden />
              <span>{plural(skipped, "file")} skipped: possible secrets</span>
            </button>
          )}

          {(meta || source.commit_hash) && (
            <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
              {meta && <span>{meta}</span>}
              {source.commit_hash && (
                <span className="inline-flex items-center gap-0.5 font-mono">
                  <IconGitCommit className="size-3" aria-label="Commit" />
                  {source.commit_hash.slice(0, 7)}
                </span>
              )}
            </p>
          )}

          {lastRefresh && (
            <p
              className="mt-0.5 flex items-start gap-1 text-xs text-muted-foreground"
              data-testid="source-refresh-stats"
            >
              <IconRefresh className="mt-px size-3.5 shrink-0" aria-hidden />
              <span>
                <span className="sr-only">Last refresh: </span>
                {lastRefresh}
              </span>
            </p>
          )}

          {ahead && (
            <p
              className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 rounded-md bg-info-soft px-1.5 py-1 text-xs text-foreground"
              data-testid="source-ahead"
            >
              <IconGitCommit className="size-3.5 shrink-0" aria-hidden />
              <span>{ahead}</span>
              <button
                type="button"
                onClick={() => refresh.mutate(source.id)}
                disabled={refresh.isPending}
                title="Index the new commits (only changed files are read again)"
                className="rounded-sm font-medium text-primary underline-offset-2 hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none disabled:opacity-50"
              >
                Refresh?
              </button>
            </p>
          )}

          {actionError && (
            <p role="alert" className="mt-1 text-xs text-destructive">
              {errorMessage(actionError)}
            </p>
          )}
        </div>

        {/* Overlaid on hover/focus so the text can use the full width. */}
        <div className="absolute top-1 right-1 flex items-center rounded-md border bg-popover opacity-0 shadow-xs transition-opacity group-focus-within/row:opacity-100 group-hover/row:opacity-100">
          {canRefresh(source) && (
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={`Refresh ${source.title}`}
              title="Read it again from the original"
              disabled={busy || refresh.isPending}
              onClick={() => refresh.mutate(source.id)}
            >
              <IconRefresh aria-hidden />
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={`Remove ${source.title}`}
            title="Remove"
            onClick={() => setConfirmOpen(true)}
          >
            <IconTrash aria-hidden />
          </Button>
        </div>
      </div>

      {skipped > 0 && (
        <SecretsDialog
          spaceId={spaceId}
          source={source}
          open={secretsOpen}
          onOpenChange={setSecretsOpen}
        />
      )}

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove “{source.title}”?</AlertDialogTitle>
            <AlertDialogDescription>
              Its text and search index are deleted from this space
              {source.type === "upload" ? ", with the uploaded copy" : ""}. Files on your computer
              and web pages are not touched.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => remove.mutate(source.id)}>
              Remove source
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </li>
  );
}
