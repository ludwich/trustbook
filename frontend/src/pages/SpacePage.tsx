import {
  IconArrowLeft,
  IconBulb,
  IconFileText,
  IconMessages,
  IconPlus,
  IconSend,
} from "@tabler/icons-react";
import { useEffect } from "react";
import { Link, useParams } from "react-router";
import { api, ApiError } from "@/api/client";
import { useSpace } from "@/api/queries";
import { CapabilityGate } from "@/components/capabilities/CapabilityGate";
import { EmptyState } from "@/components/common/EmptyState";
import { QueryError } from "@/components/common/QueryError";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { formatDate, plural } from "@/lib/format";

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

  const s = space.data;
  return (
    <div className="grid flex-1 grid-cols-[minmax(240px,300px)_1fr]">
      {/* Sources panel */}
      <aside aria-label="Sources" className="flex flex-col border-r bg-card/50">
        <div className="border-b px-4 py-3">
          <Link
            to="/"
            className="mb-2 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <IconArrowLeft className="size-3.5" aria-hidden />
            All spaces
          </Link>
          <h1 className="text-base leading-snug font-semibold break-words">{s.name}</h1>
          {s.description && <p className="mt-0.5 text-sm text-muted-foreground">{s.description}</p>}
          <p className="mt-1.5 text-xs text-muted-foreground">
            Created {formatDate(s.created_at)} · {plural(s.source_count, "source")}
          </p>
          <p
            className="mt-1 truncate font-mono text-[11.5px] text-muted-foreground"
            title={s.folder}
          >
            {s.folder}
          </p>
        </div>

        <div className="flex items-center justify-between px-4 pt-3 pb-2">
          <h2 className="text-sm font-semibold">Sources</h2>
        </div>
        <div className="grid gap-2 px-4">
          <Button variant="outline" size="sm" disabled aria-describedby="add-source-note">
            <IconPlus aria-hidden />
            Add source
          </Button>
          <p id="add-source-note" className="text-xs text-muted-foreground">
            Coming in the next phase.
          </p>
        </div>
        <div className="p-4">
          {s.source_count === 0 ? (
            <EmptyState icon={<IconFileText />} title="No sources yet" className="py-8">
              Documents, web pages, videos and code folders you add will show up here.
            </EmptyState>
          ) : (
            <EmptyState
              icon={<IconFileText />}
              title={`${plural(s.source_count, "source")} in this space`}
              className="py-8"
            >
              The source list arrives in the next phase.
            </EmptyState>
          )}
        </div>
      </aside>

      {/* Centre */}
      <section className="flex min-w-0 flex-col px-6 py-4">
        <Tabs defaultValue="chat" className="flex flex-1 flex-col">
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

          <TabsContent value="chat" className="flex flex-col gap-4 pt-2">
            <EmptyState icon={<IconMessages />} title="Ask questions about your sources">
              Answers come only from the sources you tick, with citations you can check. Chat
              arrives in the next phase.
            </EmptyState>
            <CapabilityGate capability="chat" feature="Asking questions" className="mt-auto">
              <ChatBoxPreview />
            </CapabilityGate>
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
  );
}

/** Disabled chat box placeholder; becomes the real chat box in Phase 1. */
function ChatBoxPreview() {
  return (
    <div className="rounded-lg border bg-card p-2 shadow-xs">
      <Textarea
        disabled
        rows={2}
        placeholder="Ask about your sources"
        aria-label="Question"
        className="resize-none border-0 shadow-none focus-visible:ring-0"
      />
      <div className="flex items-center justify-between px-1 pt-1">
        <span className="text-xs text-muted-foreground">Using 0 of 0 sources</span>
        <Button size="sm" disabled>
          <IconSend aria-hidden />
          Ask
        </Button>
      </div>
    </div>
  );
}
