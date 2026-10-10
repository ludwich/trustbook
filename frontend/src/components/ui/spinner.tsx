import { IconLoader2 } from "@tabler/icons-react";
import { cn } from "@/lib/utils";

function Spinner({ className, label = "Loading" }: { className?: string; label?: string }) {
  return (
    <IconLoader2
      role="status"
      aria-label={label}
      className={cn("size-4 animate-spin", className)}
    />
  );
}

export { Spinner };
