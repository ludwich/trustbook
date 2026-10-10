import { describe, expect, it } from "vitest";
import { formatBytes, formatComputer, formatEta, formatRealtimeFactor, plural } from "./format";
import { localityOfUrl } from "./locality";
import { workersFor } from "./power";

describe("formatEta", () => {
  it("returns null without an estimate", () => {
    expect(formatEta(null)).toBeNull();
    expect(formatEta(undefined)).toBeNull();
  });
  it("rounds up to whole minutes", () => {
    expect(formatEta(30)).toBe("less than a minute left");
    expect(formatEta(61)).toBe("about 2 min left");
    expect(formatEta(25 * 60)).toBe("about 25 min left");
  });
  it("switches to hours for long jobs", () => {
    expect(formatEta(2 * 3600)).toBe("about 2 h left");
    expect(formatEta(2 * 3600 + 20 * 60)).toBe("about 2 h 20 min left");
  });
});

describe("helpers", () => {
  it("pluralises and formats bytes", () => {
    expect(plural(1, "job")).toBe("1 job");
    expect(plural(3, "job")).toBe("3 jobs");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
  });

  it("classifies locality like the backend (LAN is remote)", () => {
    expect(localityOfUrl("http://host.docker.internal:8080/v1")?.locality).toBe("local");
    expect(localityOfUrl("http://localhost:11434/v1")?.locality).toBe("local");
    expect(localityOfUrl("http://192.168.1.20:8080/v1")?.locality).toBe("remote");
    expect(localityOfUrl("not a url")).toBeNull();
  });

  it("labels power levels with the backend's real (capped) worker counts", () => {
    const s = {
      power: "normal" as const,
      workers: 2,
      workers_by_power: { low: 1, normal: 2, high: 8 },
    };
    expect(workersFor("low", s)).toBe("1 worker");
    expect(workersFor("normal", s)).toBe("2 workers");
    // G1: 30 cores but capped at 8 → the label says 8, not 30 − 2.
    expect(workersFor("high", s)).toBe("8 workers");
  });

  it("falls back gracefully when workers_by_power is absent", () => {
    expect(workersFor("low", { power: "normal", workers: 2 })).toBe("1 worker");
    expect(workersFor("normal", { power: "normal", workers: 2 })).toBe("2 workers");
    // High's cap is only known by the backend: no made-up number…
    expect(workersFor("high", { power: "normal", workers: 2 })).toBe("most CPU cores");
    // …unless it is the current level, whose effective count is `workers`.
    expect(workersFor("high", { power: "high", workers: 8 })).toBe("8 workers");
    // A zero or missing entry is ignored.
    expect(
      workersFor("high", {
        power: "low",
        workers: 1,
        workers_by_power: { low: 1, normal: 2, high: 0 },
      }),
    ).toBe("most CPU cores");
  });
});

describe("formatRealtimeFactor", () => {
  it("shows faster-than-real-time as a multiple", () => {
    expect(formatRealtimeFactor(0.04)).toBe("25× faster than real time");
    expect(formatRealtimeFactor(0.4)).toBe("2.5× faster than real time");
    expect(formatRealtimeFactor(0.18)).toBe("5.6× faster than real time");
    expect(formatRealtimeFactor(0.033)).toBe("30× faster than real time");
  });
  it("treats values near 1 as about real time", () => {
    expect(formatRealtimeFactor(1.0)).toBe("about real time");
    expect(formatRealtimeFactor(0.96)).toBe("about real time");
    expect(formatRealtimeFactor(1.05)).toBe("about real time");
  });
  it("shows slower-than-real-time as the factor itself", () => {
    expect(formatRealtimeFactor(2.5)).toBe("2.5× slower than real time");
    expect(formatRealtimeFactor(12.3)).toBe("12× slower than real time");
  });
  it("handles missing or invalid values", () => {
    expect(formatRealtimeFactor(null)).toBe("Not measured");
    expect(formatRealtimeFactor(undefined)).toBe("Not measured");
    expect(formatRealtimeFactor(0)).toBe("Not measured");
  });
});

describe("formatComputer", () => {
  const cpu = { model: "AMD Ryzen AI Max+ 395", logical: 32, usable: 32 };
  const runtime = { torch_threads: 16, embedding_dtype: "bfloat16" };
  it("joins model, CPUs, threads and precision", () => {
    expect(formatComputer(cpu, runtime)).toBe(
      "AMD Ryzen AI Max+ 395 · uses 32 of 32 CPUs · 16 model threads · BF16",
    );
  });
  it("omits unknown parts and defaults precision to FP32", () => {
    expect(
      formatComputer(
        { model: null, logical: 8, usable: 6 },
        { torch_threads: null, embedding_dtype: null },
      ),
    ).toBe("uses 6 of 8 CPUs · FP32");
    expect(formatComputer(cpu, null)).toBe("AMD Ryzen AI Max+ 395 · uses 32 of 32 CPUs");
  });
  it("adds 'S streams' only for more than one embedding stream", () => {
    expect(formatComputer(cpu, { ...runtime, embedding_streams: 2 })).toBe(
      "AMD Ryzen AI Max+ 395 · uses 32 of 32 CPUs · 16 model threads · 2 streams · BF16",
    );
    expect(formatComputer(cpu, { ...runtime, embedding_streams: 1 })).not.toContain("stream");
    expect(formatComputer(cpu, { ...runtime, embedding_streams: null })).not.toContain("stream");
  });
  it("returns null for results without CPU or runtime details", () => {
    expect(formatComputer(undefined, undefined)).toBeNull();
    expect(formatComputer(null, null)).toBeNull();
  });
});
