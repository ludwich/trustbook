import * as React from "react";
import { cn } from "@/lib/utils";

/** Determinate progress bar (value 0–1). Pass value=null for an indeterminate bar. */
function Progress({
  value,
  className,
  label,
  ...props
}: Omit<React.ComponentProps<"div">, "children"> & { value: number | null; label: string }) {
  const pct = value === null ? null : Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct ?? undefined}
      data-slot="progress"
      className={cn("relative h-1.5 w-full overflow-hidden rounded-full bg-muted", className)}
      {...props}
    >
      {pct === null ? (
        <div className="absolute inset-y-0 w-1/3 animate-pulse rounded-full bg-primary/60" />
      ) : (
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-500"
          style={{ width: `${pct}%` }}
        />
      )}
    </div>
  );
}

export { Progress };
