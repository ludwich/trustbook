import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { RequestLogEntry } from "@/api/types";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const entry: RequestLogEntry = {
  id: 7,
  timestamp: "2026-10-10T08:15:00Z",
  capability: "chat",
  endpoint_host: "api.openai.com",
  space_id: "sp_1",
  space_name: "Acme audit",
  bytes_sent: 4096,
  tokens_sent: 900,
  excerpt: '{"messages":[{"role":"user","content":"Reply with OK"}]}',
};

describe("settings", () => {
  it("renders a card per visible capability (hidden ones skipped)", async () => {
    renderApp("/settings");
    expect(await screen.findByTestId("capability-card-chat")).toBeInTheDocument();
    for (const id of ["gate", "embeddings", "grounding", "stt", "web_search"]) {
      expect(screen.getByTestId(`capability-card-${id}`)).toBeInTheDocument();
    }
    expect(screen.queryByTestId("capability-card-tts")).not.toBeInTheDocument();
  });

  it("changes the power setting", async () => {
    const user = userEvent.setup();
    let body: unknown = null;
    server.use(
      http.get("*/api/calibration", () =>
        HttpResponse.json({
          status: "done",
          results: {
            version: 1,
            measured_at: "2026-10-10T07:00:00Z",
            cpu_count: 12,
            embedding_chunks_per_s: 41.5,
            rerank_pairs_per_s: 120,
            parse_pages_per_s: null,
            stt_realtime_factor: 0.18,
            notes: ["Document parsing arrives in the next phase."],
          },
        }),
      ),
      http.put("*/api/settings", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({
          power: "high",
          setup_completed: true,
          workers: 10,
          thresholds: { refuse: 0.15, verify: 0.5 },
        });
      }),
    );
    renderApp("/settings");
    const power = await screen.findByRole("radiogroup", { name: "Processing power" });
    expect(within(power).getByRole("radio", { name: /normal/i })).toBeChecked();
    await waitFor(() => expect(within(power).getByText(/10 workers/)).toBeInTheDocument());
    await user.click(within(power).getByRole("radio", { name: /high/i }));
    await waitFor(() => expect(body).toEqual({ power: "high" }));
    expect(await screen.findByText("Now using 10 workers.")).toBeInTheDocument();

    // Calibration results.
    expect(screen.getByText("41.5 chunks per second")).toBeInTheDocument();
    expect(screen.getByText("Document parsing arrives in the next phase.")).toBeInTheDocument();
  });

  it("shows the request log with expandable excerpt and clears it after confirming", async () => {
    const user = userEvent.setup();
    let entries = [entry];
    server.use(
      http.get("*/api/request-log", () => HttpResponse.json(entries)),
      http.delete("*/api/request-log", () => {
        entries = [];
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderApp("/settings");
    const section = (await screen.findByRole("heading", { name: "Request log" })).closest(
      "section",
    )!;
    const row = await within(section).findByText("api.openai.com");
    expect(row).toBeInTheDocument();
    expect(within(section).getByText("Acme audit")).toBeInTheDocument();
    expect(within(section).getByText("900 tokens")).toBeInTheDocument();

    const excerpt = within(section).getByRole("button", { name: /Reply with OK/ });
    expect(excerpt).toHaveAttribute("aria-expanded", "false");
    await user.click(excerpt);
    expect(excerpt).toHaveAttribute("aria-expanded", "true");

    await user.click(within(section).getByRole("button", { name: "Clear log" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Clear log" }));
    expect(await within(section).findByText("Nothing sent yet")).toBeInTheDocument();
  });

  it("refreshes the request log after a capability test, which sends a logged request", async () => {
    const user = userEvent.setup();
    let entries: RequestLogEntry[] = [];
    server.use(
      http.get("*/api/request-log", () => HttpResponse.json(entries)),
      http.post("*/api/capabilities/embeddings/test", () => {
        entries = [entry];
        return HttpResponse.json({
          ok: true,
          message: "Connected.",
          latency_ms: 120,
          tested_at: "2026-10-10T08:15:00Z",
        });
      }),
    );
    renderApp("/settings");
    const section = (await screen.findByRole("heading", { name: "Request log" })).closest(
      "section",
    )!;
    expect(await within(section).findByText("Nothing sent yet")).toBeInTheDocument();

    const card = await screen.findByTestId("capability-card-embeddings");
    await user.click(within(card).getByRole("button", { name: "Test" }));
    expect(await within(section).findByText("api.openai.com")).toBeInTheDocument();
  });
});
