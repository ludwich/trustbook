import { IconBulb, IconPlayerPlay } from "@tabler/icons-react";
import { useId, useState, type FormEvent } from "react";
import { useChatStatus, useCreateIdeationSession, useIdeationEstimate } from "@/api/queries";
import type { IdeationSession } from "@/api/types";
import { CapabilityGate } from "@/components/capabilities/CapabilityGate";
import { useCapabilityReady } from "@/components/capabilities/useCapabilityReady";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { localityOfUrl } from "@/lib/locality";
import { errorMessage } from "@/lib/utils";

export const MAX_IDEA_CHARS = 4000;

/** "About N tokens per question are sent to <host>", only when the chat endpoint is remote. */
export function IdeationEstimateNote({ spaceId }: { spaceId: string }) {
  const estimate = useIdeationEstimate(spaceId);
  const chat = useCapabilityReady("chat");
  const e = estimate.data;
  if (!e?.remote) return null;
  const host = localityOfUrl(chat.capability?.config.base_url ?? "")?.host;
  const where = host ? host : "your chat endpoint";
  return (
    <p className="text-xs text-muted-foreground" data-testid="ideation-estimate">
      About {e.tokens_per_turn.toLocaleString("en-US")} tokens per question are sent to {where}
      {e.seed_tokens > 0 && `, plus about ${e.seed_tokens.toLocaleString("en-US")} once to start`}.
    </p>
  );
}

/** The "I have an idea" box: describe an idea and start a session. */
export function IdeaEntry({
  spaceId,
  onCreated,
}: {
  spaceId: string;
  onCreated: (session: IdeationSession) => void;
}) {
  const [idea, setIdea] = useState("");
  const create = useCreateIdeationSession(spaceId);
  const status = useChatStatus(spaceId);
  const ideaId = useId();
  const noteId = useId();
  const noSources = status.data !== undefined && status.data.enabled_sources === 0;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const text = idea.trim();
    if (!text || create.isPending) return;
    create.mutate(text, {
      onSuccess: (session) => {
        setIdea("");
        onCreated(session);
      },
    });
  };

  return (
    <Card className="mx-auto w-full max-w-2xl gap-3 p-5" data-testid="idea-entry">
      <div className="grid gap-1">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <IconBulb className="size-5 text-primary" aria-hidden />
          <label htmlFor={ideaId}>I have an idea</label>
        </h2>
        <p id={noteId} className="text-sm text-muted-foreground">
          Describe it in a few sentences. Trustbook asks one question at a time, each with a
          recommended answer that cites your sources where they support it, and writes a brief at
          the end. Ideas and briefs never become sources.
        </p>
      </div>
      <CapabilityGate capability="chat" feature="Ideation">
        <form onSubmit={submit} className="grid gap-2">
          <Textarea
            id={ideaId}
            value={idea}
            onChange={(e) => setIdea(e.target.value)}
            maxLength={MAX_IDEA_CHARS}
            rows={4}
            aria-describedby={noteId}
            placeholder="For example: guided puffin walks on Gull Island for birdwatchers"
            className="min-h-24 resize-y"
          />
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="grid gap-0.5">
              <IdeationEstimateNote spaceId={spaceId} />
              {noSources && (
                <p className="text-xs text-muted-foreground">
                  No ticked, ready sources: every recommendation will be a suggestion.
                </p>
              )}
            </div>
            <Button type="submit" size="sm" disabled={!idea.trim() || create.isPending}>
              <IconPlayerPlay aria-hidden />
              Start
            </Button>
          </div>
          {create.error && (
            <p role="alert" className="text-xs text-destructive">
              {errorMessage(create.error)}
            </p>
          )}
        </form>
      </CapabilityGate>
    </Card>
  );
}
