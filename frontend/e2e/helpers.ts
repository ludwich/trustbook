import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { expect, type APIRequestContext, type APIResponse, type Page } from "@playwright/test";

/**
 * Shared setup for the end-to-end specs. Every spec runs against a real Trustbook started with
 * TRUSTBOOK_MOCK_LLM=1 and uses the deterministic "mock" chat preset; nothing here calls a real
 * chat endpoint. Each test creates its own space and deletes it afterwards.
 */

export async function ok(res: APIResponse, what: string): Promise<void> {
  expect(res.ok(), `${what}: HTTP ${res.status()} ${await res.text()}`).toBeTruthy();
}

/** Waits for calibration (a fresh install holds the models meanwhile) and selects the mock model. */
export async function prepareBackend(request: APIRequestContext): Promise<void> {
  await expect
    .poll(async () => (await (await request.get("/api/calibration")).json()).status, {
      timeout: 540_000,
      intervals: [3_000],
    })
    .toBe("done");
  await ok(
    await request.put("/api/capabilities/chat", { data: { preset: "mock" } }),
    "chat preset",
  );
  await ok(await request.post("/api/setup/complete"), "complete setup");
}

export async function createSpace(request: APIRequestContext, name: string): Promise<string> {
  const res = await request.post("/api/spaces", { data: { name: `${name} ${Date.now()}` } });
  await ok(res, "create space");
  return ((await res.json()) as { id: string }).id;
}

export async function deleteSpace(request: APIRequestContext, id: string | null): Promise<void> {
  if (id) await request.delete(`/api/spaces/${encodeURIComponent(id)}`);
}

export async function pasteSource(
  request: APIRequestContext,
  spaceId: string,
  title: string,
  text: string,
): Promise<string> {
  const res = await request.post(`/api/spaces/${encodeURIComponent(spaceId)}/sources/paste`, {
    data: { title, text },
  });
  await ok(res, `paste ${title}`);
  return ((await res.json()) as { id: string }).id;
}

interface SourceStatus {
  id: string;
  status: string;
  failure_reason: string | null;
}

/** Polls the API until every listed source is ready (fails fast if one fails). */
export async function waitForReady(
  request: APIRequestContext,
  spaceId: string,
  sourceIds: string[],
  timeout = 180_000,
): Promise<void> {
  await expect
    .poll(
      async () => {
        const res = await request.get(`/api/spaces/${encodeURIComponent(spaceId)}/sources`);
        const sources = (await res.json()) as SourceStatus[];
        const wanted = sources.filter((s) => sourceIds.includes(s.id));
        const failed = wanted.find((s) => s.status === "failed");
        if (failed) return `failed: ${failed.failure_reason ?? "?"}`;
        return wanted.length === sourceIds.length && wanted.every((s) => s.status === "ready")
          ? "ready"
          : "busy";
      },
      { timeout, intervals: [1_000, 2_000] },
    )
    .toBe("ready");
}

export async function openSpace(page: Page, spaceId: string): Promise<void> {
  await page.goto(`/spaces/${encodeURIComponent(spaceId)}`);
  await expect(page.getByRole("complementary", { name: "Sources" })).toBeVisible();
}

/**
 * Asks a question in the chat box and returns the stored answer (the n-th assistant message).
 * `answersBefore` is how many stored answers the conversation already shows.
 */
export async function ask(page: Page, question: string, answersBefore: number) {
  const box = page.getByRole("textbox", { name: "Question" });
  await expect(box).toBeEnabled();
  await box.fill(question);
  await page.getByRole("button", { name: "Ask" }).click();
  const answers = page.getByTestId("assistant-message");
  await expect(answers).toHaveCount(answersBefore + 1, { timeout: 90_000 });
  return answers.last();
}

/** Citation buttons in an answer that point at the given source title. */
export function citationsTo(answer: ReturnType<Page["locator"]>, sourceTitle: string) {
  return answer.getByRole("button", {
    name: new RegExp(`^Citation \\d+: ${escapeRegExp(sourceTitle)}`),
  });
}

export function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// ---- Phase 3: shared helpers for the Gate 2 UI checks ---------------------------------------

export async function getJson<T>(request: APIRequestContext, url: string): Promise<T> {
  const res = await request.get(url);
  await ok(res, `GET ${url}`);
  return (await res.json()) as T;
}

export interface CapabilityConfigLike {
  enabled: boolean;
  provider: string | null;
  base_url: string | null;
  code_judge?: boolean | null;
}

export async function capabilityConfig(
  request: APIRequestContext,
  id: string,
): Promise<CapabilityConfigLike> {
  return (await getJson<{ config: CapabilityConfigLike }>(request, `/api/capabilities/${id}`))
    .config;
}

export async function putCapability(
  request: APIRequestContext,
  id: string,
  data: Record<string, unknown>,
): Promise<void> {
  await ok(await request.put(`/api/capabilities/${id}`, { data }), `update ${id}`);
}

/**
 * The host folder the container sees as its browse root (/host). The release workflow exports it;
 * specs that need files on disk skip without it. Playwright runs on the host, so it can write
 * there before linking a folder.
 */
export const BROWSE_ROOT = process.env.E2E_BROWSE_ROOT ?? "";
export const NO_BROWSE_ROOT = "E2E_BROWSE_ROOT is not set (host folder mounted at /host)";

export interface HostFolder {
  /** Path relative to the browse root, as the API and the folder picker use it. */
  rel: string;
  /** Absolute path on the host. */
  abs: string;
}

/** Writes `files` (relative path → content) into a new, uniquely named folder under the root. */
export function writeHostFolder(prefix: string, files: Record<string, string>): HostFolder {
  const rel = `${prefix}-${Date.now()}`;
  const folder = { rel, abs: path.join(BROWSE_ROOT, rel) };
  addHostFiles(folder, files);
  return folder;
}

/** Adds (or overwrites) files in a folder made by `writeHostFolder`. */
export function addHostFiles(folder: HostFolder, files: Record<string, string>): void {
  for (const [name, content] of Object.entries(files)) {
    const file = path.join(folder.abs, name);
    mkdirSync(path.dirname(file), { recursive: true });
    writeFileSync(file, content);
  }
}

export function removeHostFolder(folder: HostFolder | null): void {
  if (folder) rmSync(folder.abs, { recursive: true, force: true });
}

export async function linkFolder(
  request: APIRequestContext,
  spaceId: string,
  rel: string,
): Promise<{ id: string; title: string }> {
  const res = await request.post(`/api/spaces/${encodeURIComponent(spaceId)}/sources/folder`, {
    data: { path: rel },
  });
  await ok(res, `link folder ${rel}`);
  return (await res.json()) as { id: string; title: string };
}

/** Opens the "Add source" menu and picks an entry, e.g. "Web page" or "Link folder". */
export async function openAddSource(page: Page, entry: RegExp): Promise<void> {
  await page.getByRole("button", { name: "Add source" }).click();
  await page.getByRole("menuitem", { name: entry }).click();
}

// ---- Gate 3 item B: the ingest JOB status must match the source status ---------------------

export interface SourceState {
  id: string;
  type: string;
  title: string;
  status: string;
  failure_reason: string | null;
  job_id: string | null;
}

export interface JobState {
  id: string;
  kind: string;
  title: string;
  space_id: string | null;
  status: string;
  error: string | null;
}

const SOURCE_FINAL = ["ready", "failed"];
const JOB_FINAL = ["done", "failed", "cancelled"];

export async function listSources(
  request: APIRequestContext,
  spaceId: string,
): Promise<SourceState[]> {
  return getJson<SourceState[]>(request, `/api/spaces/${encodeURIComponent(spaceId)}/sources`);
}

export async function listJobs(request: APIRequestContext): Promise<JobState[]> {
  return (await getJson<{ jobs: JobState[] }>(request, "/api/jobs")).jobs;
}

/** Waits until a source that is not in `known` appears in the space and returns its id. */
export async function waitForNewSource(
  request: APIRequestContext,
  spaceId: string,
  known: string[] = [],
): Promise<string> {
  let id = "";
  await expect
    .poll(
      async () => {
        const fresh = (await listSources(request, spaceId)).find((s) => !known.includes(s.id));
        id = fresh?.id ?? "";
        return id;
      },
      { timeout: 30_000 },
    )
    .not.toBe("");
  return id;
}

/**
 * Polls until both the source (ready/failed) and its ingest job (done/failed/cancelled) have
 * finished, then returns both. A source turns ready a moment before its job is closed, so
 * checking only the source is not enough: at Gate 3 every job failed after its source was ready.
 */
export async function settleSourceAndJob(
  request: APIRequestContext,
  spaceId: string,
  sourceId: string,
  timeout = 180_000,
): Promise<{ source: SourceState; job: JobState }> {
  let source: SourceState | undefined;
  let job: JobState | undefined;
  await expect
    .poll(
      async () => {
        source = (await listSources(request, spaceId)).find((s) => s.id === sourceId);
        if (!source) return "source missing";
        const jobId = source.job_id;
        if (!jobId) return "source has no job";
        job = (await listJobs(request)).find((j) => j.id === jobId);
        if (!job) return "job missing";
        return SOURCE_FINAL.includes(source.status) && JOB_FINAL.includes(job.status)
          ? "settled"
          : `busy (source ${source.status}, job ${job.status})`;
      },
      { timeout, intervals: [1_000, 2_000] },
    )
    .toBe("settled");
  if (!source || !job) throw new Error("settled without a source and a job");
  return { source, job };
}

/**
 * The invariant: a ready source has a done job without an error, and a failed source has a failed
 * job whose error is the source's failure reason. Never one without the other.
 */
export function expectSourceMatchesJob(source: SourceState, job: JobState): void {
  const what = `source "${source.title}" (${source.type}) is ${source.status}, its job is ${job.status} (${job.error ?? "no error"})`;
  expect(job.kind, what).toBe("ingest");
  if (source.status === "ready") {
    expect(job.status, what).toBe("done");
    expect(job.error, what).toBeNull();
  } else {
    expect(source.status, what).toBe("failed");
    expect(job.status, what).toBe("failed");
    expect(source.failure_reason, what).toBeTruthy();
    expect(job.error, what).toBe(source.failure_reason);
  }
}

/** Every source of the space has finished and matches its job. */
export async function expectSpaceConsistent(
  request: APIRequestContext,
  spaceId: string,
): Promise<void> {
  for (const s of await listSources(request, spaceId)) {
    const { source, job } = await settleSourceAndJob(request, spaceId, s.id);
    expectSourceMatchesJob(source, job);
  }
}

const QUEUE_LABEL: Record<string, string> = {
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

/** The Queue page lists the job as finished with its state badge and, if it failed, the reason. */
export async function expectQueueShows(page: Page, job: JobState): Promise<void> {
  await page.goto("/queue");
  const row = page.getByRole("list", { name: "Finished jobs" }).getByTestId(`job-${job.id}`);
  await expect(row).toBeVisible();
  const label = QUEUE_LABEL[job.status] ?? job.status;
  await expect(row.getByText(label, { exact: true })).toBeVisible();
  for (const other of Object.values(QUEUE_LABEL)) {
    if (other !== label) await expect(row.getByText(other, { exact: true })).toHaveCount(0);
  }
  if (job.error) await expect(row).toContainText(job.error);
  await expect(
    page.getByRole("list", { name: "Unfinished jobs" }).getByTestId(`job-${job.id}`),
  ).toHaveCount(0);
}

/** The header's processing indicator is empty: nothing waiting or running. */
export async function expectProcessingIdle(page: Page): Promise<void> {
  const indicator = page.getByTestId("processing-indicator");
  await expect(indicator).toBeAttached();
  // Busy queues are polled every 3 s; allow a few polls after the last job ends.
  await expect(indicator).toBeEmpty({ timeout: 30_000 });
}
