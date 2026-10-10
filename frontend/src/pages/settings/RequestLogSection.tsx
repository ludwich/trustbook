import { IconTrash } from "@tabler/icons-react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/api/client";
import { queryKeys, useClearRequestLog } from "@/api/queries";
import type { RequestLogEntry } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { QueryError } from "@/components/common/QueryError";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatBytes, formatTime } from "@/lib/format";
import { cn, errorMessage } from "@/lib/utils";
import { SettingsSection } from "./SettingsSection";

const PAGE = 100;

export function RequestLogSection() {
  const log = useInfiniteQuery({
    queryKey: queryKeys.requestLog,
    queryFn: ({ pageParam }) => api.listRequestLog({ limit: PAGE, beforeId: pageParam }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => (last.length === PAGE ? last[last.length - 1]?.id : undefined),
  });
  const clear = useClearRequestLog();
  const [confirm, setConfirm] = useState(false);

  const entries = log.data?.pages.flat() ?? [];

  return (
    <SettingsSection
      id="request-log"
      title="Request log"
      description="Every request Trustbook sent to a model or search service, newest first. API keys are never logged."
      actions={
        entries.length > 0 && (
          <Button size="sm" variant="outline" onClick={() => setConfirm(true)}>
            <IconTrash aria-hidden />
            Clear log
          </Button>
        )
      }
    >
      {log.isPending && <Skeleton className="h-24" />}
      {log.isError && <QueryError error={log.error} onRetry={() => void log.refetch()} />}
      {log.isSuccess && entries.length === 0 && (
        <EmptyState title="Nothing sent yet">
          When Trustbook sends text to a model or search service, it shows up here.
        </EmptyState>
      )}
      {entries.length > 0 && (
        <div className="rounded-lg border bg-card">
          <Table className="table-fixed">
            <colgroup>
              <col className="w-36" />
              <col className="w-28" />
              <col className="w-40" />
              <col className="w-32" />
              <col className="w-24" />
              <col />
            </colgroup>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Time</TableHead>
                <TableHead>Used for</TableHead>
                <TableHead>Sent to</TableHead>
                <TableHead>Space</TableHead>
                <TableHead className="text-right">Size</TableHead>
                <TableHead>What was sent</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((e) => (
                <LogRow key={e.id} entry={e} />
              ))}
            </TableBody>
          </Table>
          {log.hasNextPage && (
            <div className="border-t p-2 text-center">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void log.fetchNextPage()}
                disabled={log.isFetchingNextPage}
              >
                {log.isFetchingNextPage && <Spinner />}
                Show older
              </Button>
            </div>
          )}
        </div>
      )}

      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Clear the request log?</AlertDialogTitle>
            <AlertDialogDescription>
              This deletes the record of what was sent. It does not undo anything that was sent.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {clear.isError && (
            <p role="alert" className="text-sm text-destructive">
              {errorMessage(clear.error)}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button
              variant="destructive"
              disabled={clear.isPending}
              onClick={() => clear.mutate(undefined, { onSuccess: () => setConfirm(false) })}
            >
              Clear log
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SettingsSection>
  );
}

function LogRow({ entry }: { entry: RequestLogEntry }) {
  return (
    <TableRow>
      <TableCell className="text-xs whitespace-nowrap text-muted-foreground tabular-nums">
        {formatTime(entry.timestamp)}
      </TableCell>
      <TableCell className="text-xs">{entry.capability}</TableCell>
      <TableCell className="truncate font-mono text-xs" title={entry.endpoint_host}>
        {entry.endpoint_host}
      </TableCell>
      <TableCell className="truncate text-xs" title={entry.space_name ?? undefined}>
        {entry.space_name ?? <span className="text-muted-foreground">None</span>}
      </TableCell>
      <TableCell className="text-right text-xs whitespace-nowrap tabular-nums">
        {formatBytes(entry.bytes_sent)}
        {entry.tokens_sent !== null && (
          <div className="text-muted-foreground">
            {entry.tokens_sent.toLocaleString("en-US")} tokens
          </div>
        )}
      </TableCell>
      <TableCell>
        <Excerpt text={entry.excerpt} />
      </TableCell>
    </TableRow>
  );
}

function Excerpt({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <button
      type="button"
      aria-expanded={open}
      title={open ? "Show less" : "Show everything that was sent"}
      onClick={() => setOpen((o) => !o)}
      className={cn(
        "block w-full rounded-sm text-left font-mono text-xs outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
        open
          ? "max-h-72 overflow-auto bg-muted px-2 py-1.5 break-words whitespace-pre-wrap text-foreground"
          : "truncate text-muted-foreground hover:text-foreground",
      )}
    >
      {text || "(empty)"}
    </button>
  );
}
