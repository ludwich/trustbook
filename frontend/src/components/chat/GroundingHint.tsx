import { IconInfoCircle, IconX } from "@tabler/icons-react";
import { useDismissHint, useSettings } from "@/api/queries";
import { Button } from "@/components/ui/button";

export const CHAT_GROUNDING_HINT = "chat_grounding";

/** One-time hint above the chat box. Hidden for good once dismissed. */
export function GroundingHint() {
  const settings = useSettings();
  const dismiss = useDismissHint();
  if (!settings.data) return null;
  if ((settings.data.hints_dismissed ?? []).includes(CHAT_GROUNDING_HINT)) return null;
  return (
    <div
      role="note"
      data-testid="grounding-hint"
      className="flex items-start gap-2 rounded-lg bg-info-soft px-3 py-2 text-sm"
    >
      <IconInfoCircle className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
      <p className="flex-1">
        Answers only come from your ticked sources. Ideas and brainstorming live in the Ideation
        tab.
      </p>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label="Dismiss hint"
        onClick={() => dismiss.mutate(CHAT_GROUNDING_HINT)}
      >
        <IconX aria-hidden />
      </Button>
    </div>
  );
}
