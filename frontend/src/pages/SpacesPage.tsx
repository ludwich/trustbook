import { IconFolder, IconFolders, IconPlus, IconTrash } from "@tabler/icons-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { useCreateSpace, useDeleteSpace, useSpaces } from "@/api/queries";
import type { Space } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { PageHeader } from "@/components/common/PageHeader";
import { QueryError } from "@/components/common/QueryError";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { formatDate, plural } from "@/lib/format";
import { errorMessage } from "@/lib/utils";

const NAME_MAX = 80;
const DESCRIPTION_MAX = 500;

export function SpacesPage() {
  const spaces = useSpaces();
  const [createOpen, setCreateOpen] = useState(false);
  const [toDelete, setToDelete] = useState<Space | null>(null);

  const newButton = (
    <Button onClick={() => setCreateOpen(true)}>
      <IconPlus aria-hidden />
      New space
    </Button>
  );

  return (
    <div className="mx-auto w-full max-w-5xl px-5 py-6">
      <PageHeader
        title="Spaces"
        description="One space per engagement. Each space keeps its own sources in its own folder."
        actions={spaces.data && spaces.data.length > 0 ? newButton : undefined}
      />

      {spaces.isPending && (
        <div className="grid gap-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
      )}
      {spaces.isError && (
        <QueryError
          title="Could not load spaces"
          error={spaces.error}
          onRetry={() => void spaces.refetch()}
        />
      )}
      {spaces.data?.length === 0 && (
        <EmptyState icon={<IconFolders />} title="No spaces yet" action={newButton}>
          Create a space for each customer or project. Add sources to it, then ask questions that
          are answered only from those sources.
        </EmptyState>
      )}
      {spaces.data && spaces.data.length > 0 && (
        <ul className="grid gap-2" aria-label="Spaces">
          {spaces.data.map((space) => (
            <SpaceRow key={space.id} space={space} onDelete={() => setToDelete(space)} />
          ))}
        </ul>
      )}

      <CreateSpaceDialog open={createOpen} onOpenChange={setCreateOpen} />
      <DeleteSpaceDialog space={toDelete} onClose={() => setToDelete(null)} />
    </div>
  );
}

function SpaceRow({ space, onDelete }: { space: Space; onDelete: () => void }) {
  return (
    <li className="group relative flex items-center gap-4 rounded-lg border bg-card px-4 py-3 shadow-xs transition-colors hover:border-ring/50">
      <IconFolder className="size-5 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 flex-1">
        <Link
          to={`/spaces/${encodeURIComponent(space.id)}`}
          className="font-medium outline-none after:absolute after:inset-0 after:rounded-lg focus-visible:after:ring-[3px] focus-visible:after:ring-ring/50"
        >
          {space.name}
        </Link>
        {space.description && (
          <p className="truncate text-sm text-muted-foreground">{space.description}</p>
        )}
      </div>
      <div className="hidden shrink-0 text-right text-xs text-muted-foreground sm:block">
        <div>{plural(space.source_count, "source")}</div>
        <div>Created {formatDate(space.created_at)}</div>
      </div>
      <Button
        variant="ghost"
        size="icon-sm"
        className="relative z-10 text-muted-foreground hover:text-destructive"
        onClick={onDelete}
        aria-label={`Delete ${space.name}`}
        title="Delete space"
      >
        <IconTrash aria-hidden />
      </Button>
    </li>
  );
}

function CreateSpaceDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const create = useCreateSpace();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [touched, setTouched] = useState(false);

  const trimmed = name.trim();
  const nameError = !trimmed ? "Give the space a name." : null;

  const close = (next: boolean) => {
    onOpenChange(next);
    if (!next) {
      setName("");
      setDescription("");
      setTouched(false);
      create.reset();
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (nameError) return;
    const desc = description.trim();
    create.mutate(
      { name: trimmed, ...(desc ? { description: desc } : {}) },
      {
        onSuccess: (space) => {
          close(false);
          void navigate(`/spaces/${encodeURIComponent(space.id)}`);
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent>
        <form onSubmit={onSubmit} noValidate className="grid gap-4">
          <DialogHeader>
            <DialogTitle>New space</DialogTitle>
            <DialogDescription>
              Trustbook creates a folder for this space. Its sources and chats stay inside it.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-1.5">
            <Label htmlFor="space-name">Name</Label>
            <Input
              id="space-name"
              value={name}
              maxLength={NAME_MAX}
              onChange={(e) => setName(e.target.value)}
              placeholder="For example: Acme security audit"
              aria-invalid={(touched && !!nameError) || undefined}
              aria-describedby={touched && nameError ? "space-name-error" : undefined}
              autoFocus
            />
            {touched && nameError && (
              <p id="space-name-error" className="text-xs font-medium text-destructive">
                {nameError}
              </p>
            )}
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="space-description">
              Description <span className="font-normal text-muted-foreground">(optional)</span>
            </Label>
            <Textarea
              id="space-description"
              value={description}
              maxLength={DESCRIPTION_MAX}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
            />
            <p className="text-right text-xs text-muted-foreground">
              {description.length}/{DESCRIPTION_MAX}
            </p>
          </div>
          {create.isError && (
            <p role="alert" className="text-sm text-destructive">
              Could not create the space: {errorMessage(create.error)}
            </p>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => close(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending && <Spinner label="Creating" />}
              Create space
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteSpaceDialog({ space, onClose }: { space: Space | null; onClose: () => void }) {
  const del = useDeleteSpace();

  const onOpenChange = (open: boolean) => {
    if (!open && !del.isPending) {
      del.reset();
      onClose();
    }
  };

  const onConfirm = () => {
    if (!space) return;
    del.mutate(space.id, {
      onSuccess: () => {
        del.reset();
        onClose();
      },
    });
  };

  return (
    <AlertDialog open={space !== null} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete “{space?.name}”?</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="grid gap-2">
              <p>
                This removes the space folder and everything in it: sources, chats and ideas. It
                can't be undone. Files in linked folders on your computer are not touched.
              </p>
              <p className="rounded-md bg-muted px-2.5 py-1.5 font-mono text-xs break-all text-foreground">
                {space?.folder}
              </p>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        {del.isError && (
          <p role="alert" className="text-sm text-destructive">
            Could not delete the space: {errorMessage(del.error)}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={del.isPending}>Cancel</AlertDialogCancel>
          {/* Plain button (not AlertDialogAction) so the dialog stays open on errors. */}
          <Button variant="destructive" onClick={onConfirm} disabled={del.isPending}>
            {del.isPending && <Spinner label="Deleting" />}
            Delete space and folder
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
