import { expect, test } from "@playwright/test";
import {
  ask,
  createSpace,
  deleteSpace,
  getJson,
  openSpace,
  pasteSource,
  prepareBackend,
  waitForReady,
} from "./helpers";

/**
 * Gate 2 UI check #12 (gate router and log): "hello" takes the chitchat path (short reply, no
 * citations); a brainstorming question gets the Ideation hint; both decisions appear in the
 * space's Gate log, and correcting one is stored.
 */

const TITLE = "Payment client notes";
const TEXT = [
  "The payment client retries a failed request three times before it gives up.",
  "Each retry waits twice as long as the one before, starting at half a second.",
].join("\n\n");
const BRAINSTORM = "Brainstorm ideas for a retry feature";

let spaceId: string | null = null;

test.beforeAll(async ({ request }) => {
  test.setTimeout(600_000);
  await prepareBackend(request);
});

test.afterEach(async ({ request }) => {
  await deleteSpace(request, spaceId);
  spaceId = null;
});

interface GateRow {
  id: number;
  input: string;
  decision: string;
  user_correction: string | null;
}

test("Gate 2 #12: chitchat and brainstorm decisions show in the Gate log and can be corrected", async ({
  page,
  request,
}) => {
  spaceId = await createSpace(request, "E2E g2 gate log");
  const id = await pasteSource(request, spaceId, TITLE, TEXT);
  await waitForReady(request, spaceId, [id]);
  await openSpace(page, spaceId);

  // "hello" → the fixed short reply: no citations, no refusal, no badge.
  const reply = await ask(page, "hello", 0);
  await expect(reply.getByTestId("refusal-card")).toHaveCount(0);
  await expect(reply.getByRole("button", { name: /^Citation \d+:/ })).toHaveCount(0);
  await expect(reply.getByTestId("answer-badge")).toHaveCount(0);
  const text = ((await reply.textContent()) ?? "").trim();
  expect(text.length).toBeGreaterThan(0);
  expect(text.length).toBeLessThan(400);

  // Brainstorming → the Ideation hint under the answer (or refusal).
  const ideas = await ask(page, BRAINSTORM, 1);
  await expect(ideas.getByTestId("ideation-hint")).toBeVisible();
  // (`ask` returns the last answer, so look the greeting's reply up by position.)
  await expect(
    page.getByTestId("assistant-message").first().getByTestId("ideation-hint"),
  ).toHaveCount(0);

  // ⋮ → Gate log: both decisions.
  const openLog = async () => {
    await page.getByRole("button", { name: "Space menu" }).click();
    await page.getByRole("menuitem", { name: "Gate log" }).click();
    const dialog = page.getByRole("dialog", { name: "Gate log" });
    await expect(dialog).toBeVisible();
    return dialog;
  };
  let dialog = await openLog();
  const table = dialog.getByRole("table", { name: "Gate decisions" });
  const helloRow = table.getByRole("row").filter({ hasText: "hello" });
  const ideasRow = table.getByRole("row").filter({ hasText: BRAINSTORM });
  await expect(helloRow).toHaveCount(1);
  await expect(ideasRow).toHaveCount(1);
  await expect(helloRow).toContainText("Small talk");
  await expect(ideasRow).toContainText("Brainstorming");

  // Correct "hello" → stored.
  const select = dialog.getByRole("combobox", { name: "Correct the decision for “hello”" });
  await expect(select).toHaveValue("");
  const saved = page.waitForResponse(
    (r) => /\/gate-log\/\d+\/correct$/.test(r.url()) && r.request().method() === "POST",
  );
  await select.selectOption("sources");
  expect((await saved).ok()).toBe(true);
  await expect(select).toHaveValue("sources");

  // Still there after closing and opening the log again, and in the API.
  await dialog.press("Escape");
  await expect(dialog).toBeHidden();
  dialog = await openLog();
  await expect(
    dialog.getByRole("combobox", { name: "Correct the decision for “hello”" }),
  ).toHaveValue("sources");
  const log = await getJson<GateRow[]>(
    request,
    `/api/spaces/${encodeURIComponent(spaceId)}/gate-log`,
  );
  expect(log.find((e) => e.input === "hello")).toMatchObject({
    decision: "chitchat",
    user_correction: "sources",
  });
  expect(log.find((e) => e.input === BRAINSTORM)).toMatchObject({ decision: "brainstorm" });
});
