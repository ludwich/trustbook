/// <reference lib="dom" />
// Browser-side callbacks (addInitScript, evaluate) below use DOM types.
import { randomBytes } from "node:crypto";
import { expect, test } from "@playwright/test";
import {
  ask,
  BROWSE_ROOT,
  createSpace,
  deleteSpace,
  getJson,
  type HostFolder,
  linkFolder,
  NO_BROWSE_ROOT,
  openSpace,
  prepareBackend,
  removeHostFolder,
  waitForReady,
  writeHostFolder,
} from "./helpers";

/**
 * Gate 2 UI checks #6 (code viewer) and #7 (secrets report). Both write a small folder into the
 * browse root on the host (E2E_BROWSE_ROOT, mounted read-only at /host in the container) and link
 * it. The planted secret is generated at runtime: no secret-shaped literal lives in this repo.
 */

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

/** A Python module whose pool code sits far below the top, so the viewer must scroll to it. */
function poolModule(): string {
  const filler = Array.from(
    { length: 70 },
    (_, i) =>
      `def scale_${i}(value):\n    """Multiply value by ${i}."""\n    return value * ${i}\n`,
  ).join("\n\n");
  // No module docstring: the constant below is the only module-level code, so no chunk spans
  // from the top of the file to the pool code.
  return [
    filler,
    "",
    "DEFAULT_POOLSIZE = 10",
    "",
    "",
    "def make_pool(size=DEFAULT_POOLSIZE):",
    '    """Create a connection pool. The default pool size is DEFAULT_POOLSIZE, which is 10 connections."""',
    '    return ["conn-%d" % n for n in range(size)]',
    "",
  ].join("\n");
}

interface FirstPaint {
  scrollTop: number;
  clientHeight: number;
  rowTop: number | null;
  rowHeight: number | null;
}

test("Gate 2 #6: a code citation opens monospace code at the cited lines, in view at once", async ({
  page,
  request,
}) => {
  test.skip(!BROWSE_ROOT, NO_BROWSE_ROOT);
  folder = writeHostFolder("e2e-repo", { "pool.py": poolModule() });
  spaceId = await createSpace(request, "E2E g2 code viewer");
  const src = await linkFolder(request, spaceId, folder.rel);
  await waitForReady(request, spaceId, [src.id]);

  // Record the viewer's state in the same task React inserts it (after its layout effect, before
  // the browser paints): that is what the first frame shows.
  await page.addInitScript(() => {
    const w = window as unknown as { __codeFirstPaint?: unknown };
    new MutationObserver(() => {
      if (w.__codeFirstPaint) return;
      const el = document.querySelector<HTMLElement>('[data-testid="code-view"]');
      if (!el) return;
      const row = el.querySelector<HTMLElement>("[data-highlighted]");
      const box = el.getBoundingClientRect();
      w.__codeFirstPaint = {
        scrollTop: el.scrollTop,
        clientHeight: el.clientHeight,
        rowTop: row ? row.getBoundingClientRect().top - box.top : null,
        rowHeight: row ? row.getBoundingClientRect().height : null,
      };
    }).observe(document, { childList: true, subtree: true });
  });

  await openSpace(page, spaceId);
  const answer = await ask(page, "What is the default pool size in make_pool?", 0);
  await expect(answer.getByTestId("refusal-card")).toHaveCount(0);
  const citation = answer.getByRole("button", { name: /^Citation \d+:/ }).first();
  await expect(citation).toBeVisible();

  // The citation's locator: "<title>, pool.py, lines A–B (symbol)".
  const label = (await citation.getAttribute("title")) ?? "";
  const m = /lines? (\d+)(?:–(\d+))?/.exec(label);
  expect(m, `citation locator in "${label}"`).not.toBeNull();
  const from = Number(m?.[1]);
  const to = Number(m?.[2] ?? m?.[1]);
  expect(label).toContain("pool.py");

  await citation.click();
  const viewer = page.getByRole("dialog", { name: src.title });
  const code = viewer.getByTestId("code-view");
  await expect(code).toBeVisible();

  // In view on the first frame: already scrolled down to the highlighted row.
  const first = (await page.evaluate(
    () => (window as unknown as { __codeFirstPaint?: FirstPaint }).__codeFirstPaint ?? null,
  )) as FirstPaint | null;
  expect(first, "code view state on first paint").not.toBeNull();
  if (!first) return;
  expect(first.scrollTop).toBeGreaterThan(0);
  expect(first.rowTop).not.toBeNull();
  expect(first.rowTop ?? -1).toBeGreaterThanOrEqual(0);
  expect((first.rowTop ?? 0) + (first.rowHeight ?? 0)).toBeLessThanOrEqual(first.clientHeight);

  // Monospace, with line numbers.
  const font = await code.evaluate((el) => getComputedStyle(el).fontFamily);
  expect(font).toMatch(/mono/i);
  await expect(viewer.getByTestId("viewer-file")).toContainText("pool.py");
  await expect(viewer.getByTestId("viewer-file")).toContainText(
    from === to ? `line ${from}` : `lines ${from}–${to}`,
  );

  // The highlighted rows are exactly the cited lines, and each shows its number.
  const rows = code.locator("[data-highlighted]");
  const lines = await rows.evaluateAll((els) =>
    els.map((el) => Number(el.getAttribute("data-line"))),
  );
  expect(lines).toEqual(Array.from({ length: to - from + 1 }, (_, i) => from + i));
  await expect(rows.first()).toBeInViewport();
  await expect(rows.first().locator("span").first()).toHaveText(String(from));
  await expect(code).toContainText("DEFAULT_POOLSIZE");

  // No jump afterwards: the position from the first frame is kept.
  await page.waitForTimeout(750);
  expect(await code.evaluate((el) => el.scrollTop)).toBe(first.scrollTop);
});

/** A PEM block that gitleaks' `private-key` rule matches; random body, assembled at runtime. */
function fakePrivateKey(): { text: string; body: string[] } {
  const label = ["RSA", "PRIVATE", "KEY"].join(" ");
  const dashes = "-".repeat(5);
  const raw = randomBytes(600).toString("base64");
  const body = raw.match(/.{1,64}/g) ?? [raw];
  const text = [`${dashes}BEGIN ${label}${dashes}`, ...body, `${dashes}END ${label}${dashes}`].join(
    "\n",
  );
  return { text, body };
}

test("Gate 2 #7: a planted secret is skipped and reported by file, line and rule only", async ({
  page,
  request,
}) => {
  test.skip(!BROWSE_ROOT, NO_BROWSE_ROOT);
  const key = fakePrivateKey();
  folder = writeHostFolder("e2e-secrets", {
    "app/settings.py": 'SERVICE_NAME = "billing"\nRETRY_LIMIT = 3\n',
    "README.md": "# Billing service\n\nSettings live in app/settings.py.\n",
    // Two lines of text first: the key starts on line 3.
    "keys/deploy_key.pem": `Deploy key for the staging box.\nDo not share.\n${key.text}\n`,
  });
  spaceId = await createSpace(request, "E2E g2 secrets");
  const src = await linkFolder(request, spaceId, folder.rel);
  await waitForReady(request, spaceId, [src.id]);
  await openSpace(page, spaceId);

  // Exactly one secrets line in the panel, and it is the button.
  const row = page.getByTestId(`source-${src.id}`);
  await expect(row).toHaveAttribute("data-source-status", "ready");
  const panel = page.getByRole("complementary", { name: "Sources" });
  await expect(panel.getByText(/skipped: possible secrets/)).toHaveCount(1);
  const button = row.getByRole("button", { name: "1 file skipped: possible secrets" });
  await expect(button).toHaveCount(1);
  await button.click();

  const dialog = page.getByTestId("secrets-dialog");
  await expect(dialog).toBeVisible();
  const table = dialog.getByRole("table", { name: "Possible secrets" });
  await expect(dialog).toContainText(`1 file in “${src.title}”`);
  // Every finding is in the planted file; the key itself is one row: file, line 3, rule ID.
  const findings = table.locator("tbody tr");
  await expect(findings.first()).toBeVisible();
  await expect(findings.filter({ hasNotText: "deploy_key.pem" })).toHaveCount(0);
  const keyRow = findings.filter({ hasText: "private-key" });
  await expect(keyRow).toHaveCount(1);
  const cells = keyRow.getByRole("cell");
  await expect(cells.nth(0)).toHaveText(/(^|\/)keys\/deploy_key\.pem$/);
  await expect(cells.nth(1)).toHaveText("3");
  await expect(cells.nth(2)).toHaveText("private-key");

  // Never the value: not in the dialog, the page or the API.
  const shown = (await dialog.textContent()) ?? "";
  expect(shown).not.toContain("PRIVATE KEY");
  for (const line of key.body) expect(shown).not.toContain(line.slice(0, 24));
  const html = await page.content();
  for (const line of key.body) expect(html).not.toContain(line.slice(0, 24));
  const report = JSON.stringify(
    await getJson(
      request,
      `/api/spaces/${encodeURIComponent(spaceId)}/sources/${encodeURIComponent(src.id)}/secrets`,
    ),
  );
  for (const line of key.body) expect(report).not.toContain(line.slice(0, 24));
});
