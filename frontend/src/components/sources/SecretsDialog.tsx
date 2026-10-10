import { ApiError } from "@/api/client";
import { useSecretsReport } from "@/api/queries";
import type { Source } from "@/api/types";
import { QueryError } from "@/components/common/QueryError";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { plural } from "@/lib/format";

/**
 * The gitleaks report of a linked folder (SPEC §7.4): file path, line and rule ID only.
 * The API never returns secret values, and this view has no place to show one.
 */
export function SecretsDialog({
  spaceId,
  source,
  open,
  onOpenChange,
}: {
  spaceId: string;
  source: Source;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const report = useSecretsReport(spaceId, source.id, open);
  const notFound = report.error instanceof ApiError && report.error.status === 404;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl" data-testid="secrets-dialog">
        <DialogHeader>
          <DialogTitle>Files skipped: possible secrets</DialogTitle>
          <DialogDescription>
            These files were not indexed. Secret values are never stored or shown.
          </DialogDescription>
        </DialogHeader>

        {report.isPending ? (
          <div className="grid gap-2" aria-busy="true">
            <Skeleton className="h-5" />
            <Skeleton className="h-5" />
            <Skeleton className="h-5 w-2/3" />
          </div>
        ) : notFound ? (
          <p className="text-sm text-muted-foreground">
            No report is available for this source. Refresh it to scan the folder again.
          </p>
        ) : report.isError ? (
          <QueryError
            title="Could not load the report"
            error={report.error}
            onRetry={() => void report.refetch()}
          />
        ) : (
          <div className="grid gap-2">
            <p className="text-xs text-muted-foreground">
              {plural(report.data.files_skipped, "file")} in “{source.title}” ·{" "}
              {plural(report.data.findings.length, "finding")}
            </p>
            {report.data.findings.length > 0 && (
              <div className="max-h-[50vh] overflow-y-auto rounded-md border">
                <Table aria-label="Possible secrets">
                  <TableHeader className="sticky top-0 bg-popover">
                    <TableRow>
                      <TableHead>File</TableHead>
                      <TableHead className="w-16 text-right">Line</TableHead>
                      <TableHead>Rule</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {report.data.findings.map((f, i) => (
                      <TableRow key={`${f.file}:${f.line}:${f.rule_id}:${i}`}>
                        <TableCell className="font-mono text-xs break-all">{f.file}</TableCell>
                        <TableCell className="text-right font-mono text-xs tabular-nums">
                          {f.line}
                        </TableCell>
                        <TableCell className="font-mono text-xs">{f.rule_id}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
