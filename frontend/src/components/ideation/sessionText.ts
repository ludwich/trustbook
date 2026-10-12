import type { IdeationNode, IdeationSession, IdeationSessionSummary } from "@/api/types";

/** "X resolved, Y open" (SPEC §10). */
export function progressText(progress: IdeationSession["progress"]): string {
  return `${progress.resolved} resolved, ${progress.open} open`;
}

/** The session's title, else the start of its idea. */
export function sessionLabel(s: Pick<IdeationSessionSummary, "title" | "idea">): string {
  const t = s.title.trim() || s.idea.trim().replace(/\s+/g, " ");
  if (!t) return "Untitled idea";
  return t.length > 60 ? `${t.slice(0, 57)}…` : t;
}

/** Asked and answered branches in the order they were answered. */
export function answeredNodes(nodes: IdeationNode[]): IdeationNode[] {
  return nodes
    .filter((n) => n.status === "resolved" && n.question)
    .sort((a, b) => (a.answered_at ?? "").localeCompare(b.answered_at ?? ""));
}
