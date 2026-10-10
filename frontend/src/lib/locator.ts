import type { Locator } from "@/api/types";
import { formatClock } from "./format";

/**
 * Where a passage is in its source, in plain words:
 * "Page 3", "Pages 3–4", "01:01–01:35 · Anna", "Section: Install", "src/api.py, lines 12–58".
 * Returns "" when the locator says nothing useful.
 */
export function formatLocator(loc: Locator | null | undefined): string {
  if (!loc) return "";
  if (loc.file) {
    const parts = [loc.file];
    if (loc.start_line !== undefined) {
      parts.push(
        loc.end_line !== undefined && loc.end_line !== loc.start_line
          ? `lines ${loc.start_line}–${loc.end_line}`
          : `line ${loc.start_line}`,
      );
    }
    let text = parts.join(", ");
    if (loc.symbol) text += ` (${loc.symbol})`;
    return text;
  }
  if (loc.pages && loc.pages.length > 0) {
    const min = Math.min(...loc.pages);
    const max = Math.max(...loc.pages);
    return min === max ? `Page ${min}` : `Pages ${min}–${max}`;
  }
  if (loc.start_s !== undefined) {
    let text = formatClock(loc.start_s);
    if (loc.end_s !== undefined && loc.end_s > loc.start_s) text += `–${formatClock(loc.end_s)}`;
    if (loc.speaker) text += ` · ${loc.speaker}`;
    return text;
  }
  if (loc.heading) return `Section: ${loc.heading}`;
  return "";
}
