import { IconFile, IconUpload, IconX } from "@tabler/icons-react";
import { useId, useRef, useState, type DragEvent, type FormEvent } from "react";
import { useUploadFiles } from "@/api/queries";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formatBytes, plural } from "@/lib/format";
import { cn } from "@/lib/utils";
import { type AddDialogProps, FormError } from "./dialogParts";
import { useEstimateConfirm } from "./useEstimateConfirm";

export function UploadDialog({
  spaceId,
  open,
  onOpenChange,
  initialFiles,
}: AddDialogProps & { initialFiles: File[] }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Upload files</DialogTitle>
          <DialogDescription>
            PDF, Word, PowerPoint, Excel, text, Markdown and audio. Trustbook keeps a copy in this
            space.
          </DialogDescription>
        </DialogHeader>
        <UploadForm
          spaceId={spaceId}
          initialFiles={initialFiles}
          onDone={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  );
}

/** Same name, size and date: treat as the same file when adding more. */
function fileKey(f: File): string {
  return `${f.name}:${f.size}:${f.lastModified}`;
}

function UploadForm({
  spaceId,
  initialFiles,
  onDone,
}: {
  spaceId: string;
  initialFiles: File[];
  onDone: () => void;
}) {
  const upload = useUploadFiles(spaceId);
  const estimate = useEstimateConfirm(spaceId);
  const [files, setFiles] = useState<File[]>(initialFiles);
  const [over, setOver] = useState(false);
  const [missing, setMissing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();

  const addFiles = (more: File[]) => {
    setMissing(false);
    setFiles((prev) => {
      const seen = new Set(prev.map(fileKey));
      return [...prev, ...more.filter((f) => !seen.has(fileKey(f)))];
    });
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setOver(false);
    addFiles(Array.from(e.dataTransfer.files));
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (files.length === 0) {
      setMissing(true);
      return;
    }
    const chosen = files;
    void estimate.check(
      [{ kind: "upload", files: chosen.map((f) => ({ name: f.name, bytes: f.size })) }],
      () => upload.mutate(chosen, { onSuccess: onDone }),
    );
  };

  return (
    <form onSubmit={submit} className="grid gap-4" noValidate>
      <label
        htmlFor={inputId}
        onDragOver={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-lg border-2 border-dashed px-4 py-8 text-center transition-colors hover:bg-accent/40 has-[:focus-visible]:ring-[3px] has-[:focus-visible]:ring-ring/50",
          over && "border-primary bg-accent/60",
        )}
      >
        <IconUpload className="size-6 text-muted-foreground" aria-hidden />
        <span className="font-medium">Drop files here or choose files</span>
        <span className="text-xs text-muted-foreground">You can pick several at once.</span>
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          multiple
          className="sr-only"
          aria-label="Choose files"
          onChange={(e) => {
            addFiles(Array.from(e.target.files ?? []));
            // Allow choosing the same file again after removing it.
            e.target.value = "";
          }}
        />
      </label>

      {files.length > 0 && (
        <ul aria-label="Files to upload" className="grid max-h-56 gap-1 overflow-y-auto">
          {files.map((f) => (
            <li
              key={fileKey(f)}
              className="flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm"
            >
              <IconFile className="size-4 shrink-0 text-muted-foreground" aria-hidden />
              <span className="min-w-0 flex-1 truncate" title={f.name}>
                {f.name}
              </span>
              <span className="shrink-0 text-xs text-muted-foreground">{formatBytes(f.size)}</span>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label={`Remove ${f.name}`}
                onClick={() => setFiles((prev) => prev.filter((x) => x !== f))}
              >
                <IconX aria-hidden />
              </Button>
            </li>
          ))}
        </ul>
      )}

      {missing && <FormError error="Choose at least one file." />}
      <FormError error={upload.error} />
      <DialogFooter>
        <Button type="submit" disabled={upload.isPending || estimate.checking}>
          {upload.isPending
            ? "Uploading…"
            : estimate.checking
              ? "Estimating…"
              : files.length > 0
                ? `Add ${plural(files.length, "file")}`
                : "Add files"}
        </Button>
      </DialogFooter>
      {estimate.dialog}
    </form>
  );
}
