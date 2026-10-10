import { describe, expect, it } from "vitest";
import { formatClock } from "./format";
import { formatLocator } from "./locator";

describe("formatLocator", () => {
  it("formats pages", () => {
    expect(formatLocator({ pages: [3] })).toBe("Page 3");
    expect(formatLocator({ pages: [4, 3] })).toBe("Pages 3–4");
  });

  it("formats timestamps as mm:ss with an optional speaker", () => {
    expect(formatLocator({ start_s: 61.2, end_s: 95, speaker: null })).toBe("01:01–01:35");
    expect(formatLocator({ start_s: 3725, speaker: "Anna" })).toBe("1:02:05 · Anna");
  });

  it("formats sections and files", () => {
    expect(formatLocator({ heading: "Install", offset: 1234 })).toBe("Section: Install");
    expect(
      formatLocator({ file: "src/requests/api.py", start_line: 12, end_line: 58, symbol: "get" }),
    ).toBe("src/requests/api.py, lines 12–58 (get)");
  });

  it("returns an empty string for an empty locator", () => {
    expect(formatLocator({})).toBe("");
    expect(formatLocator(null)).toBe("");
  });
});

describe("formatClock", () => {
  it("pads minutes and seconds", () => {
    expect(formatClock(5)).toBe("00:05");
    expect(formatClock(600)).toBe("10:00");
  });
});
