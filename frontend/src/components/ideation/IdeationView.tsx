import {
  IconAlertCircle,
  IconCircleCheck,
  IconFlag,
  IconLoader2,
  IconPlayerPlay,
} from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";
import { useIdeationAction, useIdeationSession, useIdeationSessions } from "@/api/queries";
import type { ChatStage, IdeationAction, IdeationNode, IdeationSession } from "@/api/types";
import { QueryError } from "@/components/common/QueryError";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { formatRelative } from "@/lib/format";
import { errorMessage } from "@/lib/utils";
import { BranchMap } from "./BranchMap";
import { FinishBar } from "./FinishBar";
import { IdeaEntry } from "./IdeaEntry";
import { IdeationSessionBar } from "./IdeationSessionBar";
import { InterviewThread } from "./InterviewThread";
import { QuestionCard } from "./QuestionCard";
import { sessionLabel } from "./sessionText";
import { type TurnState, useIdeationStream } from "./useIdeationStream";

const STAGE_TEXT: Record<ChatStage, string> = {
  retrieving: "Searching your sources…",
  generating: "Preparing the next question…",
  checking: "Checking the recommendation against your sources…",
};

export const COMPLETE_TEXT =
  "Complete: every branch is resolved. Press Finish now to write the brief.";

function StageLine({ stage, seeding }: { stage: ChatStage | null; seeding: boolean }) {
  const text =
    seeding && (stage === null || stage === "generating")
      ? "Sketching the first branches…"
      : STAGE_TEXT[stage ?? "generating"];
  return (
    <p
      role="status"
      className="flex items-center gap-2 text-sm text-muted-foreground"
      data-testid="ideation-stage"
    >
      <IconLoader2 className="size-3.5 animate-spin" aria-hidden />
      {text}
    </p>
  );
}

function TurnError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-2 rounded-lg bg-danger-soft px-3 py-2 text-sm"
      data-testid="ideation-error"
    >
      <IconAlertCircle className="size-4 text-destructive" aria-hidden />
      <span className="flex-1">{message}</span>
      {onRetry && (
        <Button size="xs" variant="outline" onClick={onRetry}>
          Ask again
        </Button>
      )}
    </div>
  );
}

/** What shows below the thread: the waiting question, progress, an error, or the end state. */
function CurrentStep({
  session,
  current,
  turn,
  focusQuestion,
  onAnswer,
  onAsk,
  onFinish,
}: {
  session: IdeationSession;
  current: IdeationNode | null;
  turn: TurnState | null;
  focusQuestion: boolean;
  onAnswer: (node: IdeationNode, action: IdeationAction, text?: string) => void;
  onAsk: () => void;
  onFinish: () => void;
}) {
  const active = session.status === "active";
  if (turn?.running) {
    return <StageLine stage={turn.stage} seeding={session.nodes.length === 0} />;
  }
  return (
    <>
      {turn?.error && <TurnError message={turn.error} onRetry={active ? onAsk : undefined} />}
      {current && (
        <QuestionCard
          key={current.id}
          node={current}
          canAnswer={active}
          focusOnMount={focusQuestion}
          onAnswer={(action, text) => onAnswer(current, action, text)}
        />
      )}
      {!current && active && session.complete && (
        <div
          className="flex flex-wrap items-center gap-2 rounded-lg border border-success/40 bg-success-soft px-3 py-2 text-sm"
          data-testid="ideation-complete"
        >
          <IconCircleCheck className="size-4 text-success" aria-hidden />
          <span className="flex-1">{COMPLETE_TEXT}</span>
          <Button size="xs" onClick={onFinish}>
            <IconFlag aria-hidden />
            Finish now
          </Button>
        </div>
      )}
      {!current && active && !session.complete && !turn?.error && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-dashed px-3 py-2 text-sm text-muted-foreground">
          <span className="flex-1">
            {session.nodes.length === 0
              ? "The interview has not started yet."
              : "No question is waiting for an answer."}
          </span>
          <Button size="xs" variant="outline" onClick={onAsk}>
            <IconPlayerPlay aria-hidden />
            {session.nodes.length === 0 ? "Start the interview" : "Ask the next question"}
          </Button>
        </div>
      )}
      {!active && (
        <p className="text-sm text-muted-foreground" data-testid="ideation-finished">
          Finished{session.finished_at ? ` ${formatRelative(session.finished_at)}` : ""}. Open
          branches are listed as open questions in the brief. Press Continue to add to it.
        </p>
      )}
    </>
  );
}

function SessionView({
  spaceId,
  sessionId,
  startedHere,
}: {
  spaceId: string;
  sessionId: string;
  /** Created from the idea box just now: its first question gets focus. */
  startedHere: boolean;
}) {
  const query = useIdeationSession(spaceId, sessionId);
  const { state: turn, run } = useIdeationStream(spaceId, sessionId);
  const action = useIdeationAction(spaceId, sessionId);
  const [dismissing, setDismissing] = useState<string | null>(null);
  // Focus a new question only when it follows the user's own action in this view.
  const [askedHere, setAskedHere] = useState(startedHere);
  const scrollRef = useRef<HTMLDivElement>(null);

  const session = query.data;
  const running = turn?.running ?? false;
  const answeredCount = session?.nodes.filter((n) => n.status === "resolved").length ?? 0;

  // Keep the newest step in view.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [answeredCount, running, session?.current_node_id]);

  if (query.isPending) {
    return (
      <div className="grid gap-3">
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-32" />
      </div>
    );
  }
  if (query.isError || !session) {
    return (
      <QueryError
        title="Could not open this idea"
        error={query.error}
        onRetry={() => void query.refetch()}
      />
    );
  }

  const active = session.status === "active";
  const current = session.nodes.find((n) => n.id === session.current_node_id) ?? null;

  const ask = () => {
    setAskedHere(true);
    void run(session.id);
  };
  const answer = (node: IdeationNode, act: IdeationAction, text?: string) => {
    setAskedHere(true);
    void run(session.id, {
      node_id: node.id,
      action: act,
      ...(act === "answer" ? { text } : {}),
    });
  };
  const finish = () => action.mutate("finish");

  return (
    <div className="grid min-h-0 flex-1 gap-4 overflow-y-auto lg:grid-cols-[minmax(0,1fr)_minmax(240px,300px)] lg:overflow-hidden">
      <div className="flex min-h-0 min-w-0 flex-col gap-3">
        <header className="grid gap-1.5" data-testid="idea-header">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="min-w-0 text-base font-semibold break-words">{sessionLabel(session)}</h2>
            {!active && <Badge variant="muted">Finished</Badge>}
          </div>
          <p className="line-clamp-3 text-sm whitespace-pre-wrap text-muted-foreground">
            <span className="sr-only">Idea: </span>
            {session.idea}
          </p>
          <FinishBar
            spaceId={spaceId}
            session={session}
            pending={action.isPending}
            onFinish={finish}
            onContinue={() => action.mutate("continue")}
          />
          {action.error && (
            <p role="alert" className="text-xs text-destructive">
              {errorMessage(action.error)}
            </p>
          )}
        </header>

        <div
          ref={scrollRef}
          className="grid min-h-0 content-start gap-4 lg:overflow-y-auto lg:pr-1"
        >
          <InterviewThread nodes={session.nodes} pending={turn?.pending ?? null} />
          <CurrentStep
            session={session}
            current={current}
            turn={turn}
            focusQuestion={askedHere}
            onAnswer={answer}
            onAsk={ask}
            onFinish={finish}
          />
        </div>
      </div>

      <aside aria-label="Branch map panel" className="min-h-0 lg:overflow-y-auto">
        <BranchMap
          session={session}
          canDismiss={active && !running}
          dismissingId={dismissing}
          onDismiss={(node) => {
            setDismissing(node.id);
            action.mutate({ dismiss: node.id }, { onSettled: () => setDismissing(null) });
          }}
        />
      </aside>
    </div>
  );
}

/** The Ideation tab: "I have an idea", saved sessions, the interview and the branch map. */
export function IdeationView({ spaceId }: { spaceId: string }) {
  const sessions = useIdeationSessions(spaceId);
  // undefined = follow the most recently updated session (resume after a reload); null = new idea.
  const [selected, setSelected] = useState<string | null | undefined>(undefined);
  const list = sessions.data ?? [];
  const current = selected === undefined ? (list[0]?.id ?? null) : selected;
  const { run } = useIdeationStream(spaceId, null);
  const [created, setCreated] = useState<string | null>(null);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3" data-testid="ideation-view">
      <IdeationSessionBar
        spaceId={spaceId}
        sessions={list}
        current={current}
        onSelect={(id) => setSelected(id)}
        onNew={() => setSelected(null)}
      />
      {sessions.isError && (
        <QueryError
          title="Could not load your ideas"
          error={sessions.error}
          onRetry={() => void sessions.refetch()}
        />
      )}
      {sessions.isPending ? (
        <Skeleton className="h-40" />
      ) : current === null ? (
        <div className="min-h-0 flex-1 overflow-y-auto pt-2">
          <IdeaEntry
            spaceId={spaceId}
            onCreated={(session) => {
              setSelected(session.id);
              setCreated(session.id);
              void run(session.id);
            }}
          />
        </div>
      ) : (
        <SessionView
          key={current}
          spaceId={spaceId}
          sessionId={current}
          startedHere={created === current}
        />
      )}
    </div>
  );
}
