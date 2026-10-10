import { IconAlertTriangle, IconCpu, IconPlugConnected } from "@tabler/icons-react";
import { useState, type FormEvent } from "react";
import { useTestCapability, useUpdateCapability } from "@/api/queries";
import type { Capability, TestResult } from "@/api/types";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { errorMessage } from "@/lib/utils";
import { CapabilityFields } from "./CapabilityFields";
import { hasField, useCapabilityDraft } from "./draft";
import { LocalityBadge } from "./LocalityBadge";
import { StatusPill } from "./StatusPill";
import { TestResultView } from "./TestResultView";
import { useModelSuggestions } from "./useModelSuggestions";

function hostOf(url: string | null): string | undefined {
  if (!url) return undefined;
  try {
    return new URL(url).host;
  } catch {
    return undefined;
  }
}

/** One Settings card per capability, rendered from its manifest. */
export function CapabilityCard({ capability }: { capability: Capability }) {
  const form = useCapabilityDraft(capability);
  const save = useUpdateCapability(capability.id);
  const test = useTestCapability(capability.id);
  const models = useModelSuggestions(capability, form);
  const [result, setResult] = useState<TestResult | null>(null);
  const [showErrors, setShowErrors] = useState(false);
  const [saved, setSaved] = useState(false);

  const titleId = `capability-${capability.id}-title`;
  const hasEnabled = hasField(capability, "enabled");
  const enabled = form.draft.values.enabled !== false;
  const { bundled } = capability;

  const onSave = (e: FormEvent) => {
    e.preventDefault();
    if (form.missing.length) {
      setShowErrors(true);
      return;
    }
    setSaved(false);
    save.mutate(form.update, {
      onSuccess: (updated) => {
        form.reset(updated);
        setShowErrors(false);
        setResult(null);
        setSaved(true);
      },
    });
  };

  const onTest = () => {
    if (form.missing.length) {
      setShowErrors(true);
      return;
    }
    setResult(null);
    // Unsaved changes are tested as a draft; otherwise the saved config is tested and stored.
    test.mutate(form.dirty ? form.update : undefined, { onSuccess: setResult });
  };

  return (
    <Card
      id={`capability-${capability.id}`}
      aria-labelledby={titleId}
      className="scroll-mt-20"
      data-testid={`capability-card-${capability.id}`}
    >
      <form onSubmit={onSave} noValidate className="flex h-full flex-col">
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <CardTitle id={titleId}>{capability.label}</CardTitle>
              {capability.required && <Badge variant="secondary">Required</Badge>}
            </div>
            <div className="flex items-center gap-1.5">
              <LocalityBadge
                locality={capability.locality}
                host={hostOf(capability.config.base_url)}
              />
              <StatusPill status={capability.status} title={capability.status_detail} />
            </div>
          </div>
          <CardDescription>{capability.description}</CardDescription>
          {capability.status !== "ready" && capability.status_detail && (
            <p className="text-xs text-muted-foreground" data-testid="status-detail">
              {capability.status_detail}
            </p>
          )}
        </CardHeader>

        <CardContent className="grid flex-1 content-start gap-4">
          {hasEnabled && (
            <div className="flex items-center gap-3">
              <Switch
                id={`cap-${capability.id}-enabled`}
                checked={enabled}
                onCheckedChange={(v) => form.setValue("enabled", v)}
              />
              <label htmlFor={`cap-${capability.id}-enabled`} className="text-sm font-medium">
                {enabled ? "On" : "Off"}
              </label>
            </div>
          )}

          {capability.id === "grounding" && !enabled && (
            <Alert variant="warning">
              <IconAlertTriangle aria-hidden />
              <AlertDescription className="text-foreground">
                With checking off, every answer is marked "Unverified".
              </AlertDescription>
            </Alert>
          )}

          {bundled && <BundledInfo bundled={bundled} />}

          <CapabilityFields
            capability={capability}
            form={form}
            idPrefix={`cap-${capability.id}`}
            showErrors={showErrors}
            models={models}
          />

          {save.isError && (
            <p role="alert" className="text-sm text-destructive">
              Could not save: {errorMessage(save.error)}
            </p>
          )}
          {test.isError && (
            <p role="alert" className="text-sm text-destructive">
              Could not run the test: {errorMessage(test.error)}
            </p>
          )}
        </CardContent>

        <CardFooter className="flex-wrap justify-between">
          <div className="min-w-0 flex-1">
            {result ? (
              <TestResultView result={result} />
            ) : capability.last_test ? (
              <TestResultView result={capability.last_test} previous />
            ) : saved && !form.dirty ? (
              <span className="text-sm text-muted-foreground" role="status">
                Saved.
              </span>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={onTest} disabled={test.isPending}>
              {test.isPending ? <Spinner label="Testing" /> : <IconPlugConnected aria-hidden />}
              Test
            </Button>
            {capability.fields.length > 0 && (
              <Button type="submit" size="sm" disabled={!form.dirty || save.isPending}>
                {save.isPending && <Spinner label="Saving" />}
                Save
              </Button>
            )}
          </div>
        </CardFooter>
      </form>
    </Card>
  );
}

function BundledInfo({ bundled }: { bundled: NonNullable<Capability["bundled"]> }) {
  return (
    <div className="grid gap-1.5 rounded-md bg-muted/60 px-3 py-2 text-xs">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        <IconCpu className="size-3.5" aria-hidden />
        Built-in model
      </div>
      <div
        className="font-mono text-[12.5px] break-all text-foreground"
        data-testid="bundled-model"
      >
        {bundled.model_id}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-muted-foreground">
        <span>
          License: <span className="text-foreground">{bundled.license}</span>
        </span>
        {bundled.revision && (
          <span>
            Revision:{" "}
            <span className="font-mono text-foreground">{bundled.revision.slice(0, 12)}</span>
          </span>
        )}
      </div>
      {bundled.fallback && (
        <p className="text-warning">Using the fallback model because the default did not load.</p>
      )}
      {!bundled.present && (
        <p className="text-destructive">The model files are missing from this install.</p>
      )}
    </div>
  );
}
