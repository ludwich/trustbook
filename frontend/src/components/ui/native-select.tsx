import { IconChevronDown } from "@tabler/icons-react";
import * as React from "react";
import { cn } from "@/lib/utils";

/** Styled native <select>: accessible, keyboard friendly and simple to test. */
function NativeSelect({ className, children, ...props }: React.ComponentProps<"select">) {
  return (
    <div className="relative">
      <select
        data-slot="native-select"
        className={cn(
          "h-9 w-full appearance-none rounded-md border border-input bg-card py-1 pr-8 pl-3 text-sm shadow-xs outline-none disabled:cursor-not-allowed disabled:opacity-50",
          "focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/40",
          "aria-invalid:border-destructive",
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <IconChevronDown
        aria-hidden
        className="pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2 text-muted-foreground"
      />
    </div>
  );
}

export { NativeSelect };
