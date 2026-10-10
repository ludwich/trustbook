import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "@/test/server";
import { api, ApiError, detailToMessage } from "./client";

interface Captured {
  method: string;
  path: string;
  search: string;
  body: unknown;
  contentType: string | null;
}

/** Records every request and replies with `reply`. */
function capture(reply: () => Response) {
  const seen: Captured[] = [];
  server.use(
    http.all("*/api/*", async ({ request }) => {
      const url = new URL(request.url);
      const text = await request.text();
      seen.push({
        method: request.method,
        path: url.pathname,
        search: url.search,
        body: text ? JSON.parse(text) : undefined,
        contentType: request.headers.get("content-type"),
      });
      return reply();
    }),
  );
  return seen;
}

describe("api client", () => {
  it("GETs JSON and returns the parsed body", async () => {
    server.use(
      http.get("*/api/setup", () => HttpResponse.json({ completed: false, chat_ready: false })),
    );
    await expect(api.getSetup()).resolves.toEqual({ completed: false, chat_ready: false });
  });

  it("sends JSON bodies with the right method and path", async () => {
    const seen = capture(() => HttpResponse.json({}));
    await api.updateCapability("chat", { preset: "ollama", model: "qwen3" });
    await api.testCapability("chat", { base_url: "http://x/v1" });
    await api.listModels("chat");
    await api.createSpace({ name: "Acme" });
    await api.updateSettings({ power: "high" });
    await api.reorderJobs(["b", "a"]);

    expect(seen.map((s) => `${s.method} ${s.path}`)).toEqual([
      "PUT /api/capabilities/chat",
      "POST /api/capabilities/chat/test",
      "POST /api/capabilities/chat/models",
      "POST /api/spaces",
      "PUT /api/settings",
      "POST /api/jobs/reorder",
    ]);
    expect(seen[0]!.body).toEqual({ preset: "ollama", model: "qwen3" });
    expect(seen[0]!.contentType).toBe("application/json");
    expect(seen[1]!.body).toEqual({ base_url: "http://x/v1" });
    // No draft → empty object, so the backend tests (and stores) the saved config.
    expect(seen[2]!.body).toEqual({});
    expect(seen[5]!.body).toEqual({ job_ids: ["b", "a"] });
  });

  it("handles 204 responses and job actions", async () => {
    const seen = capture(() => new HttpResponse(null, { status: 204 }));
    await expect(api.deleteSpace("sp 1")).resolves.toBeUndefined();
    await expect(api.setActiveSpace("sp_1")).resolves.toBeUndefined();
    await expect(api.clearRequestLog()).resolves.toBeUndefined();
    await api.cancelJob("j1");
    await api.pauseJob("j1");
    await api.resumeJob("j1");
    expect(seen.map((s) => `${s.method} ${s.path}`)).toEqual([
      "DELETE /api/spaces/sp%201",
      "PUT /api/active-space",
      "DELETE /api/request-log",
      "POST /api/jobs/j1/cancel",
      "POST /api/jobs/j1/pause",
      "POST /api/jobs/j1/resume",
    ]);
    expect(seen[1]!.body).toEqual({ space_id: "sp_1" });
  });

  it("passes request-log paging parameters", async () => {
    const seen = capture(() => HttpResponse.json([]));
    await api.listRequestLog();
    await api.listRequestLog({ limit: 20, beforeId: 55 });
    expect(seen[0]!.search).toBe("?limit=100");
    expect(seen[1]!.search).toBe("?limit=20&before_id=55");
  });

  it("turns FastAPI string details into ApiError messages", async () => {
    server.use(
      http.post("*/api/capabilities/chat/models", () =>
        HttpResponse.json({ detail: "The endpoint refused the connection." }, { status: 502 }),
      ),
    );
    const err = await api.listModels("chat", {}).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(502);
    expect((err as ApiError).message).toBe("The endpoint refused the connection.");
  });

  it("joins validation error lists and falls back on non-JSON errors", async () => {
    server.use(
      http.post("*/api/spaces", () =>
        HttpResponse.json(
          { detail: [{ loc: ["body", "name"], msg: "String should have at least 1 character" }] },
          { status: 422 },
        ),
      ),
      http.get("*/api/settings", () => new HttpResponse("boom", { status: 500 })),
    );
    await expect(api.createSpace({ name: "" })).rejects.toThrow(
      "name: String should have at least 1 character",
    );
    await expect(api.getSettings()).rejects.toThrow("Trustbook had a problem. Try again.");
  });

  it("reports network failures in plain English", async () => {
    server.use(http.get("*/api/health", () => HttpResponse.error()));
    await expect(api.health()).rejects.toMatchObject({
      status: 0,
      message: expect.stringMatching(/reach Trustbook/),
    });
  });

  it("detailToMessage falls back when detail is unusable", () => {
    expect(detailToMessage(undefined, "x")).toBe("x");
    expect(detailToMessage([{ nope: 1 }], "y")).toBe("y");
  });
});
