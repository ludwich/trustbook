import { expect, test } from "@playwright/test";
import {
  ask,
  citationsTo,
  createSpace,
  deleteSpace,
  openSpace,
  pasteSource,
  prepareBackend,
  waitForReady,
} from "./helpers";

/**
 * Checkbox scoping (SPEC §8 "filter to the space's enabled sources"): an unticked source is never
 * used for answers; ticking it again brings it back. Group checkboxes do the same for a group.
 */

const A_TITLE = "Brennvik bakery";
const A_TEXT = [
  "The Brennvik bakery opens at six in the morning and bakes caraway rye loaves every day.",
  "Its owner, Sigrid Holm, took over the bakery from her grandfather in 2009.",
].join("\n\n");
const A_QUESTION = "What kind of loaves does the Brennvik bakery bake?";

const B_TITLE = "Ostby ferry timetable";
const B_TEXT = [
  "The Ostby ferry crosses the fjord every forty minutes during the summer season.",
  "In winter the crossing runs hourly and the last departure is at ten in the evening.",
].join("\n\n");

let spaceId: string | null = null;

test.beforeAll(async ({ request }) => {
  test.setTimeout(600_000);
  await prepareBackend(request);
});

test.afterEach(async ({ request }) => {
  await deleteSpace(request, spaceId);
  spaceId = null;
});

test("unticked sources are not used; ticking them again brings them back", async ({
  page,
  request,
}) => {
  spaceId = await createSpace(request, "E2E scoping");
  const a = await pasteSource(request, spaceId, A_TITLE, A_TEXT);
  const b = await pasteSource(request, spaceId, B_TITLE, B_TEXT);
  await waitForReady(request, spaceId, [a, b]);
  await openSpace(page, spaceId);

  const boxA = page.getByRole("checkbox", { name: `Use ${A_TITLE}` });
  const boxB = page.getByRole("checkbox", { name: `Use ${B_TITLE}` });
  await expect(boxA).toBeChecked();
  await expect(boxB).toBeChecked();
  await expect(page.getByText("Using 2 of 2 sources")).toBeVisible();

  // Untick A → it is not used.
  await boxA.uncheck();
  await expect(page.getByText("Using 1 of 2 sources")).toBeVisible();
  const without = await ask(page, A_QUESTION, 0);
  await expect(citationsTo(without, A_TITLE)).toHaveCount(0);
  const refused = await without.getByTestId("refusal-card").count();
  if (refused > 0) {
    await expect(without.getByTestId("refusal-title")).toHaveText("Not in your sources");
  }

  // Tick A again → the answer cites it.
  await boxA.check();
  await expect(page.getByText("Using 2 of 2 sources")).toBeVisible();
  const withA = await ask(page, A_QUESTION, 1);
  await expect(withA.getByTestId("refusal-card")).toHaveCount(0);
  await expect(citationsTo(withA, A_TITLE).first()).toBeVisible();
});

test("a group checkbox unticks and ticks the whole group", async ({ page, request }) => {
  spaceId = await createSpace(request, "E2E group");
  const a = await pasteSource(request, spaceId, A_TITLE, A_TEXT);
  const b = await pasteSource(request, spaceId, B_TITLE, B_TEXT);
  await waitForReady(request, spaceId, [a, b]);
  await openSpace(page, spaceId);

  const group = page.getByRole("checkbox", { name: "Use all documents" });
  const boxA = page.getByRole("checkbox", { name: `Use ${A_TITLE}` });
  const boxB = page.getByRole("checkbox", { name: `Use ${B_TITLE}` });
  await expect(group).toBeChecked();
  await expect(page.getByText("Using 2 of 2 sources")).toBeVisible();

  // Untick the group → nothing is used and asking is blocked.
  await group.uncheck();
  await expect(boxA).not.toBeChecked();
  await expect(boxB).not.toBeChecked();
  await expect(page.getByText("Tick at least one source to ask.")).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Question" })).toBeDisabled();

  // The backend agrees (it counts ready + ticked).
  const status = (await (
    await request.get(`/api/spaces/${encodeURIComponent(spaceId)}/chat/status`)
  ).json()) as { enabled_sources: number; ready_sources: number };
  expect(status).toMatchObject({ enabled_sources: 0, ready_sources: 2 });

  // One source on its own makes the group mixed; the group box then ticks all.
  await boxB.check();
  await expect(page.getByText("Using 1 of 2 sources")).toBeVisible();
  await expect(group).not.toBeChecked();
  expect(await group.evaluate((el) => (el as { indeterminate?: boolean }).indeterminate)).toBe(
    true,
  );
  await group.check();
  await expect(boxA).toBeChecked();
  await expect(boxB).toBeChecked();
  await expect(page.getByText("Using 2 of 2 sources")).toBeVisible();

  // Survives a reload (stored, not just optimistic).
  await page.reload();
  await expect(page.getByRole("checkbox", { name: `Use ${A_TITLE}` })).toBeChecked();
  await expect(page.getByText("Using 2 of 2 sources")).toBeVisible();
});
