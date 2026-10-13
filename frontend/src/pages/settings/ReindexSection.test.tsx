import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { ReindexStatus } from "@/api/types";
import { reindexStatus, space } from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const ACME = space({ id: "sp_1", slug: "acme-audit", name: "Acme audit" });
const NOTES = space({ id: "sp_2", slug: "notes", name: "Team notes" });
const DOCS = space({ id: "sp_3", slug: "docs", name: "Product docs" });

const OLD = [
  { id: "src_1", title: "Requests docs", reindexing: false },
  { id: "src_2", title: "Handbook", reindexing: false },
];

/** Spaces with their re-index status; POST marks a space's sources as re-indexing. */
function backend(statuses: Record<string, ReindexStatus | number>) {
  const posts: string[] = [];
  const state = { ...statuses };
  server.use(
    http.get("*/api/spaces", () => HttpResponse.json([ACME, NOTES, DOCS])),
    http.get("*/api/spaces/:sid/reindex", ({ params }) => {
      const s = state[String(params.sid)] ?? reindexStatus();
      return typeof s === "number"
        ? HttpResponse.json({ detail: "Database is locked" }, { status: s })
        : HttpResponse.json(s);
    }),
    http.post("*/api/spaces/:sid/reindex", ({ params }) => {
      const sid = String(params.sid);
      posts.push(sid);
      const s = state[sid];
      const next: ReindexStatus = {
        ...(typeof s === "object" ? s : reindexStatus()),
        reindexing: true,
        estimate_seconds: null,
        estimate_summary: null,
        confirm: false,
      };
      next.sources = next.sources.map((x) => ({ ...x, reindexing: true }));
      state[sid] = next;
      return HttpResponse.json(next, { status: 202 });
    }),
  );
  return { posts };
}

async function section() {
  const heading = await screen.findByRole("heading", { name: "Re-index outdated spaces" });
  return heading.closest("section") as HTMLElement;
}

describe("settings: re-index outdated spaces (D-114)", () => {
  it("says all spaces are up to date when none needs it", async () => {
    backend({});
    renderApp("/settings");
    const s = await section();
    expect(await within(s).findByText("All spaces are up to date.")).toBeInTheDocument();
    expect(within(s).queryByRole("button", { name: /Re-index/ })).not.toBeInTheDocument();
  });

  it("lists only outdated spaces and re-indexes the one clicked", async () => {
    const { posts } = backend({
      sp_1: reindexStatus({
        needed: true,
        sources: OLD,
        estimate_seconds: 40,
        estimate_summary: "Under a minute on this PC",
      }),
      sp_3: reindexStatus({
        needed: true,
        sources: [OLD[0]!],
        estimate_seconds: 20,
        estimate_summary: "Under a minute on this PC",
      }),
    });
    const user = userEvent.setup();
    renderApp("/settings");
    const s = await section();
    const acme = await within(s).findByTestId("reindex-space-sp_1");
    expect(acme).toHaveTextContent("Acme audit");
    expect(acme).toHaveTextContent("2 sources need it. Under a minute on this PC.");
    expect(within(acme).getByRole("link", { name: "Acme audit" })).toHaveAttribute(
      "href",
      "/spaces/sp_1",
    );
    expect(await within(s).findByTestId("reindex-space-sp_3")).toHaveTextContent(
      "1 source needs it.",
    );
    expect(within(s).queryByText("Team notes")).not.toBeInTheDocument();
    expect(within(s).queryByText("All spaces are up to date.")).not.toBeInTheDocument();

    await user.click(within(acme).getByRole("button", { name: "Re-index Acme audit" }));
    await waitFor(() => expect(posts).toEqual(["sp_1"]));
    expect(await within(acme).findByText(/Re-indexing…/)).toBeInTheDocument();
    expect(within(acme).getByRole("link", { name: "See progress in the queue" })).toHaveAttribute(
      "href",
      "/queue",
    );
    expect(within(acme).queryByRole("button", { name: /Re-index/ })).not.toBeInTheDocument();
    // The other space is untouched.
    const docs = within(s).getByTestId("reindex-space-sp_3");
    expect(within(docs).getByRole("button", { name: "Re-index Product docs" })).toBeEnabled();
  });

  it("asks first when re-indexing a space takes more than two minutes", async () => {
    const { posts } = backend({
      sp_2: reindexStatus({
        needed: true,
        sources: OLD,
        estimate_seconds: 900,
        estimate_summary: "About 15 min on this PC",
        confirm: true,
      }),
    });
    const user = userEvent.setup();
    renderApp("/settings");
    const row = await within(await section()).findByTestId("reindex-space-sp_2");
    await user.click(within(row).getByRole("button", { name: "Re-index Team notes" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByTestId("estimate-summary")).toHaveTextContent(
      "About 15 min on this PC",
    );
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(posts).toEqual([]);
    await user.click(within(row).getByRole("button", { name: "Re-index Team notes" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Re-index" }),
    );
    await waitFor(() => expect(posts).toEqual(["sp_2"]));
  });

  it("shows a re-index that is already running", async () => {
    backend({
      sp_1: reindexStatus({
        needed: true,
        reindexing: true,
        sources: [{ ...OLD[0]!, reindexing: true }, OLD[1]!],
      }),
    });
    renderApp("/settings");
    const row = await within(await section()).findByTestId("reindex-space-sp_1");
    expect(row).toHaveTextContent("Re-indexing… See progress in the queue.");
    expect(within(row).queryByRole("button", { name: /Re-index/ })).not.toBeInTheDocument();
  });

  it("says when a space could not be checked instead of calling everything up to date", async () => {
    backend({ sp_2: 500 });
    renderApp("/settings");
    const s = await section();
    expect(await within(s).findByText("Could not check every space")).toBeInTheDocument();
    expect(within(s).queryByText("All spaces are up to date.")).not.toBeInTheDocument();
  });
});
