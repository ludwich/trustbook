import { IconCheck, IconPlayerSkipForward } from "@tabler/icons-react";
import type { IdeationAnswer, IdeationNode } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { RecommendationText } from "./RecommendationText";
import { answeredNodes } from "./sessionText";

function Answer({ node }: { node: IdeationNode }) {
  if (node.answer_source === "skipped") {
    return (
      <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
        <IconPlayerSkipForward className="size-3.5" aria-hidden />
        Skipped. The brief lists it as an open question.
      </p>
    );
  }
  if (node.answer_source === "accepted") {
    const sentences = node.recommendation?.sentences ?? [];
    return (
      <div className="grid gap-1">
        <Badge variant="info" data-testid="accepted-tag">
          <IconCheck aria-hidden />
          Accepted recommendation
        </Badge>
        {sentences.length > 0 ? (
          <RecommendationText sentences={sentences} className="text-sm" />
        ) : (
          <p className="text-sm whitespace-pre-wrap">{node.final_answer}</p>
        )}
      </div>
    );
  }
  return (
    <p className="text-sm whitespace-pre-wrap">
      <span className="sr-only">You answered: </span>
      {node.final_answer}
    </p>
  );
}

function pendingText(pending: IdeationAnswer): string {
  if (pending.action === "accept") return "Accepting the recommendation…";
  if (pending.action === "skip") return "Skipping…";
  return pending.text ?? "";
}

/** The interview so far: each answered question with its answer (accepted ones marked). */
export function InterviewThread({
  nodes,
  pending = null,
}: {
  nodes: IdeationNode[];
  /** An answer on its way; shown until the stored session has it. */
  pending?: IdeationAnswer | null;
}) {
  const answered = answeredNodes(nodes);
  const pendingNode =
    pending && nodes.find((n) => n.id === pending.node_id && n.status !== "resolved");
  if (answered.length === 0 && !pendingNode) return null;

  return (
    <ol className="grid gap-3" aria-label="Interview so far" data-testid="interview-thread">
      {answered.map((n) => (
        <li
          key={n.id}
          className="grid gap-1.5 border-l-2 pl-3"
          data-testid="thread-item"
          data-answer-source={n.answer_source ?? undefined}
        >
          <p className="text-sm font-medium">{n.question}</p>
          <Answer node={n} />
        </li>
      ))}
      {pending && pendingNode && (
        <li className="grid gap-1.5 border-l-2 border-dashed pl-3 opacity-70" aria-busy="true">
          <p className="text-sm font-medium">{pendingNode.question}</p>
          <p className="text-sm whitespace-pre-wrap">{pendingText(pending)}</p>
        </li>
      )}
    </ol>
  );
}
