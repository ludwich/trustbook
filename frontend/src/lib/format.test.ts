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

  it("describes worker counts per power level", () => {
    expect(workersFor("low", 8)).toBe("1 worker");
    expect(workersFor("normal", 8)).toBe("2 workers");
    expect(workersFor("high", 8)).toBe("6 workers");
    expect(workersFor("high", 2)).toBe("2 workers");
    expect(workersFor("high", null)).toBe("most CPU cores");
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
  it("returns null for results without CPU or runtime details", () => {
    expect(formatComputer(undefined, undefined)).toBeNull();
    expect(formatComputer(null, null)).toBeNull();
  });
});
