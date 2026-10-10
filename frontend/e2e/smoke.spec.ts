import { expect, test, type APIRequestContext } from "@playwright/test";

/**
 * SPEC §13 smoke test: create space → paste text source → wait for ready → ask → cited answer.
 * Needs a backend started with TRUSTBOOK_MOCK_LLM=1 (bundled models are real; the chat model is
 * the deterministic mock).
 */

const SPACE_NAME = `E2E smoke ${Date.now()}`;
const SOURCE_TITLE = "Gull Island lighthouse";
const SOURCE_TEXT = [
  "The lighthouse on Gull Island was painted red in 1952 by the harbour master, Anna Berg.",
  "It stands 31 metres tall and its lamp can be seen from 20 nautical miles away.",
  "The keeper's cottage next to it became a small museum in 1998.",
].join("\n\n");
const QUESTION = "When was the lighthouse on Gull Island painted red?";

let spaceId: string | null = null;

async function ok(res: Awaited<ReturnType<APIRequestContext["get"]>>, what: string) {
  expect(res.ok(), `${what}: HTTP ${res.status()} ${await res.text()}`).toBeTruthy();
}

test.beforeAll(async ({ request }) => {
  // A fresh install calibrates first and holds the models meanwhile (DECISIONS D-030).
  test.setTimeout(600_000);
  await expect
    .poll(async () => (await (await request.get("/api/calibration")).json()).status, {
      timeout: 540_000,
      intervals: [3_000],
    })
    .toBe("done");

  // Use the deterministic mock chat model (only offered when TRUSTBOOK_MOCK_LLM=1).
  await ok(
    await request.put("/api/capabilities/chat", { data: { preset: "mock" } }),
    "chat preset",
  );
  await ok(await request.post("/api/setup/complete"), "complete setup");
});

test.afterAll(async ({ request }) => {
  if (spaceId) await request.delete(`/api/spaces/${encodeURIComponent(spaceId)}`);
});

test("paste a source, ask a question, get a cited answer", async ({ page }) => {
  await page.goto("/");

  // Create a space.
  await page
    .getByRole("button", { name: /new space/i })
    .first()
    .click();
  const createDialog = page.getByRole("dialog", { name: "New space" });
  await createDialog.getByLabel("Name").fill(SPACE_NAME);
  await createDialog.getByRole("button", { name: "Create space" }).click();
  await expect(page).toHaveURL(/\/spaces\/[^/]+$/);
  spaceId = decodeURIComponent(new URL(page.url()).pathname.split("/").pop() ?? "");
  await expect(page.getByRole("heading", { name: SPACE_NAME })).toBeVisible();

  // Paste a text source.
  await page.getByRole("button", { name: "Add source" }).click();
  await page.getByRole("menuitem", { name: /paste text/i }).click();
  const pasteDialog = page.getByRole("dialog", { name: "Paste text" });
  await pasteDialog.getByLabel(/title/i).fill(SOURCE_TITLE);
  await pasteDialog.getByLabel("Text").fill(SOURCE_TEXT);
  await pasteDialog.getByRole("button", { name: "Add text" }).click();
  await expect(pasteDialog).toBeHidden();

  // Wait until it is read and indexed (bundled embedding model on CPU).
  const row = page.locator("li[data-source-status]").filter({ hasText: SOURCE_TITLE });
  await expect(row).toHaveAttribute("data-source-status", "ready", { timeout: 120_000 });
  await expect(page.getByText("Using 1 of 1 source")).toBeVisible();

  // Ask.
  const question = page.getByRole("textbox", { name: "Question" });
  await expect(question).toBeEnabled();
  await question.fill(QUESTION);
  await page.getByRole("button", { name: "Ask" }).click();

  // A stored answer with at least one numbered citation from the pasted source.
  const answer = page.getByTestId("assistant-message").last();
  await expect(answer).toBeVisible({ timeout: 60_000 });
  await expect(answer.getByTestId("refusal-card")).toHaveCount(0);
  const citation = answer.getByRole("button", { name: new RegExp(`^Citation 1: ${SOURCE_TITLE}`) });
  await expect(citation.first()).toBeVisible();

  // The citation opens the source viewer at the highlighted passage.
  await citation.first().click();
  const viewer = page.getByRole("dialog", { name: SOURCE_TITLE });
  await expect(viewer).toBeVisible();
  await expect(viewer.locator("mark")).toContainText("Gull Island");
});
