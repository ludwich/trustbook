import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/api/client";
import { queryKeys } from "@/api/queries";
import type { AnswerSentence, ChatStage, Conversation, Message, Refusal } from "@/api/types";
import { errorMessage } from "@/lib/utils";

export interface StreamState {
  /** "" until a new conversation has been created. */
  conversationId: string;
  question: string;
  stage: ChatStage | null;
  sentences: (AnswerSentence & { index: number })[];
  refusal: Refusal | null;
  error: string | null;
  stopped: boolean;
  running: boolean;
}

/**
 * Removes the exchange for `question` from the end of a persisted message list, so a question that
 * is still shown from the stream isn't shown twice (the server stores it before answering).
 */
export function withoutTrailingExchange(messages: Message[], question: string): Message[] {
  const n = messages.length;
  const last = messages[n - 1];
  const prev = messages[n - 2];
  const isQ = (m: Message | undefined) => m?.role === "user" && m.content.text === question;
  if (last?.role === "assistant" && isQ(prev)) return messages.slice(0, n - 2);
  if (isQ(last)) return messages.slice(0, n - 1);
  return messages;
}

function titleFrom(question: string): string {
  const q = question.trim().replace(/\s+/g, " ");
  return q.length > 60 ? `${q.slice(0, 57)}…` : q;
}

export function useChatStream(spaceId: string) {
  const qc = useQueryClient();
  const [state, setState] = useState<StreamState | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Each ask() is a run; only the latest run may change the state.
  const runRef = useRef(0);

  // Leaving the page stops the stream.
  useEffect(() => () => abortRef.current?.abort(), []);

  const ask = useCallback(
    async (
      conversationId: string | null,
      question: string,
      onConversation?: (c: Conversation) => void,
    ) => {
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      const run = ++runRef.current;
      const current = () => runRef.current === run;
      const update = (fn: (s: StreamState) => StreamState) => {
        if (current()) setState((s) => (s ? fn(s) : s));
      };
      setState({
        conversationId: conversationId ?? "",
        question,
        stage: null,
        sentences: [],
        refusal: null,
        error: null,
        stopped: false,
        running: true,
      });

      let cid = conversationId;
      let finished = false;
      try {
        if (!cid) {
          const conv = await api.createConversation(spaceId, { title: titleFrom(question) });
          cid = conv.id;
          qc.setQueryData<Message[]>(queryKeys.messages(spaceId, conv.id), []);
          qc.setQueryData<Conversation[]>(queryKeys.conversations(spaceId), (old) => [
            conv,
            ...(old ?? []).filter((c) => c.id !== conv.id),
          ]);
          update((s) => ({ ...s, conversationId: conv.id }));
          onConversation?.(conv);
        }
        const key = queryKeys.messages(spaceId, cid);

        for await (const ev of api.streamMessage(spaceId, cid, question, ac.signal)) {
          if (ac.signal.aborted) break;
          switch (ev.type) {
            case "status":
              update((s) => ({ ...s, stage: ev.stage }));
              break;
            case "sentence":
              update((s) => ({
                ...s,
                sentences: [
                  ...s.sentences.filter((x) => x.index !== ev.index),
                  { index: ev.index, text: ev.text, citations: ev.citations },
                ].sort((a, b) => a.index - b.index),
              }));
              break;
            case "refused":
              update((s) => ({
                ...s,
                stage: null,
                refusal: { reason: ev.reason, message: ev.message, suggestion: ev.suggestion },
              }));
              break;
            case "error":
              update((s) => ({ ...s, stage: null, error: ev.message || "Something went wrong." }));
              break;
            case "done": {
              finished = true;
              const local: Message = {
                id: `local-${Date.now()}`,
                role: "user",
                created_at: new Date().toISOString(),
                content: { text: question },
                badge: null,
                removed_sentences: [],
                sources_used: [],
              };
              qc.setQueryData<Message[]>(key, (old) => [
                ...withoutTrailingExchange(old ?? [], question),
                local,
                ev.message,
              ]);
              if (current()) setState(null);
              break;
            }
          }
        }
        if (ac.signal.aborted) {
          update((s) => ({ ...s, running: false, stage: null, stopped: true }));
        } else if (!finished) {
          update((s) =>
            s.refusal || s.error
              ? { ...s, running: false, stage: null }
              : {
                  ...s,
                  running: false,
                  stage: null,
                  error: "The answer stopped before it was finished. Try again.",
                },
          );
        }
      } catch (e) {
        if (ac.signal.aborted) {
          update((s) => ({ ...s, running: false, stage: null, stopped: true }));
        } else {
          update((s) => ({ ...s, running: false, stage: null, error: errorMessage(e) }));
        }
      } finally {
        if (abortRef.current === ac) abortRef.current = null;
      }

      // Sync with what the server stored. If it stored the full exchange, drop the stream view.
      if (cid) {
        const key = queryKeys.messages(spaceId, cid);
        void qc.invalidateQueries({ queryKey: queryKeys.conversations(spaceId) });
        await qc.invalidateQueries({ queryKey: key });
        if (!finished) {
          const msgs = qc.getQueryData<Message[]>(key) ?? [];
          const stored =
            msgs.at(-1)?.role === "assistant" &&
            withoutTrailingExchange(msgs, question).length === msgs.length - 2;
          if (stored && current()) setState(null);
        }
      }
    },
    [qc, spaceId],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);
  const clear = useCallback(() => {
    runRef.current++;
    abortRef.current?.abort();
    setState(null);
  }, []);

  return { state, ask, stop, clear };
}
