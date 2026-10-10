import { describe, expect, it } from "vitest";
import { formatBytes, formatEta, plural } from "./format";
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
