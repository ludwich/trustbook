import { IconAlertCircle, IconLoader2, IconMessages } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";
import { useChatStatus, useConversations, useMessages, useOverview } from "@/api/queries";
import type { ChatStage, Message } from "@/api/types";
import { CapabilityGate } from "@/components/capabilities/CapabilityGate";
import { EmptyState } from "@/components/common/EmptyState";
import { QueryError } from "@/components/common/QueryError";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { AnswerBadge, CitedText, RefusalCard, RemovedSentences, SourcesUsed } from "./AnswerParts";
import { askBlocker } from "./askBlocker";
import { Composer } from "./Composer";
import { ConversationBar } from "./ConversationBar";
import { GroundingHint } from "./GroundingHint";
import { OverviewCard } from "./OverviewCard";
import { type StreamState, useChatStream, withoutTrailingExchange } from "./useChatStream";

const STAGE_TEXT: Record<ChatStage, string> = {
  retrieving: "Searching your sources…",
  generating: "Writing…",
  checking: "Checking…",
};

function UserBubble({ text }: { text: string }) {
  return (
    <div className="ml-auto max-w-[85%] rounded-lg bg-secondary px-3 py-2 whitespace-pre-wrap">
      <span className="sr-only">You asked: </span>
      {text}
    </div>
  );
}

function AssistantMessage({ message }: { message: Message }) {
  const sentences = message.content.sentences ?? [];
  const refused = message.badge === "refused";
  return (
    <article aria-label="Answer" className="grid gap-2" data-testid="assistant-message">
      {message.badge && (
        <AnswerBadge badge={message.badge} removed={message.removed_sentences.length} />
      )}
      {refused ? (
        <RefusalCard
          message={message.content.text ?? sentences.map((s) => s.text).join(" ")}
          suggestion={message.content.suggestion}
        />
      ) : (
        <CitedText sentences={sentences} />
      )}
      <RemovedSentences sentences={message.removed_sentences} />
      {!refused && <SourcesUsed sentences={sentences} sourcesUsed={message.sources_used} />}
    </article>
  );
}

function StreamingAnswer({ state, onRetry }: { state: StreamState; onRetry: () => void }) {
  return (
    <>
      <UserBubble text={state.question} />
      <article aria-label="Answer" aria-busy={state.running} className="grid gap-2">
        {state.refusal && (
          <>
            <AnswerBadge badge="refused" />
            <RefusalCard message={state.refusal.message} suggestion={state.refusal.suggestion} />
          </>
        )}
        {state.sentences.length > 0 && <CitedText sentences={state.sentences} />}
        {state.running && (
          <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
            <IconLoader2 className="size-3.5 animate-spin" aria-hidden />
            {state.stage ? STAGE_TEXT[state.stage] : STAGE_TEXT.retrieving}
          </p>
        )}
        {state.error && (
          <div
            role="alert"
            className="flex flex-wrap items-center gap-2 rounded-lg bg-danger-soft px-3 py-2 text-sm"
          >
            <IconAlertCircle className="size-4 text-destructive" aria-hidden />
            <span className="flex-1">{state.error}</span>
            <Button size="xs" variant="outline" onClick={onRetry}>
              Try again
            </Button>
          </div>
        )}
        {state.stopped && <p className="text-sm text-muted-foreground">You stopped this answer.</p>}
        {!state.running && !state.refusal && !state.error && state.sentences.length > 0 && (
          <SourcesUsed sentences={state.sentences} />
        )}
      </article>
    </>
  );
}

export function ChatView({ spaceId }: { spaceId: string }) {
  const conversations = useConversations(spaceId);
  // undefined = follow the newest conversation; null = a new, still empty conversation.
  const [selected, setSelected] = useState<string | null | undefined>(undefined);
  const cid = selected === undefined ? (conversations.data?.[0]?.id ?? null) : selected;
  const messages = useMessages(spaceId, cid);
  const status = useChatStatus(spaceId);
  const overview = useOverview(spaceId);
  const { state, ask, stop, clear } = useChatStream(spaceId);
  const scrollRef = useRef<HTMLDivElement>(null);

  const streamHere = state !== null && state.conversationId === (cid ?? "");
  const persisted = messages.data ?? [];
  const visible = streamHere ? withoutTrailingExchange(persisted, state.question) : persisted;
  const hasContent = visible.length > 0 || streamHere;
  const canAsk = askBlocker(status.data) === null && !(state?.running ?? false);

  const onAsk = (question: string) => {
    void ask(cid, question, (c) => setSelected(c.id));
  };

  const switchTo = (id: string | null) => {
    clear();
    setSelected(id);
  };

  // Keep the newest content in view.
  const sentenceCount = streamHere ? state.sentences.length : 0;
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [visible.length, sentenceCount, streamHere, state?.stage, state?.refusal, state?.error]);

  const overviewReady = overview.data?.enabled && overview.data.status === "ready";

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <ConversationBar
        spaceId={spaceId}
        current={cid}
        onSelect={(id) => switchTo(id)}
        onNew={() => switchTo(null)}
      />

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto" data-testid="chat-scroll">
        <div className="mx-auto grid w-full max-w-3xl gap-5 pb-4">
          {conversations.isError && (
            <QueryError
              title="Could not load conversations"
              error={conversations.error}
              onRetry={() => void conversations.refetch()}
            />
          )}
          {messages.isError && (
            <QueryError
              title="Could not load this conversation"
              error={messages.error}
              onRetry={() => void messages.refetch()}
            />
          )}

          {cid !== null && messages.isPending && !streamHere ? (
            <div className="grid gap-3">
              <Skeleton className="ml-auto h-9 w-1/2" />
              <Skeleton className="h-20" />
            </div>
          ) : (
            !hasContent && (
              <>
                {!overviewReady && (
                  <EmptyState
                    icon={<IconMessages />}
                    title="Ask questions about your sources"
                    className="border-none py-6"
                  >
                    Every answer comes only from the sources you tick, with numbered citations you
                    can click to check.
                  </EmptyState>
                )}
                <OverviewCard
                  spaceId={spaceId}
                  hasReadySources={(status.data?.ready_sources ?? 0) > 0}
                  canAsk={canAsk}
                  onAsk={onAsk}
                />
              </>
            )
          )}

          {visible.map((m) =>
            m.role === "user" ? (
              <UserBubble key={m.id} text={m.content.text ?? ""} />
            ) : (
              <AssistantMessage key={m.id} message={m} />
            ),
          )}

          {streamHere && <StreamingAnswer state={state} onRetry={() => onAsk(state.question)} />}
        </div>
      </div>

      <div className="mx-auto grid w-full max-w-3xl gap-2">
        <GroundingHint />
        <CapabilityGate capability="chat" feature="Asking questions">
          <Composer
            status={status.data}
            running={state?.running ?? false}
            onAsk={onAsk}
            onStop={stop}
          />
        </CapabilityGate>
      </div>
    </div>
  );
}
