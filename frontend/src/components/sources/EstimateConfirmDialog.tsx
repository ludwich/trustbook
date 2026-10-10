import { IconHourglass } from "@tabler/icons-react";
import type { SourceEstimate } from "@/api/types";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

/** "About 4,200 files, roughly 25 min on this PC" with Add / Cancel. */
export function EstimateConfirmDialog({
  estimate,
  onAdd,
  onCancel,
}: {
  estimate: SourceEstimate | null;
  onAdd: () => void;
  onCancel: () => void;
}) {
  return (
    <AlertDialog open={estimate !== null} onOpenChange={(open) => !open && onCancel()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            <IconHourglass className="size-4 text-muted-foreground" aria-hidden />
            This will take a while
          </AlertDialogTitle>
          <AlertDialogDescription data-testid="estimate-summary">
            {estimate?.summary}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <p className="text-sm text-muted-foreground">
          You can keep working while it runs, and pause or cancel it in the queue.
        </p>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={onAdd}>Add</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
