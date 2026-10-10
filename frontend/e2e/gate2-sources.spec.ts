import { expect, test } from "@playwright/test";
import {
  addHostFiles,
  BROWSE_ROOT,
  capabilityConfig,
  createSpace,
  deleteSpace,
  getJson,
  type HostFolder,
  NO_BROWSE_ROOT,
  ok,
  openAddSource,
  openSpace,
  pasteSource,
  prepareBackend,
  putCapability,
  removeHostFolder,
  waitForReady,
  writeHostFolder,
} from "./helpers";

/**
 * Gate 2 UI checks #8 (failed sources), #10 (web search ranking) and #11 (estimates before big
 * adds). Web search uses the "mock" provider (only with TRUSTBOOK_MOCK_LLM=1): fixed results that
 * include bare homepages, so no search engine is contacted.
 */

const NOTE_TITLE = "Pier opening hours";
const NOTE_TEXT =
  "The north pier is open to visitors from eight in the morning to six in the evening.";

let spaceId: string | null = null;
let folder: HostFolder | null = null;

test.beforeAll(async ({ request }) => {
  test.setTimeout(600_000);
  await prepareBackend(request);
});

test.afterEach(async ({ request }) => {
  await deleteSpace(request, spaceId);
  spaceId = null;
  removeHostFolder(folder);
  folder = null;
});

interface SourceLite {
  id: string;
  title: string;
  status: string;
  enabled: boolean;
  origin: string | null;
  failure_reason: string | null;
}

test("Gate 2 #8: a LAN address fails with the local-network reason, unticked and not counted", async ({
  page,
  request,
}) => {
  spaceId = await createSpace(request, "E2E g2 failed");
  const note = await pasteSource(request, spaceId, NOTE_TITLE, NOTE_TEXT);
  await waitForReady(request, spaceId, [note]);
  await openSpace(page, spaceId);
  await expect(page.getByText("Using 1 of 1 source")).toBeVisible();

  await openAddSource(page, /^Web page/);
  const dialog = page.getByRole("dialog", { name: "Add a web page" });
  await dialog.getByLabel("Web address").fill("http://192.168.1.1/");
  await dialog.getByRole("button", { name: "Add page" }).click();
  await expect(dialog).toBeHidden();

  // The new source fails with the plain-English local-network reason.
  const sourcesUrl = `/api/spaces/${encodeURIComponent(spaceId)}/sources`;
  let failed: SourceLite | undefined;
  await expect
    .poll(
      async () => {
        const list = await getJson<SourceLite[]>(request, sourcesUrl);
        failed = list.find((s) => s.id !== note);
        return failed?.status;
      },
      { timeout: 60_000 },
    )
    .toBe("failed");
  if (!failed) return;
  const row = page.getByTestId(`source-${failed.id}`);
  await expect(row).toHaveAttribute("data-source-status", "failed");
  await expect(row).toContainText("This address is on your local network");

  // Unticked, can't be ticked, and left out of "Using X of Y".
  const box = row.getByRole("checkbox");
  await expect(box).not.toBeChecked();
  await expect(box).toBeDisabled();
  await expect(page.getByText("Using 1 of 1 source")).toBeVisible();
  const status = await getJson<{ enabled_sources: number; ready_sources: number }>(
    request,
    `/api/spaces/${encodeURIComponent(spaceId)}/chat/status`,
  );
  expect(status).toMatchObject({ enabled_sources: 1, ready_sources: 1 });
  expect(failed.enabled).toBe(false);
});

const SPECIFIC = [
  "https://docs.example.com/certificates/ab-731",
  "https://learn.example.com/exams/ab-731",
];
const HOMEPAGES = ["https://www.example.com/sv-se/", "https://example.org/"];

test("Gate 2 #10: web search lists the specific pages above bare homepages", async ({
  page,
  request,
}) => {
  const before = await capabilityConfig(request, "web_search");
  try {
    await putCapability(request, "web_search", { provider: "mock" });
    // Store a passing test result so the card is Ready (the mock never touches the network).
    await ok(await request.post("/api/capabilities/web_search/test", { data: {} }), "test search");

    spaceId = await createSpace(request, "E2E g2 web search");
    await openSpace(page, spaceId);
    await openAddSource(page, /^Search the web/);
    const dialog = page.getByRole("dialog", { name: "Search the web" });
    await dialog.getByRole("textbox", { name: "Search for" }).fill("microsoft ab 731 certificate");
    await dialog.getByRole("button", { name: "Search" }).click();

    const results = dialog.getByRole("list", { name: "Search results" });
    await expect(results).toBeVisible();
    const urls = await results
      .locator("li[data-url]")
      .evaluateAll((els) => els.map((el) => el.getAttribute("data-url") ?? ""));
    for (const url of [...SPECIFIC, ...HOMEPAGES]) expect(urls).toContain(url);
    const lastSpecific = Math.max(...SPECIFIC.map((u) => urls.indexOf(u)));
    const firstHomepage = Math.min(...HOMEPAGES.map((u) => urls.indexOf(u)));
    expect(lastSpecific).toBeLessThan(firstHomepage);
    // In particular no Swedish homepage on top.
    expect(urls[0]).not.toContain("/sv-se");

    // Nothing is added (adding would fetch the pages).
    await dialog.press("Escape");
    await expect(dialog).toBeHidden();
    expect(
      await getJson<unknown[]>(request, `/api/spaces/${encodeURIComponent(spaceId)}/sources`),
    ).toEqual([]);
  } finally {
    await putCapability(request, "web_search", { provider: before.provider ?? "searxng" });
  }
});

interface Estimate {
  seconds: number;
  files: number | null;
  summary: string;
  confirm: boolean;
}

/** `count` small Python files in 100-file packages (keeps every folder listing short). */
function bigTree(count: number, offset = 0): Record<string, string> {
  const files: Record<string, string> = {};
  for (let i = offset; i < offset + count; i++) {
    const pkg = `pkg_${String(Math.floor(i / 100)).padStart(3, "0")}`;
    files[`${pkg}/mod_${String(i).padStart(5, "0")}.py`] = [
      `"""Module ${i} of the estimate test tree."""`,
      "",
      `LIMIT_${i} = ${i % 97}`,
      "",
      "",
      `def compute_${i}(values):`,
      `    """Sum the values below LIMIT_${i} and scale them."""`,
      `    total = sum(v for v in values if v < LIMIT_${i})`,
      `    return total * ${(i % 7) + 2}`,
      "",
      "",
      `class Worker${i}:`,
      `    """Runs compute_${i} on batches."""`,
      "",
      "    def __init__(self, batch):",
      "        self.batch = list(batch)",
      "",
      "    def run(self):",
      `        return compute_${i}(self.batch)`,
      "",
    ].join("\n");
  }
  return files;
}

test("Gate 2 #11: a big folder shows its estimate and asks first; Cancel adds nothing", async ({
  page,
  request,
}) => {
  test.skip(!BROWSE_ROOT, NO_BROWSE_ROOT);
  test.setTimeout(300_000);
  spaceId = await createSpace(request, "E2E g2 estimate");
  const estimateUrl = `/api/spaces/${encodeURIComponent(spaceId)}/sources/estimate`;

  // Grow the folder until this runner's estimate is above two minutes (confirm: true).
  let count = 4_000;
  folder = writeHostFolder("e2e-big", bigTree(count));
  const estimateFor = async (): Promise<Estimate> => {
    const res = await request.post(estimateUrl, {
      data: { items: [{ kind: "folder", path: folder?.rel ?? "" }] },
    });
    await ok(res, "estimate");
    return (await res.json()) as Estimate;
  };
  let estimate = await estimateFor();
  while (!estimate.confirm && count < 32_000) {
    addHostFiles(folder, bigTree(count, count));
    count *= 2;
    estimate = await estimateFor();
  }
  expect(estimate.confirm, `estimate for ${count} files: ${estimate.summary}`).toBe(true);
  expect(estimate.summary).toMatch(/^About [\d,]+ files, roughly .+ on this PC/);

  // Watch for any attempt to add the folder.
  const adds: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && /\/sources\/folder$/.test(r.url())) adds.push(r.url());
  });

  await openSpace(page, spaceId);
  await openAddSource(page, /^Link folder/);
  const picker = page.getByRole("dialog", { name: "Link a folder" });
  await picker
    .getByRole("list", { name: "Folder contents" })
    .getByRole("button", { name: folder.rel, exact: true })
    .click();
  await expect(picker.getByText(`Selected: ${folder.rel}`)).toBeVisible();

  // The footer shows the summary before anything is added.
  const footer = picker.getByTestId("folder-estimate");
  await expect(footer).toHaveText(estimate.summary, { timeout: 60_000 });

  // Link → the confirm dialog with the same summary ("roughly N min").
  await picker.getByRole("button", { name: "Link this folder" }).click();
  const confirm = page.getByRole("alertdialog", { name: "This will take a while" });
  await expect(confirm).toBeVisible();
  await expect(confirm.getByTestId("estimate-summary")).toHaveText(estimate.summary);
  await expect(confirm.getByTestId("estimate-summary")).toContainText(/roughly \d+/);
  await expect(confirm.getByRole("button", { name: "Add" })).toBeVisible();

  // Cancel → nothing is added.
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await expect(confirm).toBeHidden();
  await expect(picker).toBeVisible();
  await picker.press("Escape");
  await expect(picker).toBeHidden();
  expect(adds).toEqual([]);
  expect(
    await getJson<unknown[]>(request, `/api/spaces/${encodeURIComponent(spaceId)}/sources`),
  ).toEqual([]);
  await expect(page.locator("li[data-source-status]")).toHaveCount(0);
});
