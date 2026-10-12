import { readFileSync } from "node:fs";
import { expect, test, type Locator, type Page } from "@playwright/test";
import {
  ask,
  createSpace,
  deleteSpace,
  escapeRegExp,
  listSources,
  openSpace,
  pasteSource,
  prepareBackend,
  waitForReady,
} from "./helpers";

/**
 * Gate 4 (SPEC §10, §14): the Ideation tab end to end with the mock chat model
 * (TRUSTBOOK_MOCK_LLM=1) and the real bundled retrieval and grounding checker.
 *
 * The mock (ARCHITECTURE "Test hooks", D-093) seeds three branches ("Who it is for", "What your
 * sources say about <word>", "Risks and open problems"), or four when the idea contains TB-SEEDS4
 * ("How it is paid for" before the risks). It recommends the source sentence sharing most words
 * with the idea and the branch (cited), and opens one new branch under a branch whose answer
 * contains TB-BRANCH. The server picks the next branch before the model call: the open one, else
 * the first tentative branch under the last answered one, else the first tentative one in tree
 * order (backend/app/ideation/map.py, next_node).
 *
 * Mid-session test, with four seeds: dismiss "Risks", accept "Who it is for" (→ "What your
 * sources say"), type an answer with TB-BRANCH (→ "How it is paid for"; the answer opens
 * "Follow-up on …" under the branch it answered), skip (→ "Follow-up on …"). So 3 resolved and 1
 * open when Finish now is pressed. The complete test uses three seeds and no hooks.
 */

const TITLE = "Gull Island visitor guide";
const BEST = "Guided puffin walks on Gull Island suit birdwatchers who arrive on the morning ferry";
const TEXT = [
  "Gull Island has a colony of puffins that nest on the north cliffs from May to August.",
  `${BEST}.`,
  "The ferry to Gull Island leaves the harbour twice a day in summer.",
  "The island hostel has twelve beds and serves breakfast from seven.",
].join("\n\n");
const IDEA = "Guided puffin walks on Gull Island for birdwatchers";
/** The same idea with the mock hook for a fourth seed, so the session stays mid-session. */
const IDEA_FOUR_SEEDS = `${IDEA} TB-SEEDS4`;
/** A made-up word that is only ever typed into the interview, so only the brief holds it. */
const BRIEF_ONLY = "Kestrelmoss";
const TURN_TIMEOUT = 90_000;

let spaceId: string | null = null;

test.beforeAll(async ({ request }) => {
  test.setTimeout(600_000);
  await prepareBackend(request);
});

test.afterEach(async ({ request }) => {
  await deleteSpace(request, spaceId);
  spaceId = null;
});

async function openIdeation(page: Page): Promise<void> {
  await page.getByRole("tab", { name: "Ideation" }).click();
  await expect(page.getByRole("tab", { name: "Ideation" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
}

/**
 * Waits until the turn has ended with a question other than `previous` (returns its text) or, when
 * `allowComplete`, with the session complete (returns null). Fails early with the message when the
 * turn ends in an error.
 */
async function nextStep(
  page: Page,
  previous: string | null,
  allowComplete = false,
): Promise<string | null> {
  let text = "";
  let outcome = "";
  await expect
    .poll(
      async () => {
        const error = page.getByTestId("ideation-error");
        if (await error.count()) return `error: ${(await error.textContent()) ?? ""}`;
        if (await page.getByTestId("ideation-stage").count()) return "busy";
        if (allowComplete && (await page.getByTestId("ideation-complete").count())) {
          outcome = "complete";
          return "done";
        }
        const q = page.getByTestId("question-text");
        if (!(await q.count())) return "no question";
        text = ((await q.textContent()) ?? "").trim();
        if (text === previous) return "same question";
        outcome = "question";
        return "done";
      },
      { timeout: TURN_TIMEOUT, intervals: [500, 1_000] },
    )
    .toBe("done");
  return outcome === "complete" ? null : text;
}

async function questionAfter(page: Page, previous: string | null): Promise<string> {
  return (await nextStep(page, previous)) ?? "";
}

/** The map as [title, status, current] rows, top to bottom. */
async function mapRows(page: Page): Promise<string[][]> {
  return page
    .getByTestId("map-node")
    .evaluateAll((els) =>
      els.map((el) => [
        el.querySelector("[data-testid='map-node-title']")?.textContent ?? "",
        el.getAttribute("data-status") ?? "",
        el.getAttribute("data-current") ?? "",
      ]),
    );
}

async function startIdea(page: Page, idea = IDEA): Promise<string> {
  await openIdeation(page);
  const box = page.getByRole("textbox", { name: "I have an idea" });
  await expect(box).toBeEnabled();
  await box.fill(idea);
  await page.getByRole("button", { name: "Start" }).click();
  return questionAfter(page, null);
}

async function downloadBrief(page: Page): Promise<string> {
  const link = page.getByRole("link", { name: "Export Markdown" });
  await expect(link).toBeVisible();
  const download = page.waitForEvent("download");
  await link.click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^idea-brief-.+\.md$/);
  const path = await file.path();
  return readFileSync(path, "utf8");
}

function section(brief: string, heading: string): string {
  const start = brief.indexOf(`## ${heading}\n`);
  expect(start, `brief has "## ${heading}"`).toBeGreaterThanOrEqual(0);
  const rest = brief.slice(start + heading.length + 4);
  const end = rest.indexOf("\n## ");
  return end === -1 ? rest : rest.slice(0, end);
}

function citedSentences(card: Locator): Locator {
  return card.locator("[data-testid='recommendation-sentence'][data-kind='cited']");
}

test("Gate 4: a cited recommendation, accept / answer / skip / dismiss, resume, Finish now mid-session; the brief never reaches chat", async ({
  page,
  request,
}) => {
  spaceId = await createSpace(request, "E2E ideation");
  const src = await pasteSource(request, spaceId, TITLE, TEXT);
  await waitForReady(request, spaceId, [src]);
  await openSpace(page, spaceId);

  // 1. Start an idea: the map is seeded and the first question has a cited recommendation.
  const first = await startIdea(page, IDEA_FOUR_SEEDS);
  expect(first).toContain("Who it is for");
  const card = page.getByTestId("question-card");
  await expect(citedSentences(card).first()).toBeVisible();
  await expect(citedSentences(card).first()).toContainText(BEST);
  await expect(card.getByTestId("answer-badge")).toHaveAttribute("data-badge", "verified");
  const map = page.getByTestId("branch-map");
  await expect(page.getByTestId("map-node")).toHaveCount(4);
  await expect(map.getByTestId("map-progress")).toHaveText("0 resolved, 4 open");
  await expect(page.locator("[data-testid='map-node'][data-current='true']")).toHaveCount(1);

  // The citation opens the source viewer at the passage.
  await card
    .getByRole("button", { name: new RegExp(`^Citation 1: ${escapeRegExp(TITLE)}`) })
    .click();
  const viewer = page.getByRole("dialog", { name: TITLE });
  await expect(viewer).toBeVisible();
  await expect(viewer.locator("mark")).toContainText("puffin");
  await page.keyboard.press("Escape");
  await expect(viewer).toBeHidden();

  // 2. Dismiss a seed that was not asked yet.
  await map.getByRole("button", { name: /^Dismiss branch “Risks and open problems”/ }).click();
  await expect(map.getByText("Risks and open problems")).toHaveCount(0);
  await expect(map.getByTestId("map-progress")).toHaveText("0 resolved, 3 open");

  // 3. Accept the recommendation; the next seed is asked.
  await card.getByRole("button", { name: "Accept recommendation" }).click();
  const second = await questionAfter(page, first);
  expect(second).toContain("What your sources say about guided");
  const thread = page.getByRole("list", { name: "Interview so far" });
  await expect(thread.getByTestId("accepted-tag")).toHaveCount(1);

  // 4. Type an answer with a phrase that exists nowhere but in this session (and its brief).
  const answer = `Sell tickets at the ${BRIEF_ONLY} kiosk by the pier TB-BRANCH`;
  await page.getByRole("textbox", { name: "Your answer" }).fill(answer);
  await page.getByRole("button", { name: "Answer" }).click();
  const third = await questionAfter(page, second);
  expect(third).toContain("How it is paid for");
  await expect(thread).toContainText(`${BRIEF_ONLY} kiosk`);
  // TB-BRANCH opened a branch under the answered one.
  await expect(
    map.getByTestId("map-node-title").filter({ hasText: /^Follow-up on Sell tickets/ }),
  ).toHaveCount(1);

  // 5. Skip one; another branch still waits (mid-session).
  await page.getByRole("button", { name: "Skip" }).click();
  const waiting = await questionAfter(page, third);
  expect(waiting).toContain("Follow-up on Sell tickets");
  await expect(thread.locator("[data-answer-source='skipped']")).toHaveCount(1);
  await expect(map.getByTestId("map-progress")).toHaveText("3 resolved, 1 open");
  await expect(map.getByTestId("map-complete")).toHaveCount(0);
  const rows = await mapRows(page);
  expect(rows.filter((r) => r[2] === "true")).toHaveLength(1);

  // 6. Reload: the same question and the same map come back, with no new model call needed.
  await page.reload();
  await openIdeation(page);
  await expect(page.getByTestId("question-text")).toHaveText(waiting, { timeout: 30_000 });
  await expect.poll(() => mapRows(page)).toEqual(rows);
  await expect(page.getByTestId("map-progress")).toHaveText("3 resolved, 1 open");

  // 7. Finish now, mid-session: the brief is written and can be exported.
  await page.getByRole("button", { name: "Finish now" }).click();
  await expect(page.getByTestId("ideation-finished")).toBeVisible();
  await expect(page.getByRole("button", { name: "Continue" })).toBeVisible();
  const brief = await downloadBrief(page);
  expect(brief.startsWith("<!-- trustbook:idea-brief -->\n")).toBeTruthy();
  expect(section(brief, "Cited anchors")).toContain(`(*${TITLE}*`);
  const open = section(brief, "Open questions");
  expect(open).toContain("(waiting for an answer)");
  expect(open).toContain("(skipped)");
  expect(brief).toContain("(accepted recommendation)");
  expect(brief).toContain("```mermaid\nmindmap\n");
  expect(brief).toContain(BRIEF_ONLY);

  // 8. The brief never becomes a source or a citation: ask chat about the brief-only phrase.
  await page.getByRole("tab", { name: "Chat" }).click();
  await expect(page.getByText("Using 1 of 1 source")).toBeVisible();
  const reply = await ask(page, `Where is the ${BRIEF_ONLY} kiosk?`, 0);
  await expect(reply.getByTestId("refusal-card")).toBeVisible();
  await expect(reply.getByRole("button", { name: /^Citation \d+/ })).toHaveCount(0);
  await expect(page.getByText("Using 1 of 1 source")).toBeVisible();
  const sources = await listSources(request, spaceId);
  expect(sources.map((s) => s.title)).toEqual([TITLE]);
  await expect(page.locator("li[data-source-status]")).toHaveCount(1);
});

test("Gate 4: a full session runs to Complete and its brief has cited anchors and open questions", async ({
  page,
  request,
}) => {
  spaceId = await createSpace(request, "E2E ideation complete");
  const src = await pasteSource(request, spaceId, TITLE, TEXT);
  await waitForReady(request, spaceId, [src]);
  await openSpace(page, spaceId);

  let question = await startIdea(page);
  const card = page.getByTestId("question-card");
  await expect(citedSentences(card).first()).toBeVisible();

  // Accept the first, skip the second, answer the rest until no branch is open.
  await card.getByRole("button", { name: "Accept recommendation" }).click();
  question = await questionAfter(page, question);
  await page.getByRole("button", { name: "Skip" }).click();
  question = await questionAfter(page, question);
  let step: string | null = question;
  for (let i = 0; i < 6 && step !== null; i++) {
    await page.getByRole("textbox", { name: "Your answer" }).fill("Keep it small at first.");
    await page.getByRole("button", { name: "Answer" }).click();
    step = await nextStep(page, step, true);
  }
  expect(step, "the session completes within six answers").toBeNull();

  const map = page.getByTestId("branch-map");
  await expect(map.getByTestId("map-complete")).toHaveText("Complete");
  await expect(map.getByTestId("map-progress")).toHaveText(/^\d+ resolved, 0 open$/);
  await expect(page.getByTestId("ideation-complete")).toBeVisible();
  await expect(page.getByTestId("question-card")).toHaveCount(0);

  await page.getByRole("button", { name: "Finish now" }).first().click();
  await expect(page.getByTestId("ideation-finished")).toBeVisible();
  const brief = await downloadBrief(page);
  expect(section(brief, "Cited anchors")).toContain(`(*${TITLE}*`);
  // The skipped question is an open question again.
  expect(section(brief, "Open questions")).toContain("(skipped)");
  expect(brief).toContain("(accepted recommendation)");
  expect(brief).toContain("```mermaid\nmindmap\n");

  // The session list marks it finished.
  await expect(page.getByRole("combobox", { name: "Idea session" })).toContainText("(finished)");
});
