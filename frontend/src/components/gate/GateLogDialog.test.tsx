import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { toGateLog } from "@/api/client";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";
import { spaceBackend } from "@/test/spaceBackend";

const SPACE = "/spaces/sp_1";

// Raw rows as the backend stores them (DB column names).
const LOG = [
  {
    id: 7,
    gate: "chat",
    created: "2026-10-10T09:15:00Z",
    input: "hello there",
    decision: "chitchat",
    confidence: 0.93,
    user_correction: null,
  },
  {
    id: 6,
    gate: "chat",
    created: "2026-10-10T09:10:00Z",
    input: "Give me ideas for a launch party",
    decision: "brainstorm",
    confidence: 0.71,
    user_correction: null,
  },
  {
    id: 5,
    gate: "chat",
    created: "2026-10-10T09:05:00Z",
    input: "What does clause 4 say?",
    decision: "sources",
    confidence: null,
    user_correction: "sources",
  },
];

async function openGateLog() {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Space menu" }));
  await user.click(await screen.findByRole("menuitem", { name: "Gate log" }));
  const dialog = await screen.findByRole("dialog", { name: "Gate log" });
  return { user, dialog };
}

describe("gate log", () => {
  it("lists time, question, decision, confidence and correction from the space menu", async () => {
    spaceBackend({ gateLog: LOG });
    renderApp(SPACE);
    const { dialog } = await openGateLog();

    const table = await within(dialog).findByRole("table", { name: "Gate decisions" });
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((h) => h.textContent),
    ).toEqual(["Time", "Question", "Decision", "Confidence", "Correction"]);

    const row7 = within(dialog).getByTestId("gate-entry-7");
    expect(row7).toHaveTextContent("hello there");
    expect(row7).toHaveTextContent("Small talk");
    expect(row7).toHaveTextContent("93%");
    expect(row7).toHaveTextContent(/10 Oct/);

    const row6 = within(dialog).getByTestId("gate-entry-6");
    expect(row6).toHaveTextContent("Brainstorming");
    expect(row6).toHaveTextContent("71%");

    // Gate off or failing → "sources" with no confidence.
    const row5 = within(dialog).getByTestId("gate-entry-5");
    expect(row5).toHaveTextContent("Answer from sources");
    expect(row5).toHaveTextContent("Not checked");
    expect(within(row5).getByRole("combobox")).toHaveValue("sources");
    expect(within(row7).getByRole("combobox")).toHaveValue("");
  });

  it("corrects a decision", async () => {
    const { state } = spaceBackend({ gateLog: LOG });
    renderApp(SPACE);
    const { user, dialog } = await openGateLog();

    const select = await within(dialog).findByRole("combobox", {
      name: "Correct the decision for “Give me ideas for a launch party”",
    });
    await user.selectOptions(select, "sources");

    expect(select).toHaveValue("sources");
    await waitFor(() =>
      expect(state.calls.gateCorrect).toEqual([{ id: 6, body: { decision: "sources" } }]),
    );
    // After the refetch the stored correction is shown.
    await waitFor(() => expect(select).toHaveValue("sources"));
  });

  it("rolls back and explains when saving a correction fails", async () => {
    spaceBackend({ gateLog: LOG });
    server.use(
      http.post("*/api/spaces/:sid/gate-log/:id/correct", () =>
        HttpResponse.json({ detail: "Unknown decision." }, { status: 422 }),
      ),
    );
    renderApp(SPACE);
    const { user, dialog } = await openGateLog();

    const select = within(await within(dialog).findByTestId("gate-entry-7")).getByRole("combobox");
    await user.selectOptions(select, "sources");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Could not save the correction: Unknown decision.",
    );
    await waitFor(() => expect(select).toHaveValue(""));
  });

  it("says when there are no decisions yet", async () => {
    spaceBackend();
    renderApp(SPACE);
    const { dialog } = await openGateLog();
    expect(await within(dialog).findByText("No decisions yet")).toBeInTheDocument();
  });
});

describe("toGateLog", () => {
  it("accepts documented names, DB names, and a wrapped list; drops bad rows", () => {
    expect(
      toGateLog({
        entries: [
          {
            id: 1,
            created_at: "t",
            question: "q",
            decision: "brainstorm",
            confidence: 0.5,
            correction: "chitchat",
          },
          { id: 2, created: "t2", input: "q2", decision: "weird", confidence: "x" },
          { decision: "sources" },
        ],
      }),
    ).toEqual([
      {
        id: 1,
        created_at: "t",
        question: "q",
        decision: "brainstorm",
        confidence: 0.5,
        correction: "chitchat",
      },
      {
        id: 2,
        created_at: "t2",
        question: "q2",
        decision: "sources",
        confidence: null,
        correction: null,
      },
    ]);
    expect(toGateLog(null)).toEqual([]);
  });
});
