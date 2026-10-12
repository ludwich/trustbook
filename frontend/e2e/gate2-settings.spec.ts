import { expect, test } from "@playwright/test";
import { capabilityConfig, getJson, prepareBackend, putCapability } from "./helpers";

/**
 * Gate 2 UI check #9 (power labels) and the Phase 3 code-judge setting on the grounding card.
 * Neither changes the power level; the code-judge toggle is put back in a finally block.
 */

test.beforeAll(async ({ request }) => {
  test.setTimeout(600_000);
  await prepareBackend(request);
});

test("Gate 2 #9: Settings → Power shows High with the real worker count, at most 8", async ({
  page,
  request,
}) => {
  const settings = await getJson<{ workers_by_power?: { high?: number } }>(
    request,
    "/api/settings",
  );
  await page.goto("/settings");
  const power = page.getByRole("radiogroup", { name: "Processing power" });
  await expect(power).toBeVisible();
  const high = power.getByTestId("power-workers-high");
  await expect(high).toHaveText(/^· \d+ workers?$/);
  const n = Number(/(\d+)/.exec((await high.textContent()) ?? "")?.[1]);
  expect(n).toBeGreaterThanOrEqual(1);
  expect(n).toBeLessThanOrEqual(8);
  if (settings.workers_by_power?.high !== undefined) expect(n).toBe(settings.workers_by_power.high);
  // Low and Normal carry numbers too.
  await expect(power.getByTestId("power-workers-low")).toHaveText(/^· \d+ workers?$/);
  await expect(power.getByTestId("power-workers-normal")).toHaveText(/^· \d+ workers?$/);
});

test("Phase 3: the grounding card's code-judge toggle renders, explains itself and saves", async ({
  page,
  request,
}) => {
  const before = await capabilityConfig(request, "grounding");
  try {
    await page.goto("/settings");
    const card = page.getByTestId("capability-card-grounding");
    const judge = card.getByRole("switch", { name: "Check code claims with the chat model" });
    await expect(judge).toBeVisible();
    await expect(judge).toHaveAccessibleDescription(/chat model/i);
    const was = (await judge.getAttribute("aria-checked")) === "true";

    await judge.click();
    await expect(judge).toHaveAttribute("aria-checked", String(!was));
    await card.getByRole("button", { name: "Save" }).click();
    await expect(card.getByRole("button", { name: "Save" })).toBeDisabled();
    await expect
      .poll(async () => (await capabilityConfig(request, "grounding")).code_judge)
      .toBe(!was);

    // Stored, not just shown: a reload keeps it.
    await page.reload();
    await expect(
      page
        .getByTestId("capability-card-grounding")
        .getByRole("switch", { name: "Check code claims with the chat model" }),
    ).toHaveAttribute("aria-checked", String(!was));
  } finally {
    await putCapability(request, "grounding", {
      enabled: before.enabled,
      ...(before.code_judge === undefined ? {} : { code_judge: before.code_judge ?? true }),
    });
  }
});
