import { http, HttpResponse } from "msw";
import { allCapabilities, jobList, overview, reindexStatus } from "./fixtures";

/** Baseline API: setup done, nothing running, no spaces. Tests override with server.use(). */
export function defaultHandlers() {
  return [
    http.get("*/api/setup", () => HttpResponse.json({ completed: true, chat_ready: true })),
    http.get("*/api/jobs", () => HttpResponse.json(jobList())),
    http.get("*/api/capabilities", () => HttpResponse.json(allCapabilities())),
    http.get("*/api/spaces", () => HttpResponse.json([])),
    http.put("*/api/active-space", () => new HttpResponse(null, { status: 204 })),
    http.get("*/api/settings", () =>
      HttpResponse.json({
        power: "normal",
        setup_completed: true,
        workers: 2,
        thresholds: { refuse: 0.15, verify: 0.5 },
        hints_dismissed: [],
      }),
    ),
    http.get("*/api/calibration", () => HttpResponse.json({ status: "done", results: null })),
    http.get("*/api/request-log", () => HttpResponse.json([])),
    // Phase 1: an empty space.
    http.get("*/api/spaces/:sid/sources", () => HttpResponse.json([])),
    http.get("*/api/spaces/:sid/chat/status", () =>
      HttpResponse.json({
        enabled_sources: 0,
        ready_sources: 0,
        total_sources: 0,
        chat_ready: false,
      }),
    ),
    http.get("*/api/spaces/:sid/conversations", () => HttpResponse.json([])),
    http.get("*/api/spaces/:sid/overview", () => HttpResponse.json(overview())),
    http.get("*/api/spaces/:sid/reindex", () => HttpResponse.json(reindexStatus())),
  ];
}
