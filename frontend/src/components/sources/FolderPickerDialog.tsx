import { IconArrowUp, IconChevronRight, IconFile, IconFolder, IconHome } from "@tabler/icons-react";
import { useState } from "react";
import { useAddFolder, useBrowse, useFolderEstimate } from "@/api/queries";
import { QueryError } from "@/components/common/QueryError";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { formatBytes } from "@/lib/format";
import { useDebouncedValue } from "@/lib/useDebouncedValue";
import { type AddDialogProps, FormError } from "./dialogParts";
import { useEstimateConfirm } from "./useEstimateConfirm";

/** Wait this long after the last folder change before asking for an estimate. */
const FOLDER_ESTIMATE_DEBOUNCE_MS = 400;

/** "a/b/c" → [{name:"a",path:"a"},{name:"b",path:"a/b"},{name:"c",path:"a/b/c"}] */
function crumbs(path: string): { name: string; path: string }[] {
  const parts = path.split("/").filter(Boolean);
  return parts.map((name, i) => ({ name, path: parts.slice(0, i + 1).join("/") }));
}

export function FolderPickerDialog({ spaceId, open, onOpenChange }: AddDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Link a folder</DialogTitle>
          <DialogDescription>
            Trustbook reads the folder but never changes it, and only stores where it is. Files that
            look like they hold passwords or keys are skipped.
          </DialogDescription>
        </DialogHeader>
        <FolderPicker spaceId={spaceId} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

function FolderPicker({ spaceId, onDone }: { spaceId: string; onDone: () => void }) {
  const [path, setPath] = useState("");
  const listing = useBrowse(path);
  const add = useAddFolder(spaceId);
  const current = listing.data?.path ?? path;
  const entries = [...(listing.data?.entries ?? [])].sort(
    (a, b) => Number(b.is_dir) - Number(a.is_dir) || a.name.localeCompare(b.name),
  );
  const atRoot = current === "";
  const estimate = useEstimateConfirm(spaceId);
  // Estimate the folder the user has settled on (not every folder they pass through).
  const settled = useDebouncedValue(current, FOLDER_ESTIMATE_DEBOUNCE_MS);
  const folderEstimate = useFolderEstimate(
    spaceId,
    settled,
    !atRoot && settled === current && listing.isSuccess,
  );
  const known = settled === current ? folderEstimate.data : undefined;

  const link = () => {
    const path = current;
    void estimate.check(
      [{ kind: "folder", path }],
      () => add.mutate(path, { onSuccess: onDone }),
      known,
    );
  };

  return (
    <div className="grid gap-3">
      <nav aria-label="Folder path" className="flex flex-wrap items-center gap-0.5 text-sm">
        <Button
          variant="ghost"
          size="xs"
          onClick={() => setPath("")}
          aria-current={atRoot ? "location" : undefined}
        >
          <IconHome aria-hidden />
          Home
        </Button>
        {crumbs(current).map((c, i, all) => (
          <span key={c.path} className="flex items-center gap-0.5">
            <IconChevronRight className="size-3.5 text-muted-foreground" aria-hidden />
            <Button
              variant="ghost"
              size="xs"
              onClick={() => setPath(c.path)}
              aria-current={i === all.length - 1 ? "location" : undefined}
            >
              {c.name}
            </Button>
          </span>
        ))}
      </nav>

      <div className="h-72 overflow-y-auto rounded-md border">
        {listing.isPending ? (
          <div className="grid gap-2 p-3">
            <Skeleton className="h-5" />
            <Skeleton className="h-5" />
            <Skeleton className="h-5 w-1/2" />
          </div>
        ) : listing.isError ? (
          <div className="p-3">
            <QueryError
              title="Could not open this folder"
              error={listing.error}
              onRetry={() => void listing.refetch()}
            />
          </div>
        ) : (
          <ul aria-label="Folder contents" className="divide-y">
            {listing.data.parent !== null && (
              <li>
                <button
                  type="button"
                  onClick={() => setPath(listing.data.parent ?? "")}
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
                >
                  <IconArrowUp className="size-4 text-muted-foreground" aria-hidden />
                  Up one level
                </button>
              </li>
            )}
            {entries.length === 0 && (
              <li className="px-3 py-6 text-center text-sm text-muted-foreground">
                This folder is empty.
              </li>
            )}
            {entries.map((e) =>
              e.is_dir ? (
                <li key={e.path}>
                  <button
                    type="button"
                    onClick={() => setPath(e.path)}
                    className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
                  >
                    <IconFolder className="size-4 shrink-0 text-primary" aria-hidden />
                    <span className="truncate">{e.name}</span>
                  </button>
                </li>
              ) : (
                <li
                  key={e.path}
                  className="flex items-center gap-2 px-3 py-1.5 text-sm text-muted-foreground"
                >
                  <IconFile className="size-4 shrink-0" aria-hidden />
                  <span className="min-w-0 flex-1 truncate">{e.name}</span>
                  {e.size !== null && <span className="text-xs">{formatBytes(e.size)}</span>}
                </li>
              ),
            )}
          </ul>
        )}
      </div>

      <FormError error={add.error} />
      <DialogFooter className="items-center sm:justify-between">
        <div className="grid min-w-0 gap-0.5 text-xs text-muted-foreground">
          <p className="truncate">
            {atRoot ? "Open the folder you want to link." : `Selected: ${current}`}
          </p>
          {!atRoot && (
            <p className="truncate" aria-live="polite" data-testid="folder-estimate">
              {known ? known.summary : folderEstimate.isError ? "" : "Estimating…"}
            </p>
          )}
        </div>
        <Button
          disabled={
            atRoot || listing.isPending || listing.isError || add.isPending || estimate.checking
          }
          onClick={link}
        >
          {add.isPending ? "Linking…" : "Link this folder"}
        </Button>
      </DialogFooter>
      {estimate.dialog}
    </div>
  );
}
