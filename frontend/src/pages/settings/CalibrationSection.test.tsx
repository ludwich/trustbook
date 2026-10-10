import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { Calibration } from "@/api/types";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const base: Calibration = {
  version: 2,
  measured_at: "2026-10-10T07:00:00Z",
  cpu_count: 12,
  embedding_chunks_per_s: 41.5,
  rerank_pairs_per_s: 120,
  parse_pages_per_s: null,
  stt_realtime_factor: 0.04,
  notes: ["Document parsing arrives in the next phase."],
};

function serve(results: Calibration) {
  server.use(http.get("*/api/calibration", () => HttpResponse.json({ status: "done", results })));
}

async function speedTestSection() {
  const heading = await screen.findByRole("heading", { name: "Speed test" });
  const section = heading.closest("section")!;
  await within(section).findByText("Indexing");
  return within(section);
}

describe("speed test section", () => {
  it("renders older results without the computer line", async () => {
    serve(base);
    renderApp("/settings");
    const s = await speedTestSection();
    expect(s.getByText("41.5 chunks per second")).toBeInTheDocument();
    expect(s.getByText("Ranking")).toBeInTheDocument();
    expect(s.getByText("120 passages per second")).toBeInTheDocument();
    expect(s.getByText("Reading documents")).toBeInTheDocument();
    expect(s.getByText("Not measured")).toBeInTheDocument();
    expect(s.getByText("Speech-to-text")).toBeInTheDocument();
    expect(s.getByText("25× faster than real time")).toBeInTheDocument();
    expect(s.getByText("CPU cores")).toBeInTheDocument();
    expect(s.queryByText(/This computer/)).not.toBeInTheDocument();
    expect(s.getByText("Document parsing arrives in the next phase.")).toBeInTheDocument();
  });

  it("adds a compact computer line when cpu and runtime are present", async () => {
    serve({
      ...base,
      version: 3,
      parse_pages_per_s: 3.25,
      stt_realtime_factor: 2.5,
      cpu: { model: "AMD Ryzen AI Max+ 395", logical: 32, usable: 32, physical: 16, bf16: true },
      runtime: { torch_threads: 16, embedding_dtype: "bfloat16", reranker_dtype: "float32" },
    });
    renderApp("/settings");
    const s = await speedTestSection();
    expect(s.getByText("This computer:")).toBeInTheDocument();
    expect(
      s.getByText("AMD Ryzen AI Max+ 395 · uses 32 of 32 CPUs · 16 model threads · BF16"),
    ).toBeInTheDocument();
    expect(s.getByText("3.3 pages per second")).toBeInTheDocument();
    expect(s.getByText("2.5× slower than real time")).toBeInTheDocument();
    expect(s.queryByText("CPU cores")).not.toBeInTheDocument();
    expect(s.getByText("Document parsing arrives in the next phase.")).toBeInTheDocument();
  });

  it("shows code indexing speed and parallel streams (v5)", async () => {
    serve({
      ...base,
      version: 5,
      embedding_chunks_per_s_code: 18.25,
      cpu: { model: "AMD Ryzen AI Max+ 395", logical: 32, usable: 32, physical: 16, bf16: true },
      runtime: {
        torch_threads: 16,
        embedding_dtype: "bfloat16",
        reranker_dtype: "float32",
        embedding_streams: 4,
        stream_threads: 4,
      },
    });
    renderApp("/settings");
    const s = await speedTestSection();
    expect(s.getByText("Indexing code")).toBeInTheDocument();
    expect(s.getByText("18.3 chunks per second")).toBeInTheDocument();
    expect(
      s.getByText(
        "AMD Ryzen AI Max+ 395 · uses 32 of 32 CPUs · 16 model threads · 4 streams · BF16",
      ),
    ).toBeInTheDocument();
  });

  it("hides 'Indexing code' on older results and says when it was not measured", async () => {
    serve({
      ...base,
      runtime: {
        torch_threads: 8,
        embedding_dtype: "float32",
        reranker_dtype: "float32",
        embedding_streams: 1,
      },
    });
    const { unmount } = renderApp("/settings");
    let s = await speedTestSection();
    expect(s.queryByText("Indexing code")).not.toBeInTheDocument();
    // One stream is the normal case: not mentioned.
    expect(s.getByTestId("calibration-computer")).not.toHaveTextContent("stream");
    unmount();

    serve({ ...base, version: 5, embedding_chunks_per_s_code: null });
    renderApp("/settings");
    s = await speedTestSection();
    expect(s.getByText("Indexing code")).toBeInTheDocument();
    expect(s.getAllByText("Not measured").length).toBeGreaterThanOrEqual(2);
  });
});
