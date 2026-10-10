import { IconCircleCheck, IconCircleX } from "@tabler/icons-react";
import type { TestResult } from "@/api/types";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The chat test already says "replied in 4.8 s"; don't repeat the time in milliseconds. */
const MENTIONS_TIME = /\d\s?(ms|s)\b/;

export function TestResultView({
  result,
  previous = false,
  className,
}: {
  result: TestResult;
  /** True when showing the stored last_test rather than a fresh result. */
  previous?: boolean;
  className?: string;
}) {
  const Icon = result.ok ? IconCircleCheck : IconCircleX;
  return (
    <div
      role="status"
      data-ok={result.ok}
      className={cn(
        "flex min-w-0 items-start gap-1.5 text-sm",
        result.ok ? "text-success" : "text-destructive",
        className,
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span className="min-w-0 break-words">
        <span className="font-medium">
          {previous ? "Last test: " : ""}
          {result.ok ? "Works" : "Failed"}
        </span>
        <span className="text-foreground/80">
          {" — "}
          {result.message}
          {result.latency_ms !== null &&
            !MENTIONS_TIME.test(result.message) &&
            ` (${result.latency_ms.toLocaleString("en-US")} ms)`}
        </span>
        {previous && (
          <span className="text-muted-foreground"> · {formatRelative(result.tested_at)}</span>
        )}
      </span>
    </div>
  );
}
