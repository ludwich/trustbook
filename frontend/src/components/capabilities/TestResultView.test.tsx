import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TestResultView } from "./TestResultView";

const at = "2026-10-10T10:00:00.000Z";

describe("TestResultView", () => {
  it("adds the latency when the message has no time", () => {
    render(
      <TestResultView
        result={{ ok: true, message: "Model works.", latency_ms: 1534, tested_at: at }}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Works — Model works. (1,534 ms)");
  });

  it("does not repeat a time the message already gives", () => {
    render(
      <TestResultView
        result={{
          ok: true,
          message: "Connected. The model replied in 4.8 s.",
          latency_ms: 4779,
          tested_at: at,
        }}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(/replied in 4\.8 s\.$/);
  });
});
