import { IconCheck, IconPlayerSkipForward, IconSend } from "@tabler/icons-react";
import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { IdeationAction, IdeationNode } from "@/api/types";
import { AnswerBadge, RemovedSentences } from "@/components/chat/AnswerParts";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { RecommendationText, SUGGESTION_NOTE } from "./RecommendationText";

export const MAX_ANSWER_CHARS = 4000;
export const NO_RECOMMENDATION = "No recommended answer for this question. Type an answer or skip.";

/**
 * The question waiting for an answer: the question, its recommended answer (cited sentences,
 * "Suggestion" sentences and the drawer of removed ones) and Accept / Skip / Answer.
 * Questions and branch titles are prompts, never shown as grounded (no badge, no citations).
 */
export function QuestionCard({
  node,
  canAnswer,
  busy = false,
  focusOnMount = false,
  onAnswer,
}: {
  node: IdeationNode;
  /** False for finished sessions: the question is shown without actions. */
  canAnswer: boolean;
  /** A request is being sent; actions are disabled. */
  busy?: boolean;
  /** Move focus to the question (a new question after the user's own action). */
  focusOnMount?: boolean;
  onAnswer: (action: IdeationAction, text?: string) => void;
}) {
  const [text, setText] = useState("");
  const headingId = useId();
  const answerId = useId();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const rec = node.recommendation;
  const sentences = rec?.sentences ?? [];
  const removed = rec?.removed ?? [];
  const hasSuggestion = sentences.some((s) => s.kind === "suggestion");
  const canAccept = sentences.length > 0;

  // Only when the card first shows this question.
  const focusFirst = useRef(focusOnMount);
  useEffect(() => {
    if (focusFirst.current) headingRef.current?.focus();
  }, []);

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    onAnswer("answer", t);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <section
      aria-labelledby={headingId}
      data-testid="question-card"
      data-node-id={node.id}
      className="grid gap-3 rounded-lg border border-primary/40 bg-card p-4 shadow-xs"
    >
      <div className="grid gap-1">
        <p className="text-xs text-muted-foreground">
          <span className="sr-only">Branch: </span>
          {node.title}
        </p>
        <h3
          id={headingId}
          ref={headingRef}
          tabIndex={-1}
          className="leading-snug font-medium outline-none"
          data-testid="question-text"
        >
          {node.question ?? node.title}
        </h3>
      </div>

      <div className="grid gap-2 rounded-md bg-muted/40 px-3 py-2.5" data-testid="recommendation">
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="text-xs font-medium text-muted-foreground">Recommended answer</h4>
          {rec?.badge && <AnswerBadge badge={rec.badge} removed={removed.length} />}
        </div>
        {sentences.length > 0 ? (
          <RecommendationText sentences={sentences} className="text-sm" />
        ) : (
          <p className="text-sm text-muted-foreground">{NO_RECOMMENDATION}</p>
        )}
        {hasSuggestion && <p className="text-xs text-muted-foreground">{SUGGESTION_NOTE}</p>}
        <RemovedSentences sentences={removed} />
      </div>

      {canAnswer && (
        <form onSubmit={submit} className="grid gap-2">
          <label htmlFor={answerId} className="text-xs font-medium text-muted-foreground">
            Your answer
          </label>
          <Textarea
            id={answerId}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={busy}
            maxLength={MAX_ANSWER_CHARS}
            rows={2}
            placeholder="Type your own answer, or accept the recommendation"
            className="max-h-40 min-h-14 resize-y"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" size="sm" disabled={busy || !text.trim()}>
              <IconSend aria-hidden />
              Answer
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={busy || !canAccept}
              title={canAccept ? undefined : "There is no recommended answer to accept."}
              onClick={() => onAnswer("accept")}
            >
              <IconCheck aria-hidden />
              Accept recommendation
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => onAnswer("skip")}
            >
              <IconPlayerSkipForward aria-hidden />
              Skip
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
