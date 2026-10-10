import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { SOURCES_POLL_BUSY_MS, SOURCES_POLL_IDLE_MS, sourcesPollInterval } from "@/api/queries";
import { groupChecked, visibleQualityWarning } from "./sourceMeta";
import { source } from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";
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
    expect(
      within(code).getByRole("button", { name: "2 files skipped: possible secrets" }),
    ).toBeInTheDocument();
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

describe("skipped secrets (G4)", () => {
  function codeSource() {
    return source({
      id: "c1",
      type: "folder",
      group: "code",
      title: "billing-service",
      stats: { files: 40, chunks: 210, skipped_secrets: 2 },
      // The backend also puts the sentence in the quality warning; it must show only once.
      quality_warning:
        "2 files skipped: possible secrets. Some text was read from images (OCR) and may contain errors.",
    });
  }

  it("shows the warning once, as a button, and keeps other warnings", async () => {
    spaceBackend({ sources: [codeSource()] });
    renderApp(SPACE);
    const row = await screen.findByTestId("source-c1");
    expect(within(row).getAllByText(/skipped: possible secrets/)).toHaveLength(1);
    expect(
      within(row).getByRole("button", { name: "2 files skipped: possible secrets" }),
    ).toBeInTheDocument();
    expect(within(row).getByText(/^Some text was read from images \(OCR\)/)).toBeInTheDocument();
  });

  it("opens a report of file, line and rule ID", async () => {
    const user = userEvent.setup();
    const { state } = spaceBackend({
      sources: [codeSource()],
      secrets: {
        c1: {
          files_skipped: 2,
          findings: [
            { file: "config/prod.env", line: 3, rule_id: "generic-api-key" },
            { file: "src/billing/stripe_keys.py", line: 12, rule_id: "stripe-access-token" },
          ],
        },
      },
    });
    renderApp(SPACE);
    // Not fetched until asked for.
    await screen.findByTestId("source-c1");
    expect(state.calls.secrets).toEqual([]);

    await user.click(screen.getByRole("button", { name: "2 files skipped: possible secrets" }));
    const dialog = await screen.findByRole("dialog", { name: "Files skipped: possible secrets" });
    expect(
      within(dialog).getByText(
        "These files were not indexed. Secret values are never stored or shown.",
      ),
    ).toBeInTheDocument();
    const table = await within(dialog).findByRole("table", { name: "Possible secrets" });
    const rows = within(table).getAllByRole("row");
    expect(rows.map((r) => r.textContent)).toEqual([
      "FileLineRule",
      "config/prod.env3generic-api-key",
      "src/billing/stripe_keys.py12stripe-access-token",
    ]);
    expect(within(table).getByText("config/prod.env")).toHaveClass("font-mono");
    expect(state.calls.secrets).toEqual(["c1"]);
  });

  it("explains when there is no report", async () => {
    const user = userEvent.setup();
    spaceBackend({ sources: [codeSource()] });
    renderApp(SPACE);
    await user.click(
      await screen.findByRole("button", { name: "2 files skipped: possible secrets" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/No report is available/)).toBeInTheDocument();
  });

  it("drops only the secrets sentence from the quality warning", () => {
    const s = codeSource();
    expect(visibleQualityWarning(s)).toBe(
      "Some text was read from images (OCR) and may contain errors.",
    );
    expect(
      visibleQualityWarning({ ...s, quality_warning: "1 file skipped: possible secrets." }),
    ).toBeNull();
    // Without a count in stats there is no button, so the warning stays as sent.
    expect(
      visibleQualityWarning({
        ...s,
        stats: {},
        quality_warning: "3 files skipped: possible secrets.",
      }),
    ).toBe("3 files skipped: possible secrets.");
    expect(visibleQualityWarning({ ...s, quality_warning: null })).toBeNull();
  });
});

describe("failed sources (G7)", () => {
  function sourcesWithFailure() {
    return [
      source({ id: "d1", title: "Contract.pdf", enabled: true }),
      source({ id: "d2", title: "Minutes.docx", enabled: true }),
      // An older backend may still say enabled: true for a failed source.
      source({
        id: "f1",
        title: "Scan.pdf",
        status: "failed",
        enabled: true,
        failure_reason: "This PDF has no text, only images.",
      }),
      source({
        id: "f2",
        type: "url",
        group: "web",
        title: "Blocked page",
        status: "failed",
        enabled: false,
      }),
    ];
  }

  it("shows failed sources unticked and disabled with a tooltip", async () => {
    spaceBackend({ sources: sourcesWithFailure() });
    renderApp(SPACE);
    const box = await screen.findByRole("checkbox", { name: "Use Scan.pdf" });
    expect(box).not.toBeChecked();
    expect(box).toBeDisabled();
    expect(box).toHaveAttribute("title", "This source failed to load");
    expect(box.parentElement).toHaveAttribute("title", "This source failed to load");
    expect(screen.getByRole("checkbox", { name: "Use Contract.pdf" })).toBeEnabled();
  });

  it("leaves failed sources out of group state, group toggles and counts", async () => {
    const user = userEvent.setup();
    const { state } = spaceBackend({ sources: sourcesWithFailure() });
    renderApp(SPACE);

    const docs = await screen.findByRole("checkbox", { name: "Use all documents" });
    // Both usable documents are ticked → all, even though Scan.pdf is not.
    expect(docs).toBeChecked();
    expect(docs).not.toBePartiallyChecked();
    expect(screen.getByTestId("group-count-documents")).toHaveTextContent("2");
    expect(screen.getByTestId("sources-count")).toHaveTextContent("2");

    // A group with only failed sources: no count, and its checkbox can't be used.
    expect(screen.queryByTestId("group-count-web")).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Use all web" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Use all web" })).not.toBeChecked();

    // The group toggle sends only the usable sources.
    await user.click(docs);
    await waitFor(() =>
      expect(state.calls.enabled).toEqual([{ source_ids: ["d1", "d2"], enabled: false }]),
    );
    await waitFor(() => expect(docs).not.toBeChecked());
    expect(screen.getByRole("checkbox", { name: "Use Scan.pdf" })).not.toBeChecked();
  });

  it("computes group state over usable sources only", () => {
    const ok = source({ id: "a", enabled: false });
    const failedOn = source({ id: "b", status: "failed", enabled: true });
    expect(groupChecked([ok, failedOn])).toBe(false);
    expect(groupChecked([{ ...ok, enabled: true }, failedOn])).toBe(true);
    expect(groupChecked([failedOn])).toBe(false);
    expect(
      groupChecked([{ ...ok, enabled: true }, source({ id: "c", enabled: false }), failedOn]),
    ).toBe("mixed");
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

  it("changes ticks at once, before the server answers (source and group)", async () => {
    const user = userEvent.setup();
    spaceBackend({
      sources: [
        source({ id: "s1", title: "Contract.pdf" }),
        source({ id: "s2", title: "Minutes.docx" }),
      ],
    });
    // Hold every write until released, like a slow server.
    let release: () => void = () => {};
    const held = new Promise<void>((r) => (release = r));
    const slow = async () => {
      await held;
      return HttpResponse.json([]);
    };
    server.use(
      http.patch("*/api/spaces/:sid/sources/:id", slow),
      http.put("*/api/spaces/:sid/sources/enabled", slow),
    );
    renderApp(SPACE);

    const box = await screen.findByRole("checkbox", { name: "Use Contract.pdf" });
    const group = screen.getByRole("checkbox", { name: "Use all documents" });
    await user.click(box);
    // No waitFor: the checkbox must not snap back while the request is open.
    expect(box).not.toBeChecked();
    expect(group).toBePartiallyChecked();

    await user.click(group);
    expect(group).toBeChecked();
    expect(box).toBeChecked();
    release();
  });

  it("puts the tick back and explains when the server refuses it", async () => {
    const user = userEvent.setup();
    spaceBackend({ sources: [source({ id: "s1", title: "Contract.pdf" })] });
    server.use(
      http.patch("*/api/spaces/:sid/sources/:id", () =>
        HttpResponse.json({ detail: "The source is busy." }, { status: 409 }),
      ),
    );
    renderApp(SPACE);

    const box = await screen.findByRole("checkbox", { name: "Use Contract.pdf" });
    await user.click(box);
    expect(await screen.findByText("The source is busy.")).toBeInTheDocument();
    await waitFor(() => expect(box).toBeChecked());
  });
});
