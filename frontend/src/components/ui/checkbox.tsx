import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Native checkbox with a tri-state option: pass checked="mixed" for "some ticked".
 * Native inputs keep keyboard, form and screen-reader behaviour for free.
 */
function Checkbox({
  checked,
  className,
  ...props
}: Omit<React.ComponentProps<"input">, "type" | "checked"> & { checked: boolean | "mixed" }) {
  const ref = React.useRef<HTMLInputElement>(null);
  React.useEffect(() => {
    if (ref.current) ref.current.indeterminate = checked === "mixed";
  }, [checked]);
  return (
    <input
      ref={ref}
      type="checkbox"
      data-slot="checkbox"
      checked={checked === true}
      className={cn(
        "size-4 shrink-0 cursor-pointer rounded-sm accent-primary outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export { Checkbox };
