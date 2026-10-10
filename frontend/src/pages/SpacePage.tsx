import { IconArrowLeft, IconBulb, IconMessages } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link, useParams } from "react-router";
import { api, ApiError } from "@/api/client";
import { queryKeys, useSources, useSpace } from "@/api/queries";
import { ChatView } from "@/components/chat/ChatView";
import { SourceViewerProvider } from "@/components/chat/SourceViewer";
import { EmptyState } from "@/components/common/EmptyState";
import { QueryError } from "@/components/common/QueryError";
import { SourcesPanel } from "@/components/sources/SourcesPanel";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatDate } from "@/lib/format";

/** Below the 52 px sticky header (h-13 + 1 px border). */
const PAGE_HEIGHT = "h-[calc(100svh-3.25rem-1px)] min-h-[520px]";

/**
 * When a source changes status or is ticked/unticked, "Using X of Y sources" and the overview may
 * change too: refresh them.
 */
function useSyncChatWithSources(spaceId: string) {
  const qc = useQueryClient();
  const sources = useSources(spaceId);
  const signature = (sources.data ?? [])
    .map((s) => `${s.id}:${s.status}:${s.enabled ? 1 : 0}`)
    .join("|");
  useEffect(() => {
    if (!signature) return;
    void qc.invalidateQueries({ queryKey: queryKeys.chatStatus(spaceId) });
    void qc.invalidateQueries({ queryKey: queryKeys.overview(spaceId) });
  }, [qc, spaceId, signature]);
}

export function SpacePage() {
  const { id = "" } = useParams();
  const space = useSpace(id);

  // Opening a space makes it the active one: its jobs get queue priority.
  useEffect(() => {
    if (!id) return;
    api.setActiveSpace(id).catch(() => {
      // Priority is a nice-to-have; never block the page on it.
    });
  }, [id]);

  if (space.isPending) {
    return (
      <div className="grid flex-1 grid-cols-[280px_1fr] gap-0">
        <Skeleton className="m-4 h-40" />
        <Skeleton className="m-4 h-64" />
      </div>
    );
  }

  if (space.isError) {
    const notFound = space.error instanceof ApiError && space.error.status === 404;
    return (
      <div className="mx-auto w-full max-w-xl px-5 py-12">
        {notFound ? (
          <EmptyState
            title="This space does not exist"
            action={
              <Button asChild variant="outline">
                <Link to="/">Back to spaces</Link>
              </Button>
            }
          >
            It may have been deleted.
          </EmptyState>
        ) : (
          <QueryError
            title="Could not open the space"
            error={space.error}
            onRetry={() => void space.refetch()}
          />
        )}
      </div>
    );
  }

  return <SpaceWorkspace spaceId={id} />;
}

function SpaceWorkspace({ spaceId }: { spaceId: string }) {
  const space = useSpace(spaceId);
  useSyncChatWithSources(spaceId);
  const s = space.data;
  if (!s) return null;

  return (
    <SourceViewerProvider spaceId={spaceId}>
      <div className={`grid ${PAGE_HEIGHT} grid-cols-[minmax(260px,320px)_1fr]`}>
        {/* Sources panel */}
        <aside aria-label="Sources" className="flex min-h-0 flex-col border-r bg-card/50">
          <div className="border-b px-4 py-3">
            <Link
              to="/"
              className="mb-2 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              <IconArrowLeft className="size-3.5" aria-hidden />
              All spaces
            </Link>
            <h1 className="text-base leading-snug font-semibold break-words">{s.name}</h1>
            {s.description && (
              <p className="mt-0.5 text-sm text-muted-foreground">{s.description}</p>
            )}
            <p className="mt-1.5 text-xs text-muted-foreground">
              Created {formatDate(s.created_at)}
            </p>
            <p
              className="mt-0.5 truncate font-mono text-[11.5px] text-muted-foreground"
              title={s.folder}
            >
              {s.folder}
            </p>
          </div>
          <SourcesPanel spaceId={spaceId} />
        </aside>

        {/* Centre */}
        <section className="flex min-h-0 min-w-0 flex-col px-6 pt-3 pb-4">
          <Tabs defaultValue="chat" className="flex min-h-0 flex-1 flex-col">
            <TabsList aria-label="Space views">
              <TabsTrigger value="chat">
                <IconMessages aria-hidden />
                Chat
              </TabsTrigger>
              <TabsTrigger value="ideation">
                <IconBulb aria-hidden />
                Ideation
              </TabsTrigger>
            </TabsList>

            <TabsContent value="chat" className="flex min-h-0 flex-col pt-2">
              <ChatView spaceId={spaceId} />
            </TabsContent>

            <TabsContent value="ideation" className="pt-2">
              <EmptyState icon={<IconBulb />} title="I have an idea">
                Talk an idea through one question at a time and get a written brief. Ideas never
                become sources. Coming in a later phase.
              </EmptyState>
            </TabsContent>
          </Tabs>
        </section>
      </div>
    </SourceViewerProvider>
  );
}
