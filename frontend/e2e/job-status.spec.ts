import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import {
  BROWSE_ROOT,
  capabilityConfig,
  deleteSpace,
  expectProcessingIdle,
  expectQueueShows,
  expectSourceMatchesJob,
  expectSpaceConsistent,
  type HostFolder,
  linkFolder,
  listJobs,
  listSources,
  NO_BROWSE_ROOT,
  ok,
  openAddSource,
  openSpace,
  prepareBackend,
  putCapability,
  removeHostFolder,
  settleSourceAndJob,
  waitForNewSource,
  writeHostFolder,
} from "./helpers";

/**
 * Gate 3 item B: for every source type the ingest JOB ends in the state its source shows, in the
 * API (GET /api/jobs) and on the Queue page. At Gate 3 a bare `raise` failed every successful job
 * while every source looked ready; checking only the source status missed it.
 *
 * Network: the LAN address and the mock search results never leave the machine (the mock results
 * use example.com sub-domains that do not resolve). The public-page test probes the internet
 * first and skips when the runner is offline; YouTube accepts Done or Failed (captions may be
 * blocked from CI) as long as source and job agree.
 */

const NOTE_TEXT = [
  "The ferry to Gull Island leaves the north pier at nine in the morning.",
  "Tickets are sold at the harbour office, which opens at eight.",
].join("\n\n");

let spaceId: string | null = null;
let folder: HostFolder | null = null;

test.beforeAll(async ({ request }) => {
  test.setTimeout(600_000);
  await prepareBackend(request);
});

test.afterEach(async ({ request }) => {
  // Every source left in the space must agree with its job, whatever the test asserted.
  try {
    if (spaceId) await expectSpaceConsistent(request, spaceId);
  } finally {
    await deleteSpace(request, spaceId);
    spaceId = null;
    removeHostFolder(folder);
    folder = null;
  }
});

async function createNamedSpace(
  request: APIRequestContext,
  label: string,
): Promise<{ id: string; name: string }> {
  const name = `${label} ${Date.now()}`;
  const res = await request.post("/api/spaces", { data: { name } });
  await ok(res, "create space");
  return { id: ((await res.json()) as { id: string }).id, name };
}

async function addUrlInUi(page: Page, url: string): Promise<void> {
  await openAddSource(page, /^Web page/);
  const dialog = page.getByRole("dialog", { name: "Add a web page" });
  await dialog.getByLabel("Web address").fill(url);
  await dialog.getByRole("button", { name: "Add page" }).click();
  await expect(dialog).toBeHidden();
}

async function internetReachable(url: string): Promise<boolean> {
  try {
    const res = await fetch(url, { method: "HEAD", signal: AbortSignal.timeout(8_000) });
    return res.status < 500;
  } catch {
    return false;
  }
}

test("a successful add: job Done, header back to idle, no failed jobs in the Queue", async ({
  page,
  request,
}) => {
  const space = await createNamedSpace(request, "E2E job ok");
  spaceId = space.id;
  await openSpace(page, space.id);

  await openAddSource(page, /paste text/i);
  const dialog = page.getByRole("dialog", { name: "Paste text" });
  await dialog.getByLabel(/title/i).fill("Ferry times");
  await dialog.getByLabel("Text").fill(NOTE_TEXT);
  await dialog.getByRole("button", { name: "Add text" }).click();
  await expect(dialog).toBeHidden();

  const sourceId = await waitForNewSource(request, space.id);
  await expect(page.getByTestId(`source-${sourceId}`)).toHaveAttribute(
    "data-source-status",
    "ready",
    { timeout: 120_000 },
  );
  const { source, job } = await settleSourceAndJob(request, space.id, sourceId);
  expect(source.status).toBe("ready");
  expectSourceMatchesJob(source, job);

  // The header returns to idle: nothing is left running or waiting (this includes the space
  // overview job that a ready source starts).
  await expectProcessingIdle(page);

  // Every job of this space (the ingest job and the overview job) finished as Done.
  const spaceJobs = (await listJobs(request)).filter((j) => j.space_id === space.id);
  expect(spaceJobs.filter((j) => j.kind === "ingest").map((j) => j.id)).toEqual([job.id]);
  for (const j of spaceJobs) expect(j.status, `${j.kind} job: ${j.error ?? ""}`).toBe("done");

  // The Queue shows the ingest job as Done, and no failed or unfinished job for this space.
  await expectQueueShows(page, job);
  const rows = page
    .getByRole("list", { name: "Finished jobs" })
    .getByRole("listitem")
    .filter({ hasText: space.name });
  await expect(rows).toHaveCount(spaceJobs.length);
  await expect(rows.filter({ has: page.getByText("Done", { exact: true }) })).toHaveCount(
    spaceJobs.length,
  );
  await expect(rows.filter({ has: page.getByText("Failed", { exact: true }) })).toHaveCount(0);
  await expect(page.getByRole("list", { name: "Unfinished jobs" })).toHaveCount(0);
  await expectProcessingIdle(page);
});

test("upload: a text file's job is Done", async ({ page, request }) => {
  const space = await createNamedSpace(request, "E2E job upload");
  spaceId = space.id;
  await openSpace(page, space.id);

  const fileName = "harbour-notes.txt";
  await openAddSource(page, /upload files/i);
  const dialog = page.getByRole("dialog", { name: "Upload files" });
  await dialog.getByLabel("Choose files").setInputFiles({
    name: fileName,
    mimeType: "text/plain",
    buffer: Buffer.from(NOTE_TEXT, "utf-8"),
  });
  await expect(dialog.getByRole("list", { name: "Files to upload" })).toContainText(fileName);
  await dialog.getByRole("button", { name: "Add 1 file" }).click();
  await expect(dialog).toBeHidden();

  const sourceId = await waitForNewSource(request, space.id);
  const { source, job } = await settleSourceAndJob(request, space.id, sourceId);
  expect(source.type).toBe("upload");
  expect(source.status).toBe("ready");
  expectSourceMatchesJob(source, job);
  await expectQueueShows(page, job);
});

test("folder: a linked folder's job is Done", async ({ page, request }) => {
  test.skip(!BROWSE_ROOT, NO_BROWSE_ROOT);
  const space = await createNamedSpace(request, "E2E job folder");
  spaceId = space.id;
  folder = writeHostFolder("e2e-jobs", {
    "README.md": "# Ferry tools\n\nHelpers that print the ferry timetable.\n",
    "ferry.py": [
      '"""Ferry timetable helpers."""',
      "",
      "DEPARTURE_HOUR = 9",
      "",
      "",
      "def departure() -> str:",
      '    """The first departure from the north pier."""',
      '    return f"{DEPARTURE_HOUR:02d}:00"',
      "",
    ].join("\n"),
  });
  const linked = await linkFolder(request, space.id, folder.rel);

  const { source, job } = await settleSourceAndJob(request, space.id, linked.id);
  expect(source.type).toBe("folder");
  expect(source.status).toBe("ready");
  expectSourceMatchesJob(source, job);
  await expectQueueShows(page, job);
});

test("URL: a LAN address fails, and so does its job, with the same reason", async ({
  page,
  request,
}) => {
  const space = await createNamedSpace(request, "E2E job lan");
  spaceId = space.id;
  await openSpace(page, space.id);
  await addUrlInUi(page, "http://192.168.1.1/");

  const sourceId = await waitForNewSource(request, space.id);
  const { source, job } = await settleSourceAndJob(request, space.id, sourceId, 60_000);
  expect(source.status).toBe("failed");
  expect(source.failure_reason).toContain("This address is on your local network");
  expectSourceMatchesJob(source, job);
  await expectQueueShows(page, job);
});

test("URL: a public web page's job is Done (needs internet)", async ({ page, request }) => {
  const url = "https://example.com/";
  test.skip(!(await internetReachable(url)), `${url} is not reachable from this runner`);
  const space = await createNamedSpace(request, "E2E job web");
  spaceId = space.id;
  await openSpace(page, space.id);
  await addUrlInUi(page, url);

  const sourceId = await waitForNewSource(request, space.id);
  const { source, job } = await settleSourceAndJob(request, space.id, sourceId);
  expect(source.type).toBe("url");
  expect(source.status, source.failure_reason ?? "").toBe("ready");
  expectSourceMatchesJob(source, job);
  await expectQueueShows(page, job);
});

test("YouTube: Done or Failed with a reason, never a mismatch with the source", async ({
  page,
  request,
}) => {
  test.setTimeout(420_000);
  const space = await createNamedSpace(request, "E2E job youtube");
  spaceId = space.id;
  await openSpace(page, space.id);
  // "Me at the zoo": 19 seconds, with captions, so even a speech-to-text fallback is short.
  await addUrlInUi(page, "https://www.youtube.com/watch?v=jNQXAC9IVRw");

  const sourceId = await waitForNewSource(request, space.id);
  const { source, job } = await settleSourceAndJob(request, space.id, sourceId, 360_000);
  expect(source.type).toBe("youtube");
  const outcome = `${job.status}${job.error ? `: ${job.error}` : ""}`;
  test.info().annotations.push({ type: "youtube", description: outcome });
  console.log(`YouTube job outcome: ${outcome}`);
  expectSourceMatchesJob(source, job);
  await expectQueueShows(page, job);
});

test("web search (mock provider): unreachable results fail, and so do their jobs", async ({
  page,
  request,
}) => {
  const before = await capabilityConfig(request, "web_search");
  try {
    await putCapability(request, "web_search", { provider: "mock" });
    await ok(await request.post("/api/capabilities/web_search/test", { data: {} }), "test search");

    const space = await createNamedSpace(request, "E2E job search");
    spaceId = space.id;
    await openSpace(page, space.id);
    await openAddSource(page, /^Search the web/);
    const dialog = page.getByRole("dialog", { name: "Search the web" });
    await dialog.getByRole("textbox", { name: "Search for" }).fill("microsoft ab 731 certificate");
    await dialog.getByRole("button", { name: "Search" }).click();
    const results = dialog.getByRole("list", { name: "Search results" });
    await expect(results).toBeVisible();
    // The two specific mock results: example.com sub-domains that do not resolve.
    for (const url of [
      "https://docs.example.com/certificates/ab-731",
      "https://learn.example.com/exams/ab-731",
    ]) {
      await results.locator(`li[data-url="${url}"]`).getByRole("checkbox").check();
    }
    await dialog.getByRole("button", { name: "Add 2 sources" }).click();
    await expect(dialog).toBeHidden();

    await expect
      .poll(async () => (await listSources(request, space.id)).length, { timeout: 30_000 })
      .toBe(2);
    for (const added of await listSources(request, space.id)) {
      const { source, job } = await settleSourceAndJob(request, space.id, added.id, 120_000);
      expect(source.type).toBe("search");
      expect(source.status).toBe("failed");
      expect(source.failure_reason).toBe("Trustbook could not reach this site.");
      expectSourceMatchesJob(source, job);
      await expectQueueShows(page, job);
    }
  } finally {
    await putCapability(request, "web_search", { provider: before.provider ?? "searxng" });
  }
});
