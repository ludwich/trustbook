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
 * Badges and the gate (SPEC §8, Phase 2): an answerable question gets the "Verified" badge (the
 * mock chat model quotes the chunks, and the real grounding checker in the image verifies them);
 * a greeting takes the gate's chitchat path and gets a short reply without citations.
 */

const TITLE = "Harbour crane manual";
const TEXT = [
  "The Lindholm harbour crane lifts at most forty tonnes in calm weather.",
  "Operators must stop lifting when the wind speed is above fifteen metres per second.",
  "The crane is inspected by the harbour engineer every second Monday.",
].join("\n\n");
const QUESTION = "How much can the Lindholm harbour crane lift?";

let spaceId: string | null = null;

test.beforeAll(async ({ request }) => {
  test.setTimeout(600_000);
  await prepareBackend(request);
});

test.afterEach(async ({ request }) => {
  await deleteSpace(request, spaceId);
  spaceId = null;
});

test("a grounded answer is Verified; a greeting gets a short reply without citations", async ({
  page,
  request,
}) => {
  spaceId = await createSpace(request, "E2E badges");
  const id = await pasteSource(request, spaceId, TITLE, TEXT);
  await waitForReady(request, spaceId, [id]);
  await openSpace(page, spaceId);
  await expect(page.getByText("Using 1 of 1 source")).toBeVisible();

  // Answerable → verified, with citations to the pasted source.
  const answer = await ask(page, QUESTION, 0);
  await expect(answer.getByTestId("refusal-card")).toHaveCount(0);
  const badge = answer.getByTestId("answer-badge");
  await expect(badge).toHaveAttribute("data-badge", "verified");
  await expect(badge).toHaveText("Verified");
  await expect(citationsTo(answer, TITLE).first()).toBeVisible();
  await expect(answer.getByTestId("removed-sentences")).toHaveCount(0);

  // Greeting → the gate's fixed short reply: no citations, no refusal, no sources chip.
  const reply = await ask(page, "hello", 1);
  await expect(reply.getByTestId("refusal-card")).toHaveCount(0);
  await expect(reply.getByRole("button", { name: /^Citation \d+:/ })).toHaveCount(0);
  await expect(reply.getByRole("button", { name: /sources used/i })).toHaveCount(0);
  const text = ((await reply.textContent()) ?? "").trim();
  expect(text.length).toBeGreaterThan(0);
  expect(text.length).toBeLessThan(400);
});
