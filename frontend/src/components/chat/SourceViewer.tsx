import { IconExternalLink, IconGitCommit } from "@tabler/icons-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "@/api/client";
import { useChunk, useSnapshot, useSources } from "@/api/queries";
import type { Chunk, Citation, Source } from "@/api/types";
import { QueryError } from "@/components/common/QueryError";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDate } from "@/lib/format";
import { formatLocator } from "@/lib/locator";
import { CitationViewerContext } from "./useCitationViewer";

/** Provides citation clicks to everything below it and renders the viewer drawer. */
export function SourceViewerProvider({
  spaceId,
  children,
}: {
  spaceId: string;
  children: ReactNode;
}) {
  const [target, setTarget] = useState<Citation | null>(null);
  const value = useMemo(() => ({ open: setTarget }), []);
  return (
    <CitationViewerContext.Provider value={value}>
      {children}
      <Sheet open={target !== null} onOpenChange={(o) => !o && setTarget(null)}>
        {target && (
          <SheetContent aria-describedby="source-viewer-where">
            <SourceViewerBody spaceId={spaceId} citation={target} />
          </SheetContent>
        )}
      </Sheet>
    </CitationViewerContext.Provider>
  );
}

/** Characters of context shown on each side of the passage in very long sources. */
const CONTEXT_CHARS = 15_000;

function SourceViewerBody({ spaceId, citation }: { spaceId: string; citation: Citation }) {
  const chunk = useChunk(spaceId, citation.chunk_id);
  const sources = useSources(spaceId);
  const source = sources.data?.find((s) => s.id === citation.source_id);
  const locator = formatLocator(chunk.data?.locator ?? citation.locator);

  return (
    <>
      <SheetHeader>
        <SheetTitle>{chunk.data?.source_title ?? citation.source_title}</SheetTitle>
        <SheetDescription id="source-viewer-where" className="flex flex-wrap items-center gap-x-2">
          <span>Citation [{citation.n}]</span>
          {locator && <span>· {locator}</span>}
          {source?.commit_hash && (
            <span className="inline-flex items-center gap-0.5 font-mono text-xs">
              · <IconGitCommit className="size-3.5" aria-label="Commit" />
              {source.commit_hash.slice(0, 7)}
            </span>
          )}
          {source?.snapshot_at && <span>· Snapshot {formatDate(source.snapshot_at)}</span>}
        </SheetDescription>
        {source && <OriginLink spaceId={spaceId} source={source} />}
      </SheetHeader>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {chunk.isPending ? (
          <div className="grid gap-2">
            <Skeleton className="h-4" />
            <Skeleton className="h-4" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ) : chunk.isError ? (
          <QueryError
            title="Could not load the passage"
            error={chunk.error}
            onRetry={() => void chunk.refetch()}
          />
        ) : (
          <SnapshotText spaceId={spaceId} chunk={chunk.data} />
        )}
      </div>
    </>
  );
}

function OriginLink({ spaceId, source }: { spaceId: string; source: Source }) {
  let href: string | null = null;
  let label = "";
  if (source.type === "upload") {
    href = api.originalUrl(spaceId, source.id);
    label = "Open the original file";
  } else if (source.origin && /^https?:\/\//i.test(source.origin)) {
    href = source.origin;
    label = "Open the page";
  }
  if (!href) return null;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex w-fit items-center gap-1 text-xs text-primary hover:underline"
    >
      {label}
      <IconExternalLink className="size-3.5" aria-hidden />
    </a>
  );
}

function SnapshotText({ spaceId, chunk }: { spaceId: string; chunk: Chunk }) {
  const snapshot = useSnapshot(spaceId, chunk.source_id);
  const markRef = useRef<HTMLElement>(null);
  const text = snapshot.data?.markdown ?? "";
  const valid =
    snapshot.isSuccess &&
    chunk.start_offset >= 0 &&
    chunk.end_offset > chunk.start_offset &&
    chunk.end_offset <= text.length;

  useEffect(() => {
    if (valid) markRef.current?.scrollIntoView({ block: "center" });
  }, [valid, chunk.id]);

  if (snapshot.isPending) {
    return (
      <div className="grid gap-2">
        <Skeleton className="h-4" />
        <Skeleton className="h-4" />
        <Skeleton className="h-4 w-1/2" />
      </div>
    );
  }

  // Without the snapshot (or with offsets that don't fit it) show the passage on its own.
  if (!valid) {
    return (
      <div className="grid gap-3">
        {snapshot.isError && (
          <p className="text-xs text-muted-foreground">
            The full source text could not be loaded. Showing the cited passage.
          </p>
        )}
        <mark className="rounded-sm bg-warning-soft px-1 whitespace-pre-wrap text-foreground">
          {chunk.text}
        </mark>
      </div>
    );
  }

  const from = Math.max(0, chunk.start_offset - CONTEXT_CHARS);
  const to = Math.min(text.length, chunk.end_offset + CONTEXT_CHARS);
  return (
    <div
      className="font-sans text-sm leading-relaxed break-words whitespace-pre-wrap"
      data-testid="snapshot-text"
    >
      {from > 0 && <span className="text-muted-foreground">… </span>}
      {text.slice(from, chunk.start_offset)}
      <mark
        ref={markRef}
        data-testid="cited-passage"
        className="rounded-sm bg-warning-soft px-0.5 text-foreground ring-2 ring-warning/40"
      >
        {text.slice(chunk.start_offset, chunk.end_offset)}
      </mark>
      {text.slice(chunk.end_offset, to)}
      {to < text.length && <span className="text-muted-foreground"> …</span>}
    </div>
  );
}
