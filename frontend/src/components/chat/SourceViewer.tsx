import { IconExternalLink, IconGitCommit } from "@tabler/icons-react";
import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "@/api/client";
import { useChunkContext, useSources } from "@/api/queries";
import type { Chunk, ChunkContext, Citation, CodeContext, Source, TextContext } from "@/api/types";
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
import {
  centeredScrollTop,
  CODE_LINE_HEIGHT_PX,
  CODE_PADDING_TOP_PX,
  codeScrollTop,
  highlightWindow,
} from "@/lib/scroll";
import { cn } from "@/lib/utils";
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
            <SourceViewerBody key={target.chunk_id} spaceId={spaceId} citation={target} />
          </SheetContent>
        )}
      </Sheet>
    </CitationViewerContext.Provider>
  );
}

function shortCommit(commit: string | null | undefined): string | null {
  return commit ? commit.slice(0, 7) : null;
}

function lineRange([from, to]: [number, number]): string {
  return from === to ? `line ${from}` : `lines ${from}–${to}`;
}

export function SourceViewerBody({ spaceId, citation }: { spaceId: string; citation: Citation }) {
  const context = useChunkContext(spaceId, citation.chunk_id);
  const sources = useSources(spaceId);
  const source = sources.data?.find((s) => s.id === citation.source_id);
  const ctx = context.data;
  const code = ctx?.view === "code" ? ctx.code : undefined;
  const commit = shortCommit(code ? code.commit : source?.commit_hash);
  const where = code
    ? null
    : ((ctx?.view === "text" ? ctx.text?.label : null) ??
      formatLocator(ctx?.chunk.locator ?? citation.locator));
  // A document file's chunk is shown as lines of the file; its section is named too. Chunks are
  // split at headings (D-104), so the section holds every highlighted line.
  const section = code ? (ctx?.chunk.locator.heading ?? null) : null;

  return (
    <>
      <SheetHeader>
        <SheetTitle>{ctx?.chunk.source_title ?? citation.source_title}</SheetTitle>
        <SheetDescription
          id="source-viewer-where"
          className="flex flex-wrap items-center gap-x-2 gap-y-0.5"
        >
          <span>Citation [{citation.n}]</span>
          {code && (
            <span className="min-w-0 truncate font-mono text-xs" data-testid="viewer-file">
              · {code.file}, {lineRange(code.highlight)}
            </span>
          )}
          {section && <span data-testid="viewer-section">· Section: {section}</span>}
          {where && <span data-testid="viewer-label">· {where}</span>}
          {commit && (
            <span
              className="inline-flex items-center gap-0.5 font-mono text-xs"
              data-testid="viewer-commit"
            >
              · <IconGitCommit className="size-3.5" aria-label="Commit" />
              {commit}
            </span>
          )}
          {source?.snapshot_at && <span>· Snapshot {formatDate(source.snapshot_at)}</span>}
        </SheetDescription>
        {source && <OriginLink spaceId={spaceId} source={source} />}
      </SheetHeader>

      {context.isPending ? (
        <div className="grid gap-2 px-5 py-4" aria-busy="true">
          <span className="sr-only" role="status">
            Loading the passage…
          </span>
          <Skeleton className="h-4" />
          <Skeleton className="h-4" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      ) : context.isError ? (
        <div className="px-5 py-4">
          <QueryError
            title="Could not load the passage"
            error={context.error}
            onRetry={() => void context.refetch()}
          />
        </div>
      ) : (
        <ContextView context={context.data} />
      )}
    </>
  );
}

function ContextView({ context }: { context: ChunkContext }) {
  if (context.view === "code" && context.code && context.code.lines.length > 0) {
    return <CodeView code={context.code} />;
  }
  if (context.view === "text" && context.text) return <TextView text={context.text} />;
  return <PassageOnly chunk={context.chunk} />;
}

/**
 * Code with line numbers, the cited lines highlighted. Rows have a fixed height, so the scroll
 * position follows from line numbers and is set in a layout effect: the first paint already shows
 * the highlight (no flash of the top of the file).
 */
export function CodeView({ code }: { code: CodeContext }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const { start, count } = highlightWindow(code.first_line, code.lines.length, code.highlight);
  const lastNumber = code.first_line + code.lines.length - 1;
  const gutterCh = Math.max(3, String(lastNumber).length);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = codeScrollTop(
      code.first_line,
      code.lines.length,
      code.highlight,
      el.clientHeight,
    );
  }, [code]);

  return (
    <div
      ref={scrollRef}
      role="region"
      aria-label={`${code.file}, ${lineRange(code.highlight)} highlighted`}
      tabIndex={0}
      data-testid="code-view"
      className="min-h-0 flex-1 overflow-auto bg-muted/30 font-mono text-[12.5px] focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none focus-visible:ring-inset"
      style={{ lineHeight: `${CODE_LINE_HEIGHT_PX}px` }}
    >
      <div
        className="w-max min-w-full pb-3 [tab-size:4]"
        style={{ paddingTop: CODE_PADDING_TOP_PX }}
      >
        {code.lines.map((line, i) => {
          const n = code.first_line + i;
          const hit = i >= start && i < start + count;
          return (
            <div
              key={n}
              data-line={n}
              data-highlighted={hit || undefined}
              className={cn("flex", hit && "bg-warning-soft")}
              style={{ height: CODE_LINE_HEIGHT_PX }}
            >
              <span
                aria-hidden
                className={cn(
                  "sticky left-0 shrink-0 border-r pr-3 pl-4 text-right text-muted-foreground tabular-nums select-none",
                  hit ? "border-warning bg-warning-soft text-foreground" : "bg-popover",
                )}
                style={{ width: `calc(${gutterCh}ch + 1.75rem)` }}
              >
                {n}
              </span>
              <code className="pr-5 pl-3 whitespace-pre">{line}</code>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Text with the cited passage highlighted, positioned before paint like the code view. */
export function TextView({ text }: { text: TextContext }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const markRef = useRef<HTMLElement>(null);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    const mark = markRef.current;
    if (!el || !mark) return;
    // The scroll container is `relative`, so it is the mark's offsetParent.
    el.scrollTop = centeredScrollTop(mark.offsetTop, mark.offsetHeight, el.clientHeight);
  }, [text]);

  return (
    <div
      ref={scrollRef}
      className="relative min-h-0 flex-1 overflow-y-auto px-5 py-4"
      data-testid="text-view"
    >
      <div
        className="text-sm leading-relaxed break-words whitespace-pre-wrap"
        data-testid="snapshot-text"
      >
        {text.before && (
          <>
            <span className="text-muted-foreground">… </span>
            {text.before}
          </>
        )}
        <mark
          ref={markRef}
          data-testid="cited-passage"
          className="rounded-sm bg-warning-soft px-0.5 text-foreground ring-2 ring-warning/40"
        >
          {text.highlight}
        </mark>
        {text.after && (
          <>
            {text.after}
            <span className="text-muted-foreground"> …</span>
          </>
        )}
      </div>
    </div>
  );
}

/** When the backend has no context for a chunk, show the passage on its own. */
function PassageOnly({ chunk }: { chunk: Chunk }) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
      <mark
        data-testid="cited-passage"
        className="rounded-sm bg-warning-soft px-1 text-sm whitespace-pre-wrap text-foreground"
      >
        {chunk.text}
      </mark>
    </div>
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
