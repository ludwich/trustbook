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

/** Group checkbox state: all ticked, none, or some ("mixed"). */
export function groupChecked(sources: Source[]): boolean | "mixed" {
  const on = sources.filter((s) => s.enabled).length;
  if (on === 0) return false;
  return on === sources.length ? true : "mixed";
}
