import { IconPlus, IconTrash } from "@tabler/icons-react";
import { useState } from "react";
import { useConversations, useDeleteConversation } from "@/api/queries";
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

/** Compact conversation switcher: select, new, delete. */
export function ConversationBar({
  spaceId,
  current,
  onSelect,
  onNew,
}: {
  spaceId: string;
  /** null = a new conversation that has no messages yet. */
  current: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  const conversations = useConversations(spaceId);
  const del = useDeleteConversation(spaceId);
  const [confirm, setConfirm] = useState(false);
  const list = conversations.data ?? [];
  const currentTitle = list.find((c) => c.id === current)?.title ?? "this conversation";

  return (
    <div className="flex items-center gap-2">
      {list.length > 0 ? (
        <div className="w-72 max-w-full">
          <NativeSelect
            aria-label="Conversation"
            value={current ?? ""}
            onChange={(e) => e.target.value && onSelect(e.target.value)}
            className="h-8"
          >
            {current === null && <option value="">New conversation</option>}
            {list.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title || "Untitled conversation"}
              </option>
            ))}
          </NativeSelect>
        </div>
      ) : (
        <span className="text-sm text-muted-foreground">New conversation</span>
      )}
      {current !== null && (
        <Button size="xs" variant="ghost" onClick={onNew}>
          <IconPlus aria-hidden />
          New conversation
        </Button>
      )}
      {current !== null && (
        <Button
          size="icon-xs"
          variant="ghost"
          aria-label="Delete conversation"
          title="Delete conversation"
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
              The questions and answers in it are deleted. Your sources stay.
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
              Delete conversation
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
