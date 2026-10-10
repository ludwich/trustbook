import type { Power } from "@/api/types";

/** Worker counts per SPEC §11. High depends on the CPU count when we know it. */
export function workersFor(power: Power, cpuCount: number | null): string {
  if (power === "low") return "1 worker";
  if (power === "normal") return "2 workers";
  if (cpuCount === null) return "most CPU cores";
  const n = Math.max(2, cpuCount - 2);
  return `${n} workers`;
}
