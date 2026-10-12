import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { Capability, CapabilityConfigUpdate, Field } from "@/api/types";
import { config, groundingCapability } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { CapabilityCard } from "./CapabilityCard";
import { draftFromCapability } from "./draft";

/** The grounding card as the Phase 3 manifest sends it: on/off plus the code judge toggle. */
const ENABLED: Field = {
  name: "enabled",
  label: "Check every sentence against its sources",
  type: "toggle",
  required: false,
};
const CODE_JUDGE: Field = {
  name: "code_judge",
  label: "Check code claims with the chat model",
  type: "toggle",
  required: false,
};

function grounding(over: Partial<Capability> = {}, judge?: boolean | null): Capability {
  return groundingCapability({
    status: "ready",
    fields: [ENABLED, CODE_JUDGE],
    config: { ...config({ enabled: true }), ...(judge === undefined ? {} : { code_judge: judge }) },
    ...over,
  });
}

describe("grounding card: code judge (D-045)", () => {
  it("renders the toggle from the manifest with its help text, on by default", () => {
    renderWithProviders(<CapabilityCard capability={grounding()} />);
    const card = screen.getByTestId("capability-card-grounding");
    const toggle = within(card).getByRole("switch", {
      name: "Check code claims with the chat model",
    });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(toggle).toHaveAccessibleDescription(/chat model compares it with the cited lines/);
  });

  it("prefers the manifest's help text and follows the saved value", () => {
    renderWithProviders(
      <CapabilityCard
        capability={grounding(
          { fields: [ENABLED, { ...CODE_JUDGE, help: "Sends cited code to your chat model." }] },
          false,
        )}
      />,
    );
    const toggle = screen.getByRole("switch", { name: "Check code claims with the chat model" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(toggle).toHaveAccessibleDescription("Sends cited code to your chat model.");
  });

  it("defaults only the code judge to on when the backend sends no value", () => {
    const cap = grounding({
      fields: [
        ENABLED,
        CODE_JUDGE,
        { name: "other", label: "Other", type: "toggle", required: false },
      ],
    });
    expect(draftFromCapability(cap).values).toMatchObject({
      enabled: true,
      code_judge: true,
      other: false,
    });
    expect(draftFromCapability(grounding({}, null)).values.code_judge).toBe(true);
    expect(draftFromCapability(grounding({}, false)).values.code_judge).toBe(false);
  });

  it("saves the toggle", async () => {
    const user = userEvent.setup();
    const puts: CapabilityConfigUpdate[] = [];
    server.use(
      http.put("*/api/capabilities/grounding", async ({ request }) => {
        const body = (await request.json()) as CapabilityConfigUpdate;
        puts.push(body);
        return HttpResponse.json(grounding({}, body.code_judge));
      }),
    );
    renderWithProviders(<CapabilityCard capability={grounding({}, true)} />);
    const card = screen.getByTestId("capability-card-grounding");
    const save = within(card).getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();

    await user.click(within(card).getByRole("switch", { name: /check code claims/i }));
    expect(save).toBeEnabled();
    await user.click(save);
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({ enabled: true, code_judge: false });
    expect(within(card).getByRole("switch", { name: /check code claims/i })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("greys the code judge out while answer checking is off", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CapabilityCard capability={grounding()} />);
    const card = screen.getByTestId("capability-card-grounding");
    const judge = within(card).getByRole("switch", { name: /check code claims/i });
    expect(judge).toBeEnabled();
    await user.click(within(card).getByRole("switch", { name: "On" }));
    expect(judge).toBeDisabled();
    expect(within(card).getByText(/marked "Unverified"/)).toBeInTheDocument();
  });
});
