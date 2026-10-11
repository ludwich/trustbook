import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import {
  ask,
  citationsTo,
  createSpace,
  deleteSpace,
  expectQueueShows,
  expectSourceMatchesJob,
  listSources,
  openSpace,
  prepareBackend,
  settleSourceAndJob,
} from "./helpers";

/**
 * PDF upload with OCR (Gate 1 follow-up): page 1 of the fixture has a text layer, page 2 is only
 * an image, so Docling reads it with OCR. The source must reach Ready, carry the OCR quality
 * warning, and answer a question whose facts are only on the scanned page.
 */

const FIXTURE = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "fixtures",
  "scanned-notice.pdf",
);
const FILE_NAME = "scanned-notice.pdf";
const QUESTION = "When does the north pier crane inspection end?";

let spaceId: string | null = null;

test.beforeAll(async ({ request }) => {
  test.setTimeout(600_000);
  await prepareBackend(request);
});

test.afterEach(async ({ request }) => {
  await deleteSpace(request, spaceId);
  spaceId = null;
});

test("upload a PDF with a scanned page: OCR warning and a cited answer", async ({
  page,
  request,
}) => {
  // OCR on CPU is the slow part.
  test.setTimeout(420_000);
  spaceId = await createSpace(request, "E2E pdf");
  await openSpace(page, spaceId);

  // Upload through the UI.
  await page.getByRole("button", { name: "Add source" }).click();
  await page.getByRole("menuitem", { name: /upload files/i }).click();
  const dialog = page.getByRole("dialog", { name: "Upload files" });
  await dialog.getByLabel("Choose files").setInputFiles(FIXTURE);
  await expect(dialog.getByRole("list", { name: "Files to upload" })).toContainText(FILE_NAME);
  await dialog.getByRole("button", { name: "Add 1 file" }).click();
  await expect(dialog).toBeHidden();

  // Read (incl. OCR) and indexed.
  const row = page.locator("li[data-source-status]").filter({ hasText: FILE_NAME });
  await expect(row).toHaveAttribute("data-source-status", "ready", { timeout: 360_000 });
  await expect(row.getByText(/Some text was read from images \(OCR\)/)).toBeVisible();
  await expect(row.getByRole("checkbox", { name: new RegExp(`^Use ${FILE_NAME}`) })).toBeChecked();
  await expect(page.getByText("Using 1 of 1 source")).toBeVisible();

  // Gate 3 item B: its ingest job is Done too, in the API and on the Queue page.
  const [uploaded] = await listSources(request, spaceId);
  expect(uploaded?.title).toBe(FILE_NAME);
  const { source, job } = await settleSourceAndJob(request, spaceId, uploaded?.id ?? "");
  expect(job.status).toBe("done");
  expectSourceMatchesJob(source, job);
  await expectQueueShows(page, job);
  await openSpace(page, spaceId);

  // The answer comes from the OCR'd page and cites the PDF.
  const answer = await ask(page, QUESTION, 0);
  await expect(answer.getByTestId("refusal-card")).toHaveCount(0);
  await expect(citationsTo(answer, FILE_NAME).first()).toBeVisible();
  await expect(answer.getByTestId("answer-sentence").first()).toContainText(
    /north|pier|crane|inspection/i,
  );

  // The citation opens the viewer on the PDF.
  await citationsTo(answer, FILE_NAME).first().click();
  const viewer = page.getByRole("dialog", { name: FILE_NAME });
  await expect(viewer.getByTestId("cited-passage")).toBeVisible();
  await expect(viewer.getByTestId("cited-passage")).toContainText(/north|pier|crane|inspection/i);
});
