import { IconFileText, IconUpload } from "@tabler/icons-react";
import { useState, type DragEvent } from "react";
import { useSetSourcesEnabled, useSources } from "@/api/queries";
import { EmptyState } from "@/components/common/EmptyState";
import { QueryError } from "@/components/common/QueryError";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { cn, errorMessage } from "@/lib/utils";
import { AddSourceDialogs, AddSourceMenu, type AddSourceKind } from "./AddSource";
import { GROUP_LABELS, groupChecked, groupSources } from "./sourceMeta";
import { SourceRow } from "./SourceRow";

function hasFiles(e: DragEvent): boolean {
  return Array.from(e.dataTransfer?.types ?? []).includes("Files");
}

export function SourcesPanel({ spaceId }: { spaceId: string }) {
  const sources = useSources(spaceId);
  const toggle = useSetSourcesEnabled(spaceId);
  const [dialog, setDialog] = useState<AddSourceKind | null>(null);
  const [droppedFiles, setDroppedFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);

  const openDialog = (kind: AddSourceKind | null) => {
    if (kind !== "upload") setDroppedFiles([]);
    setDialog(kind);
  };

  // Files dropped anywhere on the panel open the upload dialog with them.
  const dropProps = {
    onDragOver: (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      setDragging(true);
    },
    onDragLeave: (e: DragEvent) => {
      if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
      setDragging(false);
    },
    onDrop: (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      setDragging(false);
      const files = Array.from(e.dataTransfer.files);
      if (files.length === 0) return;
      setDroppedFiles(files);
      setDialog("upload");
    },
  };

  return (
    <div {...dropProps} className={cn("relative flex min-h-0 flex-1 flex-col")}>
      <div className="flex items-center justify-between gap-2 px-4 pt-3 pb-2">
        <h2 className="text-sm font-semibold">
          Sources
          {sources.data && sources.data.length > 0 && (
            <span className="ml-1.5 font-normal text-muted-foreground">{sources.data.length}</span>
          )}
        </h2>
        <AddSourceMenu onChoose={openDialog} />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        {sources.isPending ? (
          <div className="grid gap-2 px-2 pt-1">
            <Skeleton className="h-5" />
            <Skeleton className="h-5" />
            <Skeleton className="h-5 w-2/3" />
          </div>
        ) : sources.isError ? (
          <div className="px-2">
            <QueryError
              title="Could not load sources"
              error={sources.error}
              onRetry={() => void sources.refetch()}
            />
          </div>
        ) : sources.data.length === 0 ? (
          <div className="px-2">
            <EmptyState icon={<IconFileText />} title="No sources yet" className="py-8">
              Add documents, web pages, videos or a folder. You can also drop files here.
            </EmptyState>
          </div>
        ) : (
          <>
            {toggle.error && (
              <p role="alert" className="px-2 pb-2 text-xs text-destructive">
                {errorMessage(toggle.error)}
              </p>
            )}
            {groupSources(sources.data).map(({ group, sources: items }) => {
              const state = groupChecked(items);
              const label = GROUP_LABELS[group];
              return (
                <section
                  key={group}
                  aria-labelledby={`group-${group}`}
                  className="mt-2 first:mt-0"
                  data-testid={`group-${group}`}
                >
                  <div className="flex items-center gap-2 px-2 py-1">
                    <Checkbox
                      checked={state}
                      aria-label={`Use all ${label.toLowerCase()}`}
                      onChange={() =>
                        toggle.mutate({ ids: items.map((s) => s.id), enabled: state !== true })
                      }
                    />
                    <h3
                      id={`group-${group}`}
                      className="text-xs font-semibold tracking-wide text-muted-foreground uppercase"
                    >
                      {label}
                    </h3>
                    <span className="text-xs text-muted-foreground">{items.length}</span>
                  </div>
                  <ul className="grid gap-0.5">
                    {items.map((s) => (
                      <SourceRow
                        key={s.id}
                        spaceId={spaceId}
                        source={s}
                        onToggle={(enabled) => toggle.mutate({ ids: [s.id], enabled })}
                      />
                    ))}
                  </ul>
                </section>
              );
            })}
          </>
        )}
      </div>

      {dragging && (
        <div className="pointer-events-none absolute inset-2 flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-primary bg-background/90 text-sm font-medium">
          <IconUpload className="size-6 text-primary" aria-hidden />
          Drop files to add them
        </div>
      )}

      <AddSourceDialogs
        spaceId={spaceId}
        open={dialog}
        onOpenChange={openDialog}
        initialFiles={droppedFiles}
      />
    </div>
  );
}
