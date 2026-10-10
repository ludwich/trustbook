import { IconSearch } from "@tabler/icons-react";
import { useState, type FormEvent } from "react";
import { useAddFromSearch, useWebSearch } from "@/api/queries";
import type { WebSearchResult } from "@/api/types";
import { CapabilityGate } from "@/components/capabilities/CapabilityGate";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { plural } from "@/lib/format";
import { type AddDialogProps, FormError } from "./dialogParts";
import { useEstimateConfirm } from "./useEstimateConfirm";

export function SearchDialog({ spaceId, open, onOpenChange }: AddDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Search the web</DialogTitle>
          <DialogDescription>
            Find pages, tick the ones you want, and add them as sources. Chat answers never search
            the web.
          </DialogDescription>
        </DialogHeader>
        <CapabilityGate capability="web_search" feature="Searching the web">
          <WebSearch spaceId={spaceId} onDone={() => onOpenChange(false)} />
        </CapabilityGate>
      </DialogContent>
    </Dialog>
  );
}

function displayUrl(url: string): string {
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, "") + (u.pathname === "/" ? "" : u.pathname);
  } catch {
    return url;
  }
}

function WebSearch({ spaceId, onDone }: { spaceId: string; onDone: () => void }) {
  const search = useWebSearch(spaceId);
  const add = useAddFromSearch(spaceId);
  const estimate = useEstimateConfirm(spaceId);
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const results: WebSearchResult[] = search.data?.results ?? [];

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    setPicked(new Set());
    search.mutate({ query: q, limit: 10 });
  };

  const toggle = (url: string, on: boolean) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(url);
      else next.delete(url);
      return next;
    });

  const addPicked = () => {
    const items = results
      .filter((r) => picked.has(r.url))
      .map((r) => ({ url: r.url, title: r.title }));
    void estimate.check([{ kind: "url", count: items.length }], () =>
      add.mutate(items, { onSuccess: onDone }),
    );
  };

  return (
    <div className="grid gap-3">
      <form onSubmit={submit} className="flex gap-2" role="search">
        <Input
          aria-label="Search for"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="What are you looking for?"
        />
        <Button type="submit" variant="outline" disabled={search.isPending || !query.trim()}>
          <IconSearch aria-hidden />
          {search.isPending ? "Searching…" : "Search"}
        </Button>
      </form>

      <FormError error={search.error} />

      {search.isSuccess && results.length === 0 && (
        <p className="py-6 text-center text-sm text-muted-foreground">
          No results. Try other words.
        </p>
      )}

      {results.length > 0 && (
        <ul aria-label="Search results" className="grid max-h-80 gap-1 overflow-y-auto">
          {results.map((r) => {
            const id = `result-${encodeURIComponent(r.url)}`;
            return (
              <li key={r.url} className="flex gap-2.5 rounded-md px-2 py-2 hover:bg-accent/50">
                <Checkbox
                  id={id}
                  checked={picked.has(r.url)}
                  onChange={(e) => toggle(r.url, e.target.checked)}
                  className="mt-0.5"
                />
                <label htmlFor={id} className="grid min-w-0 cursor-pointer gap-0.5">
                  <span className="text-sm font-medium">{r.title || displayUrl(r.url)}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {displayUrl(r.url)}
                  </span>
                  {r.snippet && (
                    <span className="line-clamp-2 text-xs text-muted-foreground">{r.snippet}</span>
                  )}
                </label>
              </li>
            );
          })}
        </ul>
      )}

      <FormError error={add.error} />
      {results.length > 0 && (
        <DialogFooter>
          <Button
            disabled={picked.size === 0 || add.isPending || estimate.checking}
            onClick={addPicked}
          >
            {add.isPending
              ? "Adding…"
              : estimate.checking
                ? "Estimating…"
                : picked.size === 0
                  ? "Tick pages to add"
                  : `Add ${plural(picked.size, "source")}`}
          </Button>
        </DialogFooter>
      )}
      {estimate.dialog}
    </div>
  );
}
