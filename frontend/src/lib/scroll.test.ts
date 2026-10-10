import { describe, expect, it } from "vitest";
import {
  centeredScrollTop,
  CODE_LINE_HEIGHT_PX,
  CODE_PADDING_TOP_PX,
  codeScrollTop,
  highlightWindow,
} from "./scroll";

describe("centeredScrollTop", () => {
  it("centres a band that fits", () => {
    // Band 400–440 in a 400 px viewport → 400 − (400 − 40) / 2 = 220.
    expect(centeredScrollTop(400, 40, 400)).toBe(220);
  });
  it("puts the top of a tall band just below the margin", () => {
    expect(centeredScrollTop(1000, 800, 400)).toBe(984);
    expect(centeredScrollTop(1000, 800, 400, 0)).toBe(1000);
  });
  it("never scrolls above the top", () => {
    expect(centeredScrollTop(10, 20, 400)).toBe(0);
    expect(centeredScrollTop(5, 20, 0)).toBe(0);
  });
});

describe("highlightWindow", () => {
  it("maps the 1-based highlight to indexes in the window", () => {
    expect(highlightWindow(1, 100, [12, 15])).toEqual({ start: 11, count: 4 });
    // A window that starts at line 301.
    expect(highlightWindow(301, 450, [500, 520])).toEqual({ start: 199, count: 21 });
  });
  it("clamps ranges that fall outside the window", () => {
    expect(highlightWindow(10, 5, [1, 3])).toEqual({ start: 0, count: 1 });
    expect(highlightWindow(10, 5, [12, 99])).toEqual({ start: 2, count: 3 });
    expect(highlightWindow(1, 0, [1, 1])).toEqual({ start: 0, count: 0 });
  });
});

describe("codeScrollTop", () => {
  it("follows from line numbers and the fixed row height", () => {
    const top = CODE_PADDING_TOP_PX + 99 * CODE_LINE_HEIGHT_PX;
    const height = 3 * CODE_LINE_HEIGHT_PX;
    expect(codeScrollTop(1, 300, [100, 102], 500)).toBe(Math.round(top - (500 - height) / 2));
  });
  it("is 0 when the highlight is near the top", () => {
    expect(codeScrollTop(1, 300, [2, 4], 500)).toBe(0);
  });
});
