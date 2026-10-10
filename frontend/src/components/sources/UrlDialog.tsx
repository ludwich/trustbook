import { useState, type FormEvent } from "react";
import { useAddUrl } from "@/api/queries";
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
import { normaliseUrl } from "@/lib/url";
import { type AddDialogProps, FormError } from "./dialogParts";

export function UrlDialog({ spaceId, open, onOpenChange }: AddDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a web page</DialogTitle>
          <DialogDescription>
            Trustbook saves a dated copy of the page. YouTube links work too: it reads the captions,
            or transcribes the video if there are none.
          </DialogDescription>
        </DialogHeader>
        <UrlForm spaceId={spaceId} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

function UrlForm({ spaceId, onDone }: { spaceId: string; onDone: () => void }) {
  const add = useAddUrl(spaceId);
  const [value, setValue] = useState("");
  const [invalid, setInvalid] = useState(false);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const url = normaliseUrl(value);
    if (!url) {
      setInvalid(true);
      return;
    }
    add.mutate(url, { onSuccess: onDone });
  };

  return (
    <form onSubmit={submit} className="grid gap-4" noValidate>
      <div className="grid gap-1.5">
        <Label htmlFor="source-url">Web address</Label>
        <Input
          id="source-url"
          type="url"
          inputMode="url"
          autoComplete="off"
          value={value}
          placeholder="https://example.com/page or a YouTube link"
          aria-invalid={invalid || undefined}
          aria-describedby={invalid ? "source-url-error" : undefined}
          onChange={(e) => {
            setValue(e.target.value);
            setInvalid(false);
          }}
        />
        {invalid && (
          <p id="source-url-error" className="text-sm text-destructive">
            Enter a web address, like https://example.com/page.
          </p>
        )}
      </div>
      <FormError error={add.error} />
      <DialogFooter>
        <Button type="submit" disabled={add.isPending}>
          {add.isPending ? "Adding…" : "Add page"}
        </Button>
      </DialogFooter>
    </form>
  );
}
