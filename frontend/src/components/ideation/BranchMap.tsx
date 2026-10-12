import { IconCircleCheck, IconPlayerSkipForward, IconX } from "@tabler/icons-react";
import { useId } from "react";
import type { IdeationNode, IdeationRisk, IdeationSession } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { progressText } from "./sessionText";

type MapSession = Pick<
  IdeationSession,
  "nodes" | "current_node_id" | "progress" | "complete" | "risks"
>;

/** Live branches grouped by parent, in the server's order (tree pre-order). */
function childrenByParent(nodes: IdeationNode[]): Map<string | null, IdeationNode[]> {
  const live = nodes.filter((n) => n.status !== "dismissed");
  const ids = new Set(live.map((n) => n.id));
  const map = new Map<string | null, IdeationNode[]>();
  for (const n of live) {
    // A parent the map no longer shows: keep the branch visible at the top level.
    const parent = n.parent_id !== null && ids.has(n.parent_id) ? n.parent_id : null;
    const list = map.get(parent) ?? [];
    list.push(n);
    map.set(parent, list);
  }
  return map;
}

function stateLabel(node: IdeationNode, current: boolean): string {
  if (current) return "current question";
  if (node.status === "resolved") return node.answer_source === "skipped" ? "skipped" : "answered";
  if (node.status === "open") return "asked";
  return "not asked yet";
}

function MapNode({
  node,
  tree,
  currentId,
  canDismiss,
  dismissingId,
  onDismiss,
}: {
  node: IdeationNode;
  tree: Map<string | null, IdeationNode[]>;
  currentId: string | null;
  canDismiss: boolean;
  dismissingId: string | null;
  onDismiss: (node: IdeationNode) => void;
}) {
  const current = node.id === currentId;
  const resolved = node.status === "resolved";
  const skipped = resolved && node.answer_source === "skipped";
  const kids = tree.get(node.id) ?? [];
  return (
    <li className="grid gap-1.5">
      <div
        data-testid="map-node"
        data-node-id={node.id}
        data-status={node.status}
        data-current={current ? "true" : undefined}
        aria-current={current ? "step" : undefined}
        className={cn(
          "group flex min-h-8 items-center gap-1.5 rounded-md border px-2 py-1 text-sm transition-colors",
          node.status === "tentative" && "border-dashed border-muted-foreground/50 bg-transparent",
          resolved && "border-solid border-border bg-card",
          skipped && "text-muted-foreground",
          node.status === "open" && "border-solid border-border bg-card",
          current && "border-primary bg-info-soft font-medium ring-2 ring-primary/30",
        )}
      >
        {resolved &&
          (skipped ? (
            <IconPlayerSkipForward className="size-3.5 shrink-0" aria-hidden />
          ) : (
            <IconCircleCheck className="size-3.5 shrink-0 text-success" aria-hidden />
          ))}
        <span className="min-w-0 flex-1 break-words">{node.title}</span>
        <span className="sr-only">({stateLabel(node, current)})</span>
        {node.dismissible && canDismiss && (
          <button
            type="button"
            onClick={() => onDismiss(node)}
            disabled={dismissingId !== null}
            aria-label={`Dismiss branch “${node.title}”`}
            title="Dismiss this branch"
            className="-mr-1 inline-flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none disabled:opacity-50"
          >
            <IconX className="size-3.5" aria-hidden />
          </button>
        )}
      </div>
      {kids.length > 0 && (
        <ul className="ml-3 grid gap-1.5 border-l pl-3" aria-label={`Under “${node.title}”`}>
          {kids.map((k) => (
            <MapNode
              key={k.id}
              node={k}
              tree={tree}
              currentId={currentId}
              canDismiss={canDismiss}
              dismissingId={dismissingId}
              onDismiss={onDismiss}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function Risks({ risks }: { risks: IdeationRisk[] }) {
  if (risks.length === 0) return null;
  return (
    <div className="grid gap-1 border-t pt-2">
      <h4 className="text-xs font-medium text-muted-foreground">Risks raised</h4>
      <ul className="grid list-disc gap-0.5 pl-4 text-sm" aria-label="Risks raised">
        {risks.map((r, i) => (
          <li key={i}>{r.text}</li>
        ))}
      </ul>
    </div>
  );
}

/**
 * The growing branch map beside the interview: tentative branches dashed, resolved ones solid,
 * the current question highlighted, × on branches that can still be dismissed.
 */
export function BranchMap({
  session,
  canDismiss,
  dismissingId = null,
  onDismiss,
}: {
  session: MapSession;
  /** False while a turn runs or when the session is finished. */
  canDismiss: boolean;
  dismissingId?: string | null;
  onDismiss: (node: IdeationNode) => void;
}) {
  const titleId = useId();
  const tree = childrenByParent(session.nodes);
  const roots = tree.get(null) ?? [];

  return (
    <section
      aria-labelledby={titleId}
      data-testid="branch-map"
      data-complete={session.complete ? "true" : undefined}
      className="grid gap-3 rounded-lg border bg-card/50 p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id={titleId} className="text-sm font-semibold">
          Branch map
        </h3>
        <div className="flex items-center gap-1.5">
          {session.complete && (
            <Badge variant="success" data-testid="map-complete">
              <IconCircleCheck aria-hidden />
              Complete
            </Badge>
          )}
          <span className="text-xs text-muted-foreground tabular-nums" data-testid="map-progress">
            {progressText(session.progress)}
          </span>
        </div>
      </div>

      {roots.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          The first branches appear with the first question.
        </p>
      ) : (
        <ul className="grid gap-1.5" aria-label="Branches">
          {roots.map((n) => (
            <MapNode
              key={n.id}
              node={n}
              tree={tree}
              currentId={session.current_node_id}
              canDismiss={canDismiss}
              dismissingId={dismissingId}
              onDismiss={onDismiss}
            />
          ))}
        </ul>
      )}

      {roots.length > 0 && (
        <ul
          className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground"
          aria-label="Legend"
        >
          <li className="flex items-center gap-1">
            <span className="inline-block h-2.5 w-4 rounded-sm border border-dashed border-muted-foreground/60" />
            Not asked yet
          </li>
          <li className="flex items-center gap-1">
            <span className="inline-block h-2.5 w-4 rounded-sm border border-border bg-card" />
            Answered
          </li>
          <li className="flex items-center gap-1">
            <span className="inline-block h-2.5 w-4 rounded-sm border border-primary bg-info-soft" />
            Current question
          </li>
        </ul>
      )}

      <Risks risks={session.risks} />
    </section>
  );
}
