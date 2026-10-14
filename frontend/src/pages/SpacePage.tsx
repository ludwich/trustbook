import {
  IconArrowLeft,
  IconBulb,
  IconDotsVertical,
  IconMessages,
  IconRoute,
} from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { api, ApiError } from "@/api/client";
import { queryKeys, useSources, useSpace } from "@/api/queries";
import { ChatView } from "@/components/chat/ChatView";
import { SourceViewerProvider } from "@/components/chat/SourceViewer";
import { EmptyState } from "@/components/common/EmptyState";
import { QueryError } from "@/components/common/QueryError";
import { GateLogDialog } from "@/components/gate/GateLogDialog";
import { IdeationView } from "@/components/ideation/IdeationView";
import { ReindexBanner } from "@/components/sources/ReindexBanner";
import { SourcesPanel } from "@/components/sources/SourcesPanel";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatDate } from "@/lib/format";

/** Below the 52 px sticky header (h-13 + 1 px border). */
const PAGE_HEIGHT = "h-[calc(100svh-3.25rem-1px)] min-h-[520px]";

type SpaceTab = "chat" | "ideation";

/**
 * The open tab lives in the URL (`?tab=ideation`), so a reload or a shared link keeps it. Chat, the
 * default, has no parameter. Switching tabs replaces the history entry instead of adding one.
 */
function useSpaceTab(): [SpaceTab, (tab: string) => void] {
  const [params, setParams] = useSearchParams();
  const tab: SpaceTab = params.get("tab") === "ideation" ? "ideation" : "chat";
  const setTab = (next: string) =>
    setParams(
      (prev) => {
        const out = new URLSearchParams(prev);
        if (next === "ideation") out.set("tab", "ideation");
        else out.delete("tab");
        return out;
      },
      { replace: true },
    );
  return [tab, setTab];
}

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
  const [tab, setTab] = useSpaceTab();
  const [gateLogOpen, setGateLogOpen] = useState(false);
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
            <div className="flex items-start gap-1">
              <h1 className="min-w-0 flex-1 text-base leading-snug font-semibold break-words">
                {s.name}
              </h1>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label="Space menu"
                    className="-mt-0.5 -mr-1.5 text-muted-foreground"
                  >
                    <IconDotsVertical aria-hidden />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onSelect={() => setGateLogOpen(true)}>
                    <IconRoute aria-hidden />
                    Gate log
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
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
          <GateLogDialog spaceId={spaceId} open={gateLogOpen} onOpenChange={setGateLogOpen} />
        </aside>

        {/* Centre */}
        <section className="flex min-h-0 min-w-0 flex-col px-6 pt-3 pb-4">
          <ReindexBanner spaceId={spaceId} />
          <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
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
              <ChatView spaceId={spaceId} onOpenIdeation={() => setTab("ideation")} />
            </TabsContent>

            <TabsContent value="ideation" className="flex min-h-0 flex-col pt-2">
              <IdeationView spaceId={spaceId} />
            </TabsContent>
          </Tabs>
        </section>
      </div>
    </SourceViewerProvider>
  );
}
