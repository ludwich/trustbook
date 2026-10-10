import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SOURCES_POLL_BUSY_MS, SOURCES_POLL_IDLE_MS, sourcesPollInterval } from "@/api/queries";
import { source } from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { spaceBackend } from "@/test/spaceBackend";

const SPACE = "/spaces/sp_1";

function mixedSources() {
  return [
    source({ id: "d1", title: "Contract.pdf", enabled: true, stats: { pages: 12, chunks: 30 } }),
    source({
      id: "w1",
      type: "url",
      group: "web",
      title: "Vendor pricing page",
      origin: "https://example.com/pricing",
      snapshot_at: "2026-10-02T10:00:00Z",
    }),
    source({ id: "d2", title: "Minutes.docx", enabled: false }),
    source({
      id: "t1",
      type: "youtube",
      group: "transcripts",
      title: "Kick-off call",
      stats: { duration_s: 754 },
    }),
  ];
}

describe("sources panel", () => {
  it("groups sources in a fixed order with a heading and count per group", async () => {
    spaceBackend({ sources: mixedSources() });
    renderApp(SPACE);
    const panel = await screen.findByRole("complementary", { name: "Sources" });
    await within(panel).findByText("Contract.pdf");

    const headings = within(panel)
      .getAllByRole("heading", { level: 3 })
      .map((h) => h.textContent);
    expect(headings).toEqual(["Documents", "Transcripts", "Web"]);

    const docs = within(screen.getByTestId("group-documents"));
    expect(docs.getByText("Contract.pdf")).toBeInTheDocument();
    expect(docs.getByText("Minutes.docx")).toBeInTheDocument();
    expect(docs.getByText("12 pages · 30 passages")).toBeInTheDocument();
    expect(within(screen.getByTestId("group-transcripts")).getByText("12:34")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("group-web")).getByText(/Snapshot 2 Oct 2026/),
    ).toBeInTheDocument();
  });

  it("shows a tri-state group checkbox that ticks and unticks the whole group", async () => {
    const user = userEvent.setup();
    const { state } = spaceBackend({ sources: mixedSources() });
    renderApp(SPACE);

    const group = await screen.findByRole("checkbox", { name: "Use all documents" });
    expect(group).toBePartiallyChecked();
    expect(screen.getByRole("checkbox", { name: "Use all web" })).toBeChecked();

    // Some ticked → ticks all.
    await user.click(group);
    await waitFor(() => expect(group).toBeChecked());
    expect(state.calls.enabled).toEqual([{ source_ids: ["d1", "d2"], enabled: true }]);
    expect(screen.getByRole("checkbox", { name: "Use Minutes.docx" })).toBeChecked();

    // All ticked → unticks all.
    await user.click(group);
    await waitFor(() => expect(group).not.toBeChecked());
    expect(group).not.toBePartiallyChecked();
    expect(state.calls.enabled[1]).toEqual({ source_ids: ["d1", "d2"], enabled: false });

    // One source on its own → PATCH, and the group becomes mixed.
    await user.click(screen.getByRole("checkbox", { name: "Use Contract.pdf" }));
    await waitFor(() => expect(group).toBePartiallyChecked());
    expect(state.calls.patch).toEqual([{ id: "d1", body: { enabled: true } }]);
  });

  it("shows why a source failed and what to do next", async () => {
    spaceBackend({
      sources: [
        source({
          id: "f1",
          title: "Scan.pdf",
          status: "failed",
          failure_reason: "This PDF has no text, only images.",
          failure_next_step: "Run it through OCR and upload it again.",
        }),
      ],
    });
    renderApp(SPACE);
    const row = await screen.findByTestId("source-f1");
    expect(row).toHaveAttribute("data-source-status", "failed");
    expect(within(row).getByText("This PDF has no text, only images.")).toBeInTheDocument();
    expect(within(row).getByText("Run it through OCR and upload it again.")).toBeInTheDocument();
    expect(within(row).queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("shows progress, quality warnings and skipped secrets", async () => {
    spaceBackend({
      sources: [
        source({ id: "q1", title: "Waiting.pdf", status: "queued" }),
        source({ id: "r1", title: "Big.pdf", status: "indexing", progress: 0.4 }),
        source({
          id: "a1",
          title: "Call.m4a",
          group: "transcripts",
          quality_warning: "Transcribed by the built-in model. Check names and numbers.",
        }),
        source({
          id: "c1",
          type: "folder",
          group: "code",
          title: "billing-service",
          commit_hash: "a1b2c3d4e5f6",
          stats: { files: 40, chunks: 210, skipped_secrets: 2 },
        }),
      ],
    });
    renderApp(SPACE);

    const queued = await screen.findByTestId("source-q1");
    expect(within(queued).getByText("Waiting in the queue")).toBeInTheDocument();

    const busy = screen.getByTestId("source-r1");
    expect(within(busy).getByText("Indexing…")).toBeInTheDocument();
    expect(within(busy).getByRole("progressbar", { name: "Indexing Big.pdf" })).toHaveAttribute(
      "aria-valuenow",
      "40",
    );

    expect(
      within(screen.getByTestId("source-a1")).getByText(/Transcribed by the built-in model/),
    ).toBeInTheDocument();

    const code = screen.getByTestId("source-c1");
    expect(within(code).getByText("2 files skipped: possible secrets")).toBeInTheDocument();
    expect(within(code).getByText("40 files · 210 passages")).toBeInTheDocument();
    expect(within(code).getByText("a1b2c3d")).toBeInTheDocument();
  });

  it("refreshes a web page and removes a source after confirming", async () => {
    const user = userEvent.setup();
    const { state } = spaceBackend({ sources: mixedSources() });
    renderApp(SPACE);

    await user.click(await screen.findByRole("button", { name: "Refresh Vendor pricing page" }));
    await waitFor(() => expect(state.calls.refresh).toEqual(["w1"]));
    // Uploaded files and pasted text have nothing to refresh from.
    expect(screen.queryByRole("button", { name: "Refresh Contract.pdf" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Remove Minutes.docx" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Remove “Minutes.docx”?")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(state.calls.deleted).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Remove Minutes.docx" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Remove source" }),
    );
    await waitFor(() => expect(state.calls.deleted).toEqual(["d2"]));
    await waitFor(() => expect(screen.queryByText("Minutes.docx")).not.toBeInTheDocument());
  });
});

describe("sources polling", () => {
  it("polls fast while a source is being processed and slowly otherwise", () => {
    expect(sourcesPollInterval(undefined)).toBe(SOURCES_POLL_IDLE_MS);
    expect(sourcesPollInterval([source({ id: "a" })])).toBe(SOURCES_POLL_IDLE_MS);
    expect(sourcesPollInterval([source({ id: "a", status: "failed" })])).toBe(SOURCES_POLL_IDLE_MS);
    for (const status of ["queued", "reading", "indexing"] as const) {
      expect(sourcesPollInterval([source({ id: "a" }), source({ id: "b", status })])).toBe(
        SOURCES_POLL_BUSY_MS,
      );
    }
    expect(SOURCES_POLL_BUSY_MS).toBe(2_000);
    expect(SOURCES_POLL_IDLE_MS).toBe(15_000);
  });
});
