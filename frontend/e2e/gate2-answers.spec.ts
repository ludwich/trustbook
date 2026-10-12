import { expect, test } from "@playwright/test";
import {
  ask,
  citationsTo,
  createSpace,
  deleteSpace,
  openSpace,
  pasteSource,
  prepareBackend,
  putCapability,
  waitForReady,
} from "./helpers";

/**
 * Gate 2 UI checks #3–#5 (answers): badges and the removed-statements drawer, the Unverified
 * badge with answer checking off, and the refusal card. Mock chat model (TRUSTBOOK_MOCK_LLM=1),
 * real grounding checker. A question containing TB-UNSUPPORTED makes the mock add one sentence
 * the sources don't support (ARCHITECTURE "Test hooks").
 */

const TITLE = "Harbour crane manual";
const TEXT = [
  "The Lindholm harbour crane lifts at most forty tonnes in calm weather.",
  "Operators must stop lifting when the wind speed is above fifteen metres per second.",
  "The crane is inspected by the harbour engineer every second Monday.",
].join("\n\n");
const QUESTION = "How much can the Lindholm harbour crane lift?";
const UNSUPPORTED = "This also requires a licence key from the vendor.";
const UNVERIFIED_TIP = "Answer checking is turned off in Settings";

let spaceId: string | null = null;

test.beforeAll(async ({ request }) => {
  test.setTimeout(600_000);
  await prepareBackend(request);
});

test.afterEach(async ({ request }) => {
  await deleteSpace(request, spaceId);
  spaceId = null;
});

async function craneSpace(request: Parameters<typeof createSpace>[0], name: string) {
  spaceId = await createSpace(request, name);
  const id = await pasteSource(request, spaceId, TITLE, TEXT);
  await waitForReady(request, spaceId, [id]);
  return spaceId;
}

test("Gate 2 #3: Verified answer; an unsupported statement is removed into the drawer", async ({
  page,
  request,
}) => {
  const sid = await craneSpace(request, "E2E g2 badges");
  await openSpace(page, sid);

  const verified = await ask(page, QUESTION, 0);
  const badge = verified.getByTestId("answer-badge");
  await expect(badge).toHaveAttribute("data-badge", "verified");
  await expect(badge).toHaveText("Verified");
  await expect(citationsTo(verified, TITLE).first()).toBeVisible();
  await expect(verified.getByTestId("removed-sentences")).toHaveCount(0);

  // The mock adds one unsupported sentence; answer checking removes it.
  const partial = await ask(page, `${QUESTION} TB-UNSUPPORTED`, 1);
  const partialBadge = partial.getByTestId("answer-badge");
  await expect(partialBadge).toHaveAttribute("data-badge", "partially_verified");
  await expect(partialBadge).toContainText("Partially verified");
  await expect(partialBadge).toContainText("1 statement removed");
  await expect(citationsTo(partial, TITLE).first()).toBeVisible();
  // Not part of the answer itself.
  await expect(partial.getByTestId("answer-sentence").filter({ hasText: UNSUPPORTED })).toHaveCount(
    0,
  );

  // Collapsed drawer → opens and lists the removed statement.
  const drawer = partial.getByTestId("removed-sentences");
  const toggle = drawer.getByRole("button", { name: /Unverified, not from your sources/ });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(drawer.getByText(UNSUPPORTED)).toHaveCount(0);
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  const removed = drawer.getByRole("list", { name: "Removed statements" });
  await expect(removed.getByRole("listitem")).toHaveCount(1);
  await expect(removed).toContainText(UNSUPPORTED);
});

test("Gate 2 #4: answer checking off → Unverified with a tooltip; back on afterwards", async ({
  page,
  request,
}) => {
  const sid = await craneSpace(request, "E2E g2 unverified");
  try {
    // Turn the grounding checker off in Settings.
    await page.goto("/settings");
    const card = page.getByTestId("capability-card-grounding");
    const onOff = card.getByRole("switch", { name: /^(On|Off)$/ });
    await expect(onOff).toHaveAttribute("aria-checked", "true");
    await onOff.click();
    await expect(card.getByText(/marked "Unverified"/)).toBeVisible();
    await card.getByRole("button", { name: "Save" }).click();
    await expect(card.getByText("Turned off")).toBeVisible();

    // Ask → Unverified, with the reason on hover and as the badge's description.
    await openSpace(page, sid);
    const answer = await ask(page, QUESTION, 0);
    const badge = answer.getByTestId("answer-badge");
    await expect(badge).toHaveAttribute("data-badge", "unverified");
    await expect(badge).toHaveText("Unverified");
    await expect(badge).toHaveAccessibleDescription(UNVERIFIED_TIP);
    const tip = answer.getByRole("tooltip");
    await expect(tip).toBeHidden();
    await badge.hover();
    await expect(tip).toBeVisible();
    await expect(tip).toHaveText(UNVERIFIED_TIP);

    // Turn it back on through the UI.
    await page.goto("/settings");
    await expect(onOff).toHaveAttribute("aria-checked", "false");
    await onOff.click();
    await card.getByRole("button", { name: "Save" }).click();
    await expect(card.getByText("Turned off")).toHaveCount(0);
    await expect(onOff).toHaveAttribute("aria-checked", "true");
  } finally {
    // Never leave answer checking off for the specs that follow.
    await putCapability(request, "grounding", { enabled: true });
  }
});

test("Gate 2 #5: a question the sources don't cover gets one calm refusal card", async ({
  page,
  request,
}) => {
  const sid = await craneSpace(request, "E2E g2 refusal");
  await openSpace(page, sid);

  const question = "What is the capital city of Mongolia?";
  const answer = await ask(page, question, 0);
  const card = answer.getByTestId("refusal-card");
  await expect(card).toBeVisible();
  await expect(answer.getByTestId("refusal-title")).toHaveText("Not in your sources");

  // The subtitle is a readable sentence that quotes the question.
  const sub = answer.getByTestId("refusal-suggestion");
  await expect(sub).toBeVisible();
  await expect(sub).toContainText(/capital city of Mongolia/i);
  await expect(sub).toContainText(/[“"].+[”"]/);
  await expect(sub).not.toContainText("Not in your sources");

  // The title appears once in the whole answer (no repetition), and there are no citations.
  const text = (await answer.textContent()) ?? "";
  expect(text.split("Not in your sources").length - 1).toBe(1);
  await expect(answer.getByRole("button", { name: /^Citation \d+:/ })).toHaveCount(0);
});
