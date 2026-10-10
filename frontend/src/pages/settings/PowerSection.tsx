import type { Power } from "@/api/types";
import { useCalibration, useSettings, useUpdateSettings } from "@/api/queries";
import { QueryError } from "@/components/common/QueryError";
import { Skeleton } from "@/components/ui/skeleton";
import { workersFor } from "@/lib/power";
import { cn, errorMessage } from "@/lib/utils";
import { SettingsSection } from "./SettingsSection";

const LEVELS: { value: Power; label: string; hint: string }[] = [
  { value: "low", label: "Low", hint: "Keeps the computer free for other work." },
  { value: "normal", label: "Normal", hint: "A good balance for most laptops." },
  { value: "high", label: "High", hint: "Fastest. The computer may feel busy." },
];

export function PowerSection() {
  const settings = useSettings();
  const calibration = useCalibration();
  const update = useUpdateSettings();
  const cpu = calibration.data?.results?.cpu_count ?? null;

  return (
    <SettingsSection
      id="power"
      title="Processing power"
      description="How much of this computer Trustbook uses to read and index sources."
    >
      {settings.isPending && <Skeleton className="h-20" />}
      {settings.isError && (
        <QueryError error={settings.error} onRetry={() => void settings.refetch()} />
      )}
      {settings.data && (
        <div className="grid gap-2">
          <div
            role="radiogroup"
            aria-label="Processing power"
            className="grid gap-2 sm:grid-cols-3"
          >
            {LEVELS.map((level) => {
              const checked =
                (update.isPending ? update.variables?.power : settings.data.power) === level.value;
              return (
                <label
                  key={level.value}
                  className={cn(
                    "flex cursor-pointer gap-2.5 rounded-lg border bg-card px-3 py-2.5 transition-colors",
                    "has-[:focus-visible]:ring-[3px] has-[:focus-visible]:ring-ring/50",
                    checked ? "border-primary bg-info-soft" : "hover:bg-accent",
                  )}
                >
                  <input
                    type="radio"
                    name="power"
                    value={level.value}
                    checked={checked}
                    disabled={update.isPending}
                    onChange={() => update.mutate({ power: level.value })}
                    className="mt-1 accent-[var(--primary)]"
                  />
                  <span className="grid gap-0.5">
                    <span className="font-medium">
                      {level.label}{" "}
                      <span className="font-normal text-muted-foreground">
                        · {workersFor(level.value, cpu)}
                      </span>
                    </span>
                    <span className="text-xs text-muted-foreground">{level.hint}</span>
                  </span>
                </label>
              );
            })}
          </div>
          <p className="text-xs text-muted-foreground" role="status">
            Now using {settings.data.workers} {settings.data.workers === 1 ? "worker" : "workers"}.
          </p>
          {update.isError && (
            <p role="alert" className="text-sm text-destructive">
              Could not change the setting: {errorMessage(update.error)}
            </p>
          )}
        </div>
      )}
    </SettingsSection>
  );
}
