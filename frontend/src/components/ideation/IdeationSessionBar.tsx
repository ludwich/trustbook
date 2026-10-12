import { IconPlus, IconTrash } from "@tabler/icons-react";
import { useState } from "react";
import { useDeleteIdeationSession } from "@/api/queries";
import type { IdeationSessionSummary } from "@/api/types";
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
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { errorMessage } from "@/lib/utils";
import { sessionLabel } from "./sessionText";

/** Idea session switcher: resume a saved session, start a new idea, delete one. */
export function IdeationSessionBar({
  spaceId,
  sessions,
  current,
  onSelect,
  onNew,
}: {
  spaceId: string;
  sessions: IdeationSessionSummary[];
  /** null = the "I have an idea" box. */
  current: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  const del = useDeleteIdeationSession(spaceId);
  const [confirm, setConfirm] = useState(false);
  const currentSession = sessions.find((s) => s.id === current);
  const currentTitle = currentSession ? sessionLabel(currentSession) : "this idea";

  return (
    <div className="flex flex-wrap items-center gap-2">
      {sessions.length > 0 ? (
        <div className="w-72 max-w-full">
          <NativeSelect
            aria-label="Idea session"
            value={current ?? ""}
            onChange={(e) => e.target.value && onSelect(e.target.value)}
            className="h-8"
          >
            {current === null && <option value="">New idea</option>}
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>
                {sessionLabel(s)}
                {s.status === "finished" ? " (finished)" : ""}
              </option>
            ))}
          </NativeSelect>
        </div>
      ) : (
        <span className="text-sm text-muted-foreground">New idea</span>
      )}
      {current !== null && (
        <Button size="xs" variant="ghost" onClick={onNew}>
          <IconPlus aria-hidden />
          New idea
        </Button>
      )}
      {current !== null && (
        <Button
          size="icon-xs"
          variant="ghost"
          aria-label="Delete idea session"
          title="Delete idea session"
          onClick={() => setConfirm(true)}
        >
          <IconTrash aria-hidden />
        </Button>
      )}
      {del.error && (
        <span role="alert" className="text-xs text-destructive">
          {errorMessage(del.error)}
        </span>
      )}

      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{currentTitle}”?</AlertDialogTitle>
            <AlertDialogDescription>
              The questions, answers and the brief of this idea are deleted. Your sources stay.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                if (current) del.mutate(current, { onSuccess: onNew });
              }}
            >
              Delete idea
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
