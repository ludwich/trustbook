import { IconRoute } from "@tabler/icons-react";
import { useCorrectGateDecision, useGateLog } from "@/api/queries";
import type { GateDecision, GateLogEntry } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { QueryError } from "@/components/common/QueryError";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { NativeSelect } from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import { formatTime } from "@/lib/format";
import { errorMessage } from "@/lib/utils";

const DECISION_LABELS: Record<GateDecision, string> = {
  sources: "Answer from sources",
  brainstorm: "Brainstorming",
  chitchat: "Small talk",
};

const DECISIONS = Object.keys(DECISION_LABELS) as GateDecision[];

function formatConfidence(c: number | null): string {
  return c === null ? "Not checked" : `${Math.round(c * 100)}%`;
}

export function GateLogDialog({
  spaceId,
  open,
  onOpenChange,
}: {
  spaceId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>Gate log</DialogTitle>
          <DialogDescription>
            Before answering, Trustbook decides what kind of question it is. If a decision is wrong,
            pick the right one.
          </DialogDescription>
        </DialogHeader>
        {open && <GateLog spaceId={spaceId} />}
      </DialogContent>
    </Dialog>
  );
}

function GateLog({ spaceId }: { spaceId: string }) {
  const log = useGateLog(spaceId);
  const correct = useCorrectGateDecision(spaceId);

  if (log.isPending) return <Skeleton className="h-40" />;
  if (log.isError) {
    return (
      <QueryError
        title="Could not load the gate log"
        error={log.error}
        onRetry={() => void log.refetch()}
      />
    );
  }
  if (log.data.length === 0) {
    return (
      <EmptyState icon={<IconRoute />} title="No decisions yet" className="border-none py-6">
        Each question you ask in this space shows up here.
      </EmptyState>
    );
  }

  return (
    <div className="grid gap-2">
      {correct.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not save the correction: {errorMessage(correct.error)}
        </p>
      )}
      <div className="max-h-[60svh] overflow-auto rounded-md border">
        <table className="w-full text-sm" aria-label="Gate decisions">
          <thead className="sticky top-0 bg-popover text-left text-xs text-muted-foreground">
            <tr className="border-b">
              <th className="px-3 py-2 font-medium">Time</th>
              <th className="px-3 py-2 font-medium">Question</th>
              <th className="px-3 py-2 font-medium">Decision</th>
              <th className="px-3 py-2 text-right font-medium">Confidence</th>
              <th className="px-3 py-2 font-medium">Correction</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {log.data.map((e) => (
              <Row
                key={e.id}
                entry={e}
                disabled={correct.isPending}
                onCorrect={(decision) => correct.mutate({ id: e.id, decision })}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Row({
  entry,
  disabled,
  onCorrect,
}: {
  entry: GateLogEntry;
  disabled: boolean;
  onCorrect: (d: GateDecision) => void;
}) {
  return (
    <tr data-testid={`gate-entry-${entry.id}`} className="align-top">
      <td className="px-3 py-2 whitespace-nowrap text-muted-foreground tabular-nums">
        {entry.created_at ? formatTime(entry.created_at) : ""}
      </td>
      <td className="max-w-72 px-3 py-2 break-words">{entry.question}</td>
      <td className="px-3 py-2 whitespace-nowrap">{DECISION_LABELS[entry.decision]}</td>
      <td className="px-3 py-2 text-right whitespace-nowrap text-muted-foreground tabular-nums">
        {formatConfidence(entry.confidence)}
      </td>
      <td className="w-48 px-3 py-1.5">
        <NativeSelect
          aria-label={`Correct the decision for “${entry.question}”`}
          value={entry.correction ?? ""}
          disabled={disabled}
          onChange={(ev) => {
            const v = ev.target.value as GateDecision;
            if (v) onCorrect(v);
          }}
          className="h-8 text-xs"
        >
          <option value="" disabled>
            Not corrected
          </option>
          {DECISIONS.map((d) => (
            <option key={d} value={d}>
              {DECISION_LABELS[d]}
            </option>
          ))}
        </NativeSelect>
      </td>
    </tr>
  );
}
