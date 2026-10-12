import { IconDownload, IconFlag, IconPlayerPlay } from "@tabler/icons-react";
import { api } from "@/api/client";
import type { IdeationSession } from "@/api/types";
import { Button } from "@/components/ui/button";
import { formatRelative } from "@/lib/format";

/** Finish now (writes the brief at any time), Export Markdown, and Continue a finished session. */
export function FinishBar({
  spaceId,
  session,
  pending,
  onFinish,
  onContinue,
}: {
  spaceId: string;
  session: Pick<IdeationSession, "id" | "status" | "brief_at" | "complete">;
  /** Finish or Continue is being sent. */
  pending: boolean;
  onFinish: () => void;
  onContinue: () => void;
}) {
  const active = session.status === "active";
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="finish-bar">
      {active ? (
        <Button
          size="sm"
          variant={session.complete ? "default" : "outline"}
          disabled={pending}
          onClick={onFinish}
        >
          <IconFlag aria-hidden />
          Finish now
        </Button>
      ) : (
        <Button size="sm" variant="outline" disabled={pending} onClick={onContinue}>
          <IconPlayerPlay aria-hidden />
          Continue
        </Button>
      )}
      {session.brief_at && (
        <>
          <Button asChild size="sm" variant={active ? "ghost" : "default"}>
            <a href={api.ideationBriefUrl(spaceId, session.id)} download>
              <IconDownload aria-hidden />
              Export Markdown
            </a>
          </Button>
          <span className="text-xs text-muted-foreground">
            Brief written {formatRelative(session.brief_at)}
            {active && ". Finish again to include new answers."}
          </span>
        </>
      )}
    </div>
  );
}
