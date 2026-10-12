import { IconBulb } from "@tabler/icons-react";
import type { RecommendationSentence } from "@/api/types";
import { CitationButton } from "@/components/chat/AnswerParts";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export const SUGGESTION_LABEL = "Suggestion";
export const SUGGESTION_NOTE =
  "Sentences marked Suggestion are the model's own ideas. They are not from your sources.";

/** The visible tag after an uncited recommendation sentence. */
export function SuggestionTag() {
  return (
    <Badge
      variant="warning"
      data-testid="suggestion-tag"
      title="Not from your sources"
      className="mx-0.5 translate-y-[-1px] px-1.5 py-0 align-baseline text-[11px]"
    >
      <IconBulb aria-hidden />
      {SUGGESTION_LABEL}
    </Badge>
  );
}

/**
 * A recommendation as one paragraph. Cited sentences carry their numbered citations (they open the
 * source viewer, as in chat); suggestions carry the "Suggestion" tag and never a number.
 */
export function RecommendationText({
  sentences,
  className,
}: {
  sentences: RecommendationSentence[];
  className?: string;
}) {
  return (
    <p className={cn("leading-relaxed", className)} data-testid="recommendation-text">
      {sentences.map((s, i) => (
        <span key={i} data-testid="recommendation-sentence" data-kind={s.kind}>
          {s.text}{" "}
          {s.kind === "suggestion" ? (
            <SuggestionTag />
          ) : (
            s.citations.map((c) => <CitationButton key={`${c.n}-${c.chunk_id}`} citation={c} />)
          )}
          {i < sentences.length - 1 && " "}
        </span>
      ))}
    </p>
  );
}
