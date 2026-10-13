import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ReindexSource } from "@/api/types";
import { reindexStatus, source } from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { spaceBackend } from "@/test/spaceBackend";

const SPACE = "/spaces/sp_1";
const DOCS: ReindexSource = { id: "src_1", title: "Requests docs", reindexing: false };
const HANDBOOK: ReindexSource = { id: "src_2", title: "Handbook", reindexing: false };
const OLD = [DOCS, HANDBOOK];

describe("re-index banner (G4-1, D-106)", () => {
  it("is not shown when the space is up to date", async () => {
    spaceBackend({ sources: [source({ id: "src_1", title: "Requests docs" })] });
    renderApp(SPACE);
    await screen.findByText("Requests docs");
    expect(screen.queryByTestId("reindex-banner")).not.toBeInTheDocument();
  });

  it("says the space needs re-indexing and starts it through the queue", async () => {
    const backend = spaceBackend({
      sources: [source({ id: "src_1", title: "Requests docs" })],
      reindex: reindexStatus({
        needed: true,
        sources: OLD,
        estimate_seconds: 40,
        estimate_summary: "Under a minute on this PC",
      }),
    });
    const user = userEvent.setup();
    renderApp(SPACE);
    const banner = await screen.findByTestId("reindex-banner");
    expect(banner).toHaveTextContent("This space was indexed with an older version.");
    expect(banner).toHaveTextContent(
      "Re-index to get accurate section labels. 2 sources need it. Under a minute on this PC. " +
        "Chat keeps working meanwhile.",
    );
    await user.click(within(banner).getByRole("button", { name: "Re-index" }));
    await waitFor(() => expect(backend.state.calls.reindex).toBe(1));
    const running = await screen.findByText("Re-indexing this space…");
    const alert = running.closest("[data-testid=reindex-banner]") as HTMLElement;
    expect(alert).toHaveTextContent("Chat keeps using the current index until it is done.");
    expect(within(alert).getByRole("link", { name: "See progress in the queue" })).toHaveAttribute(
      "href",
      "/queue",
    );
    expect(within(alert).queryByRole("button", { name: "Re-index" })).not.toBeInTheDocument();
  });

  it("asks first when re-indexing takes more than two minutes", async () => {
    const backend = spaceBackend({
      reindex: reindexStatus({
        needed: true,
        sources: OLD,
        estimate_seconds: 900,
        estimate_summary: "About 15 min on this PC",
        confirm: true,
      }),
    });
    const user = userEvent.setup();
    renderApp(SPACE);
    const banner = await screen.findByTestId("reindex-banner");
    await user.click(within(banner).getByRole("button", { name: "Re-index" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByTestId("estimate-summary")).toHaveTextContent(
      "About 15 min on this PC",
    );
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(backend.state.calls.reindex).toBe(0);
    await user.click(within(banner).getByRole("button", { name: "Re-index" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Re-index" }),
    );
    await waitFor(() => expect(backend.state.calls.reindex).toBe(1));
  });

  it("shows a re-index that is already running", async () => {
    spaceBackend({
      reindex: reindexStatus({
        needed: true,
        reindexing: true,
        sources: [{ ...DOCS, reindexing: true }, HANDBOOK],
      }),
    });
    renderApp(SPACE);
    const banner = await screen.findByTestId("reindex-banner");
    expect(banner).toHaveTextContent("Re-indexing this space…");
    expect(within(banner).queryByRole("button", { name: "Re-index" })).not.toBeInTheDocument();
  });
});
