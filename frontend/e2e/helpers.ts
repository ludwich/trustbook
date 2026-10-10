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
