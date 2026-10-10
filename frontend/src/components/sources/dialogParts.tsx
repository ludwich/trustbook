import { IconAlertCircle } from "@tabler/icons-react";
import { errorMessage } from "@/lib/utils";

export interface AddDialogProps {
  spaceId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Inline error under a form. Renders nothing without an error. */
export function FormError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p role="alert" className="flex items-start gap-1.5 text-sm text-destructive">
      <IconAlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
      {typeof error === "string" ? error : errorMessage(error)}
    </p>
  );
}
