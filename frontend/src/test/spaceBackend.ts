import { http, HttpResponse } from "msw";
import type {
  BrowseListing,
  ChatStatus,
  Chunk,
  ChunkContext,
  Conversation,
  Message,
  Overview,
  SecretsReport,
  Settings,
  Snapshot,
  Source,
  SourceEstimate,
  SourceEstimateItem,
  WebSearchResult,
} from "@/api/types";
import {
  chatCapability,
  embeddingsCapability,
  gateCapability,
  groundingCapability,
  overview as overviewFixture,
  searchCapability,
  settings as settingsFixture,
  source as sourceFixture,
  space as spaceFixture,
  sttCapability,
  ttsCapability,
  conversation as conversationFixture,
} from "./fixtures";
import { server } from "./server";

type StreamHandler = (args: {
  conversationId: string;
  content: string;
}) => Response | Promise<Response>;

export interface SpaceBackendOptions {
  sources?: Source[];
  conversations?: Conversation[];
  messages?: Record<string, Message[]>;
  overview?: Overview;
  settings?: Partial<Settings>;
  /** Overrides the status computed from the sources. */
  chatStatus?: Partial<ChatStatus>;
  chatReady?: boolean;
  searchReady?: boolean;
  browse?: Record<string, BrowseListing>;
  searchResults?: WebSearchResult[];
  chunks?: Record<string, Chunk>;
  snapshots?: Record<string, Snapshot>;
  /** GET …/chunks/{id}/context by chunk id (404 when missing). */
  contexts?: Record<string, ChunkContext>;
  /** GET …/sources/{id}/secrets by source id (404 when missing). */
  secrets?: Record<string, SecretsReport>;
  stream?: StreamHandler;
  /** POST …/sources/estimate. Default: a few seconds, no confirmation. */
  estimate?: (items: SourceEstimateItem[]) => SourceEstimate;
  /** GET …/gate-log, as the server would send it (raw rows). */
  gateLog?: Record<string, unknown>[];
}

/**
 * In-memory backend for one space ("sp_1") covering the Phase 1 API. Records every write so tests
 * can assert what the UI sent.
 */
export function spaceBackend(opts: SpaceBackendOptions = {}) {
  const state = {
    sources: [...(opts.sources ?? [])],
    conversations: [...(opts.conversations ?? [])],
    messages: { ...(opts.messages ?? {}) } as Record<string, Message[]>,
    overview: opts.overview ?? overviewFixture(),
    settings: settingsFixture(opts.settings),
    calls: {
      patch: [] as { id: string; body: unknown }[],
      enabled: [] as unknown[],
      paste: [] as unknown[],
      url: [] as unknown[],
      folder: [] as unknown[],
      upload: [] as string[][],
      search: [] as unknown[],
      fromSearch: [] as unknown[],
      refresh: [] as string[],
      deleted: [] as string[],
      browse: [] as string[],
      secrets: [] as string[],
      contexts: [] as string[],
      settings: [] as unknown[],
      overviewPut: [] as unknown[],
      overviewRefresh: 0,
      conversationsCreated: [] as unknown[],
      conversationsDeleted: [] as string[],
      asked: [] as { conversationId: string; content: string }[],
      estimate: [] as SourceEstimateItem[][],
      gateCorrect: [] as { id: number; body: unknown }[],
    },
    gateLog: [...(opts.gateLog ?? [])],
  };
  let nextId = 1;
  const newSource = (over: Partial<Source>): Source => {
    const s = sourceFixture({ id: `src_new${nextId++}`, status: "queued", ...over });
    state.sources.push(s);
    return s;
  };

  const chatReady = opts.chatReady ?? true;
  const caps = [
    chatCapability(chatReady ? { status: "ready", locality: "local" } : {}),
    gateCapability(),
    embeddingsCapability(),
    groundingCapability(),
    sttCapability(),
    searchCapability(
      opts.searchReady === false
        ? { status: "error", status_detail: "SearXNG is not answering." }
        : {},
    ),
    ttsCapability(),
  ];

  server.use(
    http.get("*/api/capabilities", () => HttpResponse.json(caps)),
    http.get("*/api/spaces/:sid", () =>
      HttpResponse.json(spaceFixture({ source_count: state.sources.length })),
    ),
    http.get("*/api/settings", () => HttpResponse.json(state.settings)),
    http.put("*/api/settings", async ({ request }) => {
      const body = (await request.json()) as { dismiss_hint?: string };
      state.calls.settings.push(body);
      if (body.dismiss_hint) {
        state.settings = {
          ...state.settings,
          hints_dismissed: [...state.settings.hints_dismissed, body.dismiss_hint],
        };
      }
      return HttpResponse.json(state.settings);
    }),

    // Sources
    http.get("*/api/spaces/:sid/sources", () => HttpResponse.json(state.sources)),
    http.post("*/api/spaces/:sid/sources/paste", async ({ request }) => {
      const body = (await request.json()) as { title?: string; text: string };
      state.calls.paste.push(body);
      return HttpResponse.json(newSource({ type: "paste", title: body.title ?? "Pasted text" }), {
        status: 201,
      });
    }),
    http.post("*/api/spaces/:sid/sources/url", async ({ request }) => {
      const body = (await request.json()) as { url: string };
      state.calls.url.push(body);
      return HttpResponse.json(
        newSource({ type: "url", group: "web", title: body.url, origin: body.url }),
        { status: 201 },
      );
    }),
    http.post("*/api/spaces/:sid/sources/folder", async ({ request }) => {
      const body = (await request.json()) as { path: string };
      state.calls.folder.push(body);
      return HttpResponse.json(newSource({ type: "folder", title: body.path }), { status: 201 });
    }),
    http.post("*/api/spaces/:sid/sources/upload", async ({ request }) => {
      const form = await request.formData();
      // Node's File, not jsdom's: check the shape instead of instanceof.
      const names = form
        .getAll("files")
        .map((f) => (typeof f === "object" && "name" in f ? String(f.name) : String(f)));
      state.calls.upload.push(names);
      return HttpResponse.json(
        names.map((n) => newSource({ title: n })),
        { status: 201 },
      );
    }),
    http.post("*/api/spaces/:sid/web-search", async ({ request }) => {
      state.calls.search.push(await request.json());
      return HttpResponse.json({ results: opts.searchResults ?? [] });
    }),
    http.post("*/api/spaces/:sid/sources/from-search", async ({ request }) => {
      const body = (await request.json()) as { items: { url: string; title: string }[] };
      state.calls.fromSearch.push(body);
      return HttpResponse.json(
        body.items.map((i) =>
          newSource({ type: "search", group: "web", title: i.title, origin: i.url }),
        ),
        { status: 201 },
      );
    }),
    http.put("*/api/spaces/:sid/sources/enabled", async ({ request }) => {
      const body = (await request.json()) as { source_ids: string[]; enabled: boolean };
      state.calls.enabled.push(body);
      if (state.sources.some((s) => body.source_ids.includes(s.id) && s.status === "failed")) {
        return HttpResponse.json({ detail: "A failed source can't be ticked." }, { status: 409 });
      }
      state.sources = state.sources.map((s) =>
        body.source_ids.includes(s.id) ? { ...s, enabled: body.enabled } : s,
      );
      return HttpResponse.json(state.sources.filter((s) => body.source_ids.includes(s.id)));
    }),
    http.patch("*/api/spaces/:sid/sources/:id", async ({ params, request }) => {
      const body = (await request.json()) as Partial<Source>;
      state.calls.patch.push({ id: String(params.id), body });
      const target = state.sources.find((s) => s.id === params.id);
      if (target?.status === "failed" && "enabled" in body) {
        return HttpResponse.json({ detail: "A failed source can't be ticked." }, { status: 409 });
      }
      state.sources = state.sources.map((s) => (s.id === params.id ? { ...s, ...body } : s));
      return HttpResponse.json(state.sources.find((s) => s.id === params.id));
    }),
    http.post("*/api/spaces/:sid/sources/:id/refresh", ({ params }) => {
      state.calls.refresh.push(String(params.id));
      state.sources = state.sources.map((s) =>
        s.id === params.id ? { ...s, status: "queued" } : s,
      );
      return HttpResponse.json(state.sources.find((s) => s.id === params.id));
    }),
    http.delete("*/api/spaces/:sid/sources/:id", ({ params }) => {
      state.calls.deleted.push(String(params.id));
      state.sources = state.sources.filter((s) => s.id !== params.id);
      return new HttpResponse(null, { status: 204 });
    }),
    http.get("*/api/spaces/:sid/sources/:id/snapshot", ({ params }) => {
      const snap = opts.snapshots?.[String(params.id)];
      return snap
        ? HttpResponse.json(snap)
        : HttpResponse.json({ detail: "No snapshot." }, { status: 404 });
    }),
    http.get("*/api/spaces/:sid/sources/:id/secrets", ({ params }) => {
      state.calls.secrets.push(String(params.id));
      const report = opts.secrets?.[String(params.id)];
      return report
        ? HttpResponse.json(report)
        : HttpResponse.json({ detail: "No secrets report." }, { status: 404 });
    }),
    http.get("*/api/spaces/:sid/chunks/:id/context", ({ params }) => {
      state.calls.contexts.push(String(params.id));
      const c = opts.contexts?.[String(params.id)];
      return c
        ? HttpResponse.json(c)
        : HttpResponse.json({ detail: "Passage not found." }, { status: 404 });
    }),
    http.get("*/api/spaces/:sid/chunks/:id", ({ params }) => {
      const c = opts.chunks?.[String(params.id)];
      return c
        ? HttpResponse.json(c)
        : HttpResponse.json({ detail: "Passage not found." }, { status: 404 });
    }),
    http.post("*/api/spaces/:sid/sources/estimate", async ({ request }) => {
      const body = (await request.json()) as { items: SourceEstimateItem[] };
      state.calls.estimate.push(body.items);
      return HttpResponse.json(
        opts.estimate?.(body.items) ?? {
          seconds: 5,
          files: null,
          summary: "Less than a minute on this PC",
          confirm: false,
        },
      );
    }),
    http.get("*/api/spaces/:sid/gate-log", () => HttpResponse.json(state.gateLog)),
    http.post("*/api/spaces/:sid/gate-log/:id/correct", async ({ params, request }) => {
      const id = Number(params.id);
      const body = (await request.json()) as { decision: string };
      state.calls.gateCorrect.push({ id, body });
      state.gateLog = state.gateLog.map((e) =>
        e.id === id ? { ...e, user_correction: body.decision } : e,
      );
      return HttpResponse.json(state.gateLog.find((e) => e.id === id) ?? {});
    }),
    http.get("*/api/browse", ({ request }) => {
      const path = new URL(request.url).searchParams.get("path") ?? "";
      state.calls.browse.push(path);
      const listing = opts.browse?.[path];
      return listing
        ? HttpResponse.json(listing)
        : HttpResponse.json({ detail: "Folder not found." }, { status: 404 });
    }),

    // Chat
    http.get("*/api/spaces/:sid/chat/status", () => {
      const ready = state.sources.filter((s) => s.status === "ready");
      const computed: ChatStatus = {
        enabled_sources: ready.filter((s) => s.enabled).length,
        ready_sources: ready.length,
        total_sources: state.sources.length,
        chat_ready: chatReady,
      };
      return HttpResponse.json({ ...computed, ...opts.chatStatus });
    }),
    http.get("*/api/spaces/:sid/conversations", () => HttpResponse.json(state.conversations)),
    http.post("*/api/spaces/:sid/conversations", async ({ request }) => {
      const body = (await request.json()) as { title?: string };
      state.calls.conversationsCreated.push(body);
      const c = conversationFixture({ id: `cv_new${nextId++}`, title: body.title ?? "" });
      state.conversations = [c, ...state.conversations];
      state.messages[c.id] = [];
      return HttpResponse.json(c, { status: 201 });
    }),
    http.delete("*/api/spaces/:sid/conversations/:cid", ({ params }) => {
      state.calls.conversationsDeleted.push(String(params.cid));
      state.conversations = state.conversations.filter((c) => c.id !== params.cid);
      return new HttpResponse(null, { status: 204 });
    }),
    http.get("*/api/spaces/:sid/conversations/:cid/messages", ({ params }) =>
      HttpResponse.json(state.messages[String(params.cid)] ?? []),
    ),
    http.post("*/api/spaces/:sid/conversations/:cid/messages", async ({ params, request }) => {
      const body = (await request.json()) as { content: string };
      const args = { conversationId: String(params.cid), content: body.content };
      state.calls.asked.push(args);
      if (!opts.stream) return HttpResponse.json({ detail: "No stream." }, { status: 500 });
      return opts.stream(args);
    }),

    // Overview
    http.get("*/api/spaces/:sid/overview", () => HttpResponse.json(state.overview)),
    http.put("*/api/spaces/:sid/overview", async ({ request }) => {
      const body = (await request.json()) as { enabled: boolean };
      state.calls.overviewPut.push(body);
      state.overview = { ...state.overview, enabled: body.enabled };
      return HttpResponse.json(state.overview);
    }),
    http.post("*/api/spaces/:sid/overview/refresh", () => {
      state.calls.overviewRefresh++;
      state.overview = { ...state.overview, status: "pending" };
      return HttpResponse.json({ id: "job_ov", status: "queued" }, { status: 202 });
    }),
  );

  /** Stores the exchange as the real backend would, so a refetch after `done` sees it. */
  const persist = (conversationId: string, messages: Message[]) => {
    state.messages[conversationId] = [...(state.messages[conversationId] ?? []), ...messages];
  };

  return { state, persist };
}
