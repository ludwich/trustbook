import { useState, type FormEvent } from "react";
import { useAddPaste } from "@/api/queries";
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
import { Textarea } from "@/components/ui/textarea";
import { type AddDialogProps, FormError } from "./dialogParts";

export function PasteDialog({ spaceId, open, onOpenChange }: AddDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Paste text</DialogTitle>
          <DialogDescription>
            Notes, emails or anything else you want to ask about.
          </DialogDescription>
        </DialogHeader>
        <PasteForm spaceId={spaceId} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

function PasteForm({ spaceId, onDone }: { spaceId: string; onDone: () => void }) {
  const add = useAddPaste(spaceId);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [invalid, setInvalid] = useState(false);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) {
      setInvalid(true);
      return;
    }
    const t = title.trim();
    add.mutate(t ? { title: t, text } : { text }, { onSuccess: onDone });
  };

  return (
    <form onSubmit={submit} className="grid gap-4" noValidate>
      <div className="grid gap-1.5">
        <Label htmlFor="paste-title">
          Title <span className="font-normal text-muted-foreground">(optional)</span>
        </Label>
        <Input
          id="paste-title"
          value={title}
          maxLength={200}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Meeting notes, 3 October"
        />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="paste-text">Text</Label>
        <Textarea
          id="paste-text"
          value={text}
          rows={10}
          aria-invalid={invalid || undefined}
          aria-describedby={invalid ? "paste-text-error" : undefined}
          onChange={(e) => {
            setText(e.target.value);
            setInvalid(false);
          }}
          className="max-h-[50vh]"
        />
        {invalid && (
          <p id="paste-text-error" className="text-sm text-destructive">
            Paste some text first.
          </p>
        )}
      </div>
      <FormError error={add.error} />
      <DialogFooter>
        <Button type="submit" disabled={add.isPending}>
          {add.isPending ? "Adding…" : "Add text"}
        </Button>
      </DialogFooter>
    </form>
  );
}
