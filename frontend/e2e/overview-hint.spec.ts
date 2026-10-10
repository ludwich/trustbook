import { expect, test } from "@playwright/test";
import {
  createSpace,
  deleteSpace,
  openSpace,
  pasteSource,
  prepareBackend,
  waitForReady,
} from "./helpers";

/**
 * Space overview and the one-time grounding hint (SPEC §9). The mock chat endpoint is local, so
 * the overview is written automatically when the first source becomes ready.
 */

const TOPICS = [
  "The Tallholm harbour authority maintains four piers, two slipways and a winter ice channel.",
  "Pier one handles the passenger ferry, which runs every forty minutes from May to September.",
  "Pier two is reserved for fishing boats, which land herring, cod and mackerel before dawn.",
  "The harbour office keeps a tide table, a berth register and a list of licensed pilots.",
  "Dredging of the inner basin happens every third spring to keep a depth of six metres.",
  "The breakwater was rebuilt in granite after the storm of 1987 damaged its outer section.",
  "Visiting yachts pay a nightly fee that includes fresh water, electricity and showers.",
  "A volunteer rescue crew keeps a rigid inflatable boat ready at the foot of the lighthouse.",
];

/** Long enough (several thousand characters) to become more than one chunk. */
function harbourGuide(): string {
  const sections: string[] = [];
  for (let i = 0; i < 6; i++) {
    sections.push(`## Part ${i + 1}`);
    for (let j = 0; j < TOPICS.length; j++) {
      sections.push(`${TOPICS[(i + j) % TOPICS.length]} Note ${i + 1}.${j + 1} applies all year.`);
    }
  }
  return sections.join("\n\n");
}

const HINT = "Answers only come from your ticked sources.";

let spaceId: string | null = null;

test.beforeAll(async ({ request }) => {
  test.setTimeout(600_000);
  await prepareBackend(request);
});

test.afterEach(async ({ request }) => {
  await deleteSpace(request, spaceId);
  spaceId = null;
});

test("the overview is written automatically and its suggested questions can be asked", async ({
  page,
  request,
}) => {
  spaceId = await createSpace(request, "E2E overview");
  const src = await pasteSource(request, spaceId, "Tallholm harbour guide", harbourGuide());
  await waitForReady(request, spaceId, [src]);
  await openSpace(page, spaceId);

  const card = page.getByTestId("overview-card");
  await expect(card).toBeVisible();
  // Pending → ready without anyone pressing a button.
  const sentences = card.getByTestId("answer-sentence");
  await expect(sentences.first()).toBeVisible({ timeout: 120_000 });
  const count = await sentences.count();
  expect(count, "overview sentences").toBeGreaterThanOrEqual(2);
  expect(count, "overview sentences").toBeLessThanOrEqual(4);
  await expect(
    card.getByRole("button", { name: /^Citation \d+: Tallholm harbour guide/ }).first(),
  ).toBeVisible();

  const questions = card.getByTestId("suggested-question");
  await expect(questions).toHaveCount(3);
  const question = (await questions.first().textContent())?.trim() ?? "";
  expect(question.length).toBeGreaterThan(0);

  // Clicking a suggestion asks it.
  await questions.first().click();
  // Scoped to the chat: the conversation picker also holds the question (as a hidden <option>).
  await expect(
    page.getByTestId("user-message").filter({ hasText: question }).first(),
  ).toBeVisible();
  await expect(page.getByTestId("assistant-message")).toHaveCount(1, { timeout: 90_000 });
});

test("the one-time hint can be dismissed and stays hidden after a reload", async ({
  page,
  request,
}) => {
  spaceId = await createSpace(request, "E2E hint");
  const settings = (await (await request.get("/api/settings")).json()) as {
    hints_dismissed?: string[];
  };
  const alreadyDismissed = (settings.hints_dismissed ?? []).includes("chat_grounding");
  await openSpace(page, spaceId);
  const hint = page.getByTestId("grounding-hint");

  if (alreadyDismissed) {
    // The data folder was used by an earlier run (e.g. a CI retry): dismissal is permanent and
    // there is no API to undo it, so only the "stays hidden" half can be checked.
    test.info().annotations.push({ type: "note", description: "hint was already dismissed" });
  } else {
    await expect(hint).toBeVisible();
    await expect(hint).toContainText(HINT);
    await expect(hint).toContainText("Ideas and brainstorming live in the Ideation tab.");
    const saved = page.waitForResponse(
      (r) => r.url().endsWith("/api/settings") && r.request().method() === "PUT",
    );
    await hint.getByRole("button", { name: "Dismiss hint" }).click();
    expect((await saved).ok(), "dismiss hint saved").toBeTruthy();
    await expect(hint).toBeHidden();
  }

  await page.reload();
  await expect(page.getByRole("textbox", { name: "Question" })).toBeVisible();
  await expect(page.getByText(HINT)).toHaveCount(0);
  await expect(hint).toHaveCount(0);
});
