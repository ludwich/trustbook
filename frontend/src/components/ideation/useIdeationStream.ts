import { notifyManager, useQueryClient } from "@tanstack/react-query";
import { useCallback, useSyncExternalStore } from "react";
import { api } from "@/api/client";
import { queryKeys, storeIdeationSession } from "@/api/queries";
import type { ChatStage, IdeationAnswer } from "@/api/types";
import { errorMessage } from "@/lib/utils";

export interface TurnState {
  running: boolean;
  stage: ChatStage | null;
  /** The answer being sent; shown in the thread until the stored session has it. */
  pending: IdeationAnswer | null;
  /** Plain English, from the stream's `error` event or a refused request (e.g. 409). */
  error: string | null;
}

export const STOPPED_EARLY = "The next question stopped before it was ready. Press Ask again.";

/*
 * Turn state lives outside React: the server finishes a turn even when the page stops listening,
 * and the Ideation tab unmounts when the user looks at Chat. Keeping the stream (and its state)
 * here lets a running turn carry on and show again when the tab comes back.
 */
const turns = new Map<string, TurnState>();
const listeners = new Set<() => void>();

function turnKey(spaceId: string, sessionId: string): string {
  return `${spaceId}\u0000${sessionId}`;
}

function setTurn(key: string, state: TurnState | null): void {
  if (state) turns.set(key, state);
  else turns.delete(key);
  for (const l of listeners) l();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Forgets every turn state (tests). */
export function resetIdeationStreams(): void {
  turns.clear();
  for (const l of listeners) l();
}

/**
 * The turn of one session: `run()` asks the next question (seeding the map first when it is
 * empty); `run(answer)` stores an answer and then asks the next question.
 */
export function useIdeationStream(spaceId: string, sessionId: string | null) {
  const qc = useQueryClient();
  const state = useSyncExternalStore(subscribe, () =>
    sessionId ? (turns.get(turnKey(spaceId, sessionId)) ?? null) : null,
  );

  const run = useCallback(
    async (id: string, answer?: IdeationAnswer) => {
      const key = turnKey(spaceId, id);
      if (turns.get(key)?.running) return;
      const sessionKey = queryKeys.ideationSession(spaceId, id);
      const update = (patch: Partial<TurnState>) => {
        const cur = turns.get(key);
        if (cur) setTurn(key, { ...cur, ...patch });
      };
      setTurn(key, { running: true, stage: null, pending: answer ?? null, error: null });

      let done = false;
      try {
        // The answer is stored before the stream starts: show it in the thread and the map.
        const onOpen = answer
          ? () => void qc.invalidateQueries({ queryKey: sessionKey, exact: true })
          : undefined;
        const events = answer
          ? api.streamIdeationAnswer(spaceId, id, answer, { onOpen })
          : api.streamIdeationTurn(spaceId, id, { onOpen });
        for await (const ev of events) {
          if (ev.type === "status") {
            update({ stage: ev.stage });
          } else if (ev.type === "error") {
            update({ error: ev.message || "Something went wrong. Press Ask again." });
          } else {
            done = true;
            // A refetch started at onOpen must not overwrite the newer state.
            await qc.cancelQueries({ queryKey: sessionKey, exact: true });
            storeIdeationSession(qc, spaceId, ev.session);
          }
        }
        if (!done && !turns.get(key)?.error) update({ error: STOPPED_EARLY });
      } catch (e) {
        update({ error: errorMessage(e) });
      } finally {
        const cur = turns.get(key);
        const next = cur?.error ? { ...cur, running: false, stage: null, pending: null } : null;
        // Query observers hear about the stored session on a later tick. Clear the turn after
        // them, so the question just answered never shows again for a moment.
        notifyManager.schedule(() => setTurn(key, next));
      }
      if (!done) {
        // Whatever happened, show what the server stored (an answer is kept after an error).
        void qc.invalidateQueries({ queryKey: sessionKey, exact: true });
        void qc.invalidateQueries({ queryKey: queryKeys.ideationSessions(spaceId), exact: true });
      }
    },
    [qc, spaceId],
  );

  return { state, run };
}
