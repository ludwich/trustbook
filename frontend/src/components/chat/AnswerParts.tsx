import {
  IconBulb,
  IconChevronDown,
  IconCircleCheck,
  IconFileSearch,
  IconInfoCircle,
  IconShieldQuestion,
} from "@tabler/icons-react";
import { useId, useState } from "react";
import type { AnswerSentence, Citation, MessageBadge } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { formatLocator } from "@/lib/locator";
import { cn } from "@/lib/utils";
import { useCitationViewer } from "./useCitationViewer";

export function CitationButton({ citation }: { citation: Citation }) {
  const viewer = useCitationViewer();
  const where = formatLocator(citation.locator);
  const label = `${citation.source_title}${where ? `, ${where}` : ""}`;
  return (
    <button
      type="button"
      onClick={() => viewer.open(citation)}
      aria-label={`Citation ${citation.n}: ${label}`}
      title={label}
      className="mx-px inline-flex h-4.5 min-w-4.5 translate-y-[-1px] items-center justify-center rounded-sm bg-info-soft px-1 align-baseline text-[11px] leading-none font-semibold text-primary tabular-nums transition-colors hover:bg-primary hover:text-primary-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
    >
      {citation.n}
    </button>
  );
}

/** Sentences as one paragraph, each followed by its numbered citations. */
export function CitedText({
  sentences,
  className,
}: {
  sentences: AnswerSentence[];
  className?: string;
}) {
  return (
    <p className={cn("leading-relaxed", className)}>
      {sentences.map((s, i) => (
        <span key={i} data-testid="answer-sentence">
          {s.text}
          {s.citations.length > 0 && " "}
          {s.citations.map((c) => (
            <CitationButton key={`${c.n}-${c.chunk_id}`} citation={c} />
          ))}
          {i < sentences.length - 1 && " "}
        </span>
      ))}
    </p>
  );
}

const BADGES: Record<
  MessageBadge,
  {
    label: string;
    variant: "success" | "warning" | "muted" | "danger";
    icon: typeof IconCircleCheck;
  }
> = {
  verified: { label: "Verified", variant: "success", icon: IconCircleCheck },
  partially_verified: { label: "Partially verified", variant: "warning", icon: IconInfoCircle },
  refused: { label: "Refused", variant: "muted", icon: IconFileSearch },
  unverified: { label: "Unverified", variant: "muted", icon: IconShieldQuestion },
};

export const UNVERIFIED_TIP = "Answer checking is turned off in Settings";

export function AnswerBadge({ badge, removed = 0 }: { badge: MessageBadge; removed?: number }) {
  const tipId = useId();
  const b = BADGES[badge];
  const Icon = b.icon;
  const unverified = badge === "unverified";
  const el = (
    <Badge
      variant={b.variant}
      data-testid="answer-badge"
      data-badge={badge}
      tabIndex={unverified ? 0 : undefined}
      aria-describedby={unverified ? tipId : undefined}
      className={cn(
        unverified && "cursor-help focus-visible:ring-[3px] focus-visible:ring-ring/50",
      )}
    >
      <Icon aria-hidden />
      {b.label}
      {badge === "partially_verified" && removed > 0 && " "}
      {badge === "partially_verified" && removed > 0 && (
        <span className="font-normal">
          · {removed} {removed === 1 ? "statement" : "statements"} removed
        </span>
      )}
    </Badge>
  );
  if (!unverified) return el;
  // Small CSS tooltip (hover or keyboard focus); the text is also the badge's description.
  return (
    <span className="group relative inline-flex w-fit">
      {el}
      <span
        role="tooltip"
        id={tipId}
        className="pointer-events-none invisible absolute top-full left-0 z-20 mt-1 w-max max-w-64 rounded-md bg-foreground px-2 py-1 text-xs text-background opacity-0 shadow-md transition-opacity group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100"
      >
        {UNVERIFIED_TIP}
      </span>
    </span>
  );
}

export const IDEATION_HINT = "Brainstorming? The Ideation tab is made for that.";

/** Shown under an answer when the gate thought the question was brainstorming. */
export function IdeationHint({ onOpenIdeation }: { onOpenIdeation?: () => void }) {
  return (
    <p
      className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground"
      data-testid="ideation-hint"
    >
      <IconBulb className="size-4 shrink-0" aria-hidden />
      <span>{IDEATION_HINT}</span>
      {onOpenIdeation && (
        <button
          type="button"
          onClick={onOpenIdeation}
          className="font-medium text-primary underline-offset-2 hover:underline focus-visible:underline focus-visible:outline-none"
        >
          Open Ideation
        </button>
      )}
    </p>
  );
}

export const NOT_IN_SOURCES = "Not in your sources";

/**
 * Calm card for "the sources don't answer this" (G5). The title is the refusal message
 * ("Not in your sources", or e.g. "No answer with citations" for unusable model output); the only
 * subtitle is the suggestion, and nothing repeats the title. No suggestion → no subtitle.
 */
export function RefusalCard({
  message,
  suggestion,
}: {
  message: string | null | undefined;
  suggestion: string | null | undefined;
}) {
  const title = message?.trim() || NOT_IN_SOURCES;
  const sub = suggestion?.trim();
  const showSub = Boolean(sub) && !sameText(sub, title);
  return (
    <div className="grid gap-1 rounded-lg border bg-muted/50 px-4 py-3" data-testid="refusal-card">
      <p className="flex items-center gap-1.5 font-medium" data-testid="refusal-title">
        <IconFileSearch className="size-4 text-muted-foreground" aria-hidden />
        {title}
      </p>
      {showSub && (
        <p className="text-sm text-muted-foreground" data-testid="refusal-suggestion">
          {sub}
        </p>
      )}
    </div>
  );
}

function sameText(a: string | undefined, b: string): boolean {
  const norm = (t: string) =>
    t
      .toLowerCase()
      .replace(/[.!\s]+$/u, "")
      .trim();
  return a !== undefined && norm(a) === norm(b);
}

export const REMOVED_TITLE = "Unverified, not from your sources";

/**
 * Collapsed drawer with the statements removed by answer checking (partially verified answers).
 * Kept apart from the answer and muted, so nobody mistakes them for part of it.
 */
export function RemovedSentences({ sentences }: { sentences: string[] }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  if (sentences.length === 0) return null;
  return (
    <section
      aria-label={REMOVED_TITLE}
      className="mt-1 rounded-md border border-dashed bg-muted/30 text-sm"
      data-testid="removed-sentences"
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1.5 rounded-md px-3 py-1.5 text-left text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        <IconChevronDown
          className={cn("size-3.5 transition-transform", open && "rotate-180")}
          aria-hidden
        />
        {REMOVED_TITLE}
        <span className="ml-auto tabular-nums">{sentences.length}</span>
      </button>
      {open && (
        <div id={panelId} className="border-t border-dashed px-3 py-2">
          <p className="mb-1.5 text-xs text-muted-foreground">
            Removed from the answer because your sources don&apos;t support them. Don&apos;t rely on
            them.
          </p>
          <ul
            className="grid list-disc gap-1 pl-5 text-muted-foreground"
            aria-label="Removed statements"
          >
            {sentences.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

interface UsedSource {
  source_id: string;
  title: string;
}

/** "Show sources used" chip that expands to the list, with each source's citation numbers. */
export function SourcesUsed({
  sentences,
  sourcesUsed,
}: {
  sentences: AnswerSentence[];
  sourcesUsed?: UsedSource[];
}) {
  const [open, setOpen] = useState(false);
  const listId = useId();

  // Citations per source, first use first.
  const bySource = new Map<string, Citation[]>();
  for (const s of sentences) {
    for (const c of s.citations) {
      const list = bySource.get(c.source_id) ?? [];
      if (!list.some((x) => x.n === c.n)) list.push(c);
      bySource.set(c.source_id, list);
    }
  }
  const used: UsedSource[] =
    sourcesUsed && sourcesUsed.length > 0
      ? sourcesUsed
      : [...bySource.entries()].map(([source_id, cs]) => ({
          source_id,
          title: cs[0]?.source_title ?? "",
        }));
  if (used.length === 0) return null;

  return (
    <div className="grid gap-1.5">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex w-fit items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        {`${open ? "Hide" : "Show"} sources used (${used.length})`}
        <IconChevronDown
          className={cn("size-3.5 transition-transform", open && "rotate-180")}
          aria-hidden
        />
      </button>
      {open && (
        <ul id={listId} aria-label="Sources used" className="grid gap-1 text-sm">
          {used.map((u) => (
            <li key={u.source_id} className="flex flex-wrap items-center gap-1.5">
              <span className="min-w-0 truncate">{u.title}</span>
              {(bySource.get(u.source_id) ?? [])
                .sort((a, b) => a.n - b.n)
                .map((c) => (
                  <CitationButton key={c.n} citation={c} />
                ))}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
