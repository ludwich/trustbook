import { describe, expect, it } from "vitest";
import { normaliseUrl } from "./url";

describe("normaliseUrl", () => {
  it("adds https:// when the scheme is missing", () => {
    expect(normaliseUrl(" example.com/page ")).toBe("https://example.com/page");
  });

  it("keeps http and https links, including YouTube", () => {
    expect(normaliseUrl("https://www.youtube.com/watch?v=abc")).toBe(
      "https://www.youtube.com/watch?v=abc",
    );
    expect(normaliseUrl("http://localhost:8000/x")).toBe("http://localhost:8000/x");
  });

  it("rejects things that are not web addresses", () => {
    expect(normaliseUrl("")).toBeNull();
    expect(normaliseUrl("hello")).toBeNull();
    expect(normaliseUrl("ftp://example.com/file")).toBeNull();
    expect(normaliseUrl("file:///etc/passwd")).toBeNull();
  });
});
