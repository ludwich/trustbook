import { useEffect } from "react";
import { useLocation } from "react-router";
import { useCapabilities } from "@/api/queries";
import { CapabilityCard } from "@/components/capabilities/CapabilityCard";
import { PageHeader } from "@/components/common/PageHeader";
import { QueryError } from "@/components/common/QueryError";
import { Skeleton } from "@/components/ui/skeleton";
import { CalibrationSection } from "./settings/CalibrationSection";
import { PowerSection } from "./settings/PowerSection";
import { RequestLogSection } from "./settings/RequestLogSection";

const SECTIONS = [
  { id: "capabilities", label: "Models and services" },
  { id: "power", label: "Processing power" },
  { id: "calibration", label: "Speed test" },
  { id: "request-log", label: "Request log" },
];

export function SettingsPage() {
  const caps = useCapabilities();
  const { hash } = useLocation();
  const loaded = caps.isSuccess;

  // Support deep links such as /settings#capability-chat ("Set up in Settings").
  useEffect(() => {
    if (!hash || !loaded) return;
    const el = document.getElementById(decodeURIComponent(hash.slice(1)));
    el?.scrollIntoView?.({ block: "start" });
  }, [hash, loaded]);

  const visible = caps.data?.filter((c) => !c.hidden) ?? [];

  return (
    <div className="mx-auto w-full max-w-6xl px-5 py-6">
      <PageHeader title="Settings" description="Everything is stored on this computer." />

      <nav
        aria-label="Settings sections"
        className="mb-6 flex flex-wrap gap-1 border-b pb-2 text-sm"
      >
        {SECTIONS.map((s) => (
          <a
            key={s.id}
            href={`#${s.id}`}
            className="rounded-md px-2.5 py-1 text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            {s.label}
          </a>
        ))}
      </nav>

      <section id="capabilities" aria-labelledby="capabilities-title" className="scroll-mt-20">
        <h2 id="capabilities-title" className="text-base font-semibold">
          Models and services
        </h2>
        <p className="mb-3 text-sm text-muted-foreground">
          "Local" runs on this computer. "Remote" sends text to another machine; the request log
          below shows exactly what was sent.
        </p>
        {caps.isPending && (
          <div className="grid gap-4 lg:grid-cols-2">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-56" />
            ))}
          </div>
        )}
        {caps.isError && (
          <QueryError
            title="Could not load settings"
            error={caps.error}
            onRetry={() => void caps.refetch()}
          />
        )}
        {caps.data && (
          <div className="grid items-start gap-4 lg:grid-cols-2">
            {visible.map((c) => (
              <CapabilityCard key={c.id} capability={c} />
            ))}
          </div>
        )}
      </section>

      <PowerSection />
      <CalibrationSection />
      <RequestLogSection />
    </div>
  );
}
