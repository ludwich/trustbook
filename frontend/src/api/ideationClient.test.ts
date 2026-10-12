import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { server } from "@/test/server";
import { sseEvent, sseResponse } from "@/test/sse";
import { api, ApiError, toIdeationEvent } from "./client";
import type { IdeationStreamEvent } from "./types";

async function collect(gen: AsyncGenerator<IdeationStreamEvent>): Promise<IdeationStreamEvent[]> {
  const out: IdeationStreamEvent[] = [];
  for await (const ev of gen) out.push(ev);
  return out;
}

describe("api client: ideation", () => {
  it("uses the documented method and path for every session call", async () => {
    const seen: string[] = [];
    const bodies: unknown[] = [];
    server.use(
      http.all("*/api/spaces/*", async ({ request }) => {
        const url = new URL(request.url);
        seen.push(`${request.method} ${url.pathname}`);
        const text = await request.text();
        if (text) bodies.push(JSON.parse(text));
        return request.method === "DELETE"
          ? new HttpResponse(null, { status: 204 })
          : HttpResponse.json({});
      }),
    );
    await api.getIdeationEstimate("s 1");
    await api.listIdeationSessions("s1");
    await api.createIdeationSession("s1", "Puffin walks");
    await api.getIdeationSession("s1", "is1");
    await api.dismissIdeationNode("s1", "is1", "n/2");
    await api.finishIdeationSession("s1", "is1");
    await api.continueIdeationSession("s1", "is1");
    await expect(api.deleteIdeationSession("s1", "is1")).resolves.toBeUndefined();

    expect(seen).toEqual([
      "GET /api/spaces/s%201/ideation/estimate",
      "GET /api/spaces/s1/ideation/sessions",
      "POST /api/spaces/s1/ideation/sessions",
      "GET /api/spaces/s1/ideation/sessions/is1",
      "POST /api/spaces/s1/ideation/sessions/is1/nodes/n%2F2/dismiss",
      "POST /api/spaces/s1/ideation/sessions/is1/finish",
      "POST /api/spaces/s1/ideation/sessions/is1/continue",
      "DELETE /api/spaces/s1/ideation/sessions/is1",
    ]);
    expect(bodies).toEqual([{ idea: "Puffin walks" }]);
    expect(api.ideationBriefUrl("s1", "is 1")).toBe(
      "/api/spaces/s1/ideation/sessions/is%201/brief",
    );
  });

  it("streams a turn: typed status, done and error events; unknown or malformed ones skipped", async () => {
    let contentType: string | null = "unset";
    let body = "unset";
    server.use(
      http.post("*/api/spaces/s1/ideation/sessions/is1/turn", async ({ request }) => {
        expect(request.headers.get("accept")).toBe("text/event-stream");
        contentType = request.headers.get("content-type");
        body = await request.text();
        return sseResponse([
          sseEvent("status", { stage: "generating" }),
          "event: ping\ndata: {}\n\nevent: done\ndata: not json\n\n",
          sseEvent("sentence", { index: 0, text: "Not an ideation event." }),
          sseEvent("status", { stage: "retrieving" }),
          sseEvent("done", { session: { id: "is1", nodes: [] } }),
        ]);
      }),
    );
    const onOpen = vi.fn();
    const events = await collect(api.streamIdeationTurn("s1", "is1", { onOpen }));
    expect(onOpen).toHaveBeenCalledTimes(1);
    // No body: the turn takes none.
    expect(contentType).toBeNull();
    expect(body).toBe("");
    expect(events).toEqual([
      { type: "status", stage: "generating" },
      { type: "status", stage: "retrieving" },
      { type: "done", session: { id: "is1", nodes: [] } },
    ]);
  });

  it("sends an answer as JSON and passes the stream's error event on", async () => {
    let sent: unknown;
    server.use(
      http.post("*/api/spaces/s1/ideation/sessions/is1/answer", async ({ request }) => {
        expect(request.headers.get("content-type")).toBe("application/json");
        sent = await request.json();
        return sseResponse([
          sseEvent("status", { stage: "checking" }),
          sseEvent("error", {
            message: "The chat model's reply could not be used. Press Ask again.",
          }),
        ]);
      }),
    );
    const events = await collect(
      api.streamIdeationAnswer("s1", "is1", { node_id: "n1", action: "answer", text: "Families" }),
    );
    expect(sent).toEqual({ node_id: "n1", action: "answer", text: "Families" });
    expect(events).toEqual([
      { type: "status", stage: "checking" },
      { type: "error", message: "The chat model's reply could not be used. Press Ask again." },
    ]);
  });

  it("throws the 409 detail before streaming and does not report the stream as open", async () => {
    server.use(
      http.post("*/api/spaces/s1/ideation/sessions/is1/answer", () =>
        HttpResponse.json(
          { detail: "The next question is still being prepared. Wait a moment." },
          { status: 409 },
        ),
      ),
    );
    const onOpen = vi.fn();
    const run = collect(
      api.streamIdeationAnswer("s1", "is1", { node_id: "n1", action: "skip" }, { onOpen }),
    );
    await expect(run).rejects.toBeInstanceOf(ApiError);
    await expect(run).rejects.toMatchObject({
      status: 409,
      message: "The next question is still being prepared. Wait a moment.",
    });
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("maps events defensively", () => {
    expect(toIdeationEvent({ event: "status", data: "{}" })).toBeNull();
    expect(toIdeationEvent({ event: "done", data: '{"session": null}' })).toBeNull();
    expect(toIdeationEvent({ event: "error", data: "{}" })).toEqual({ type: "error", message: "" });
    expect(toIdeationEvent({ event: "error", data: "[oops" })).toBeNull();
  });
});
