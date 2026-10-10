import type { Power, Settings } from "@/api/types";

function workers(n: number): string {
  return `${n} ${n === 1 ? "worker" : "workers"}`;
}

/** SPEC §11 fixed counts, used only when the backend does not send `workers_by_power`. */
const FIXED: Partial<Record<Power, number>> = { low: 1, normal: 2 };

/**
 * Label for a power level's worker count, from the backend's real (capped) counts.
 * Older backends without `workers_by_power`: low and normal are fixed by the spec, the current
 * level is `workers`, and High is described without a number (its cap is only known server-side).
 */
export function workersFor(
  power: Power,
  settings: Pick<Settings, "power" | "workers" | "workers_by_power">,
): string {
  const exact = settings.workers_by_power?.[power];
  if (typeof exact === "number" && exact > 0) return workers(exact);
  if (settings.power === power) return workers(settings.workers);
  const fixed = FIXED[power];
  return fixed !== undefined ? workers(fixed) : "most CPU cores";
}
