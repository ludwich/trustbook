import type { Source, SourceGroup, SourceStatus } from "@/api/types";
import { formatClock, plural } from "@/lib/format";

export const GROUP_ORDER: SourceGroup[] = ["documents", "transcripts", "web", "code"];

export const GROUP_LABELS: Record<SourceGroup, string> = {
  documents: "Documents",
  transcripts: "Transcripts",
  web: "Web",
  code: "Code",
};

export const STATUS_TEXT: Record<SourceStatus, string> = {
  queued: "Waiting",
  reading: "Reading",
  indexing: "Indexing",
  ready: "Ready",
  failed: "Failed",
};

export const TYPE_LABELS: Record<Source["type"], string> = {
  upload: "File",
  folder: "Folder",
  url: "Web page",
  youtube: "YouTube video",
  search: "Web page",
  paste: "Pasted text",
};

/** "12 pages · 85 passages" style summary of a ready source. */
export function statsLine(source: Source): string {
  const s = source.stats;
  const parts: string[] = [];
  if (s.files !== undefined) parts.push(plural(s.files, "file"));
  if (s.pages !== undefined) parts.push(plural(s.pages, "page"));
  if (s.duration_s !== undefined) parts.push(formatClock(s.duration_s));
  if (s.chunks !== undefined) parts.push(plural(s.chunks, "passage"));
  return parts.join(" · ");
}

/** Sources whose snapshot is a dated copy of something online. */
export function isWebSnapshot(source: Source): boolean {
  return source.type === "url" || source.type === "youtube" || source.type === "search";
}

/** Sources that can be read again from their origin. */
export function canRefresh(source: Source): boolean {
  return isWebSnapshot(source) || source.type === "folder";
}

/** Splits sources into the fixed group order, leaving out empty groups. */
export function groupSources(sources: Source[]): { group: SourceGroup; sources: Source[] }[] {
  return GROUP_ORDER.map((group) => ({
    group,
    sources: sources.filter((s) => s.group === group),
  })).filter((g) => g.sources.length > 0);
}

/**
 * Sources the user can tick: everything except failed ones (G7). A failed source is stored
 * unticked by the backend and can't be ticked, so it takes no part in group state or counts.
 */
export function isSelectable(source: Source): boolean {
  return source.status !== "failed";
}

export function selectableSources(sources: Source[]): Source[] {
  return sources.filter(isSelectable);
}

/** Group checkbox state over the tickable sources: all ticked, none, or some ("mixed"). */
export function groupChecked(sources: Source[]): boolean | "mixed" {
  const usable = selectableSources(sources);
  const on = usable.filter((s) => s.enabled).length;
  if (on === 0) return false;
  return on === usable.length ? true : "mixed";
}

const SECRETS_SENTENCE = /\b\d+\s+files?\s+skipped:\s*possible secrets\.?/gi;

/**
 * The quality warning to show under a source. When the source has skipped secrets, the backend's
 * "N files skipped: possible secrets." sentence is dropped here because the row shows it once,
 * as a button that opens the report (G4).
 */
export function visibleQualityWarning(source: Source): string | null {
  const warning = source.quality_warning;
  if (!warning) return null;
  if (!(source.stats.skipped_secrets && source.stats.skipped_secrets > 0)) return warning;
  const rest = warning
    .replace(SECRETS_SENTENCE, "")
    .replace(/\s{2,}/g, " ")
    .trim();
  return rest || null;
}
