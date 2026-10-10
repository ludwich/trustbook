/**
 * scrollTop that puts a target band (top, height) in view inside a viewport: centred when it fits,
 * else its top just below a small margin. Never negative. Pure, so the viewer can compute it
 * before paint (no flash of the top of the file) and tests can check it without layout.
 */
export function centeredScrollTop(
  targetTop: number,
  targetHeight: number,
  viewportHeight: number,
  margin = 16,
): number {
  if (viewportHeight <= 0) return Math.max(0, targetTop - margin);
  const room = viewportHeight - targetHeight;
  const top = room >= 2 * margin ? targetTop - room / 2 : targetTop - margin;
  return Math.max(0, Math.round(top));
}

/** Line rows in the code viewer have a fixed height so positions follow from line numbers. */
export const CODE_LINE_HEIGHT_PX = 20;
/** Padding above the first code line (matches `pt-3`). */
export const CODE_PADDING_TOP_PX = 12;

/** Index of the first highlighted line in `lines` and how many lines are highlighted (clamped). */
export function highlightWindow(
  firstLine: number,
  lineCount: number,
  highlight: [number, number],
): { start: number; count: number } {
  if (lineCount <= 0) return { start: 0, count: 0 };
  const last = firstLine + lineCount - 1;
  const from = Math.min(Math.max(highlight[0], firstLine), last);
  const to = Math.min(Math.max(highlight[1], from), last);
  return { start: from - firstLine, count: to - from + 1 };
}

/** scrollTop for the code viewer, from line numbers alone (rows are fixed-height). */
export function codeScrollTop(
  firstLine: number,
  lineCount: number,
  highlight: [number, number],
  viewportHeight: number,
): number {
  const { start, count } = highlightWindow(firstLine, lineCount, highlight);
  return centeredScrollTop(
    CODE_PADDING_TOP_PX + start * CODE_LINE_HEIGHT_PX,
    count * CODE_LINE_HEIGHT_PX,
    viewportHeight,
  );
}
