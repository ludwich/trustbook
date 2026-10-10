import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { Source } from "@/api/types";
import { source } from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";
import { spaceBackend } from "@/test/spaceBackend";
import { aheadText, refreshStatsText } from "./sourceMeta";

const SPACE = "/spaces/sp_1";
const INDEXED = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678";
const HEAD = "ffeeddccbbaa99887766554433221100ffeeddcc";

function folder(over: Partial<Source> = {}): Source {
  return source({
    id: "c1",
    type: "folder",
    group: "code",
    title: "billing-service",
    origin: "work/billing-service",
    commit_hash: INDEXED,
    stats: { files: 40, chunks: 210 },
    ...over,
  });
}

describe("folder commits (SPEC §7.5)", () => {
  it("says how many commits the folder is ahead of the index", () => {
    const ahead = { indexed_commit: INDEXED, head_commit: HEAD };
    expect(aheadText(folder({ ahead: { ...ahead, commits_ahead: 3 } }))).toBe(
      "Indexed at a1b2c3d, folder is 3 commits ahead.",
    );
    expect(aheadText(folder({ ahead: { ...ahead, commits_ahead: 1 } }))).toBe(
      "Indexed at a1b2c3d, folder is 1 commit ahead.",
    );
    // The graph can't always count (e.g. a rewritten history): no number then.
    expect(aheadText(folder({ ahead: { ...ahead, commits_ahead: null } }))).toBe(
      "Indexed at a1b2c3d, folder has new commits.",
    );
    expect(aheadText(folder({ ahead: null }))).toBeNull();
    expect(aheadText(folder())).toBeNull();
  });

  it("summarises the last refresh only when the backend reports it", () => {
    expect(refreshStatsText(folder({ stats: { changed_files: 3, unchanged_files: 1200 } }))).toBe(
      "3 files changed, 1,200 unchanged",
    );
    expect(refreshStatsText(folder({ stats: { changed_files: 1, unchanged_files: 0 } }))).toBe(
      "1 file changed, 0 unchanged",
    );
    expect(refreshStatsText(folder())).toBeNull();
    expect(
      refreshStatsText(source({ id: "p1", stats: { changed_files: 1, unchanged_files: 2 } })),
    ).toBeNull();
  });

  it("offers a refresh when the folder moved on, then shows what changed", async () => {
    const user = userEvent.setup();
    const { state } = spaceBackend({
      sources: [
        folder({ ahead: { indexed_commit: INDEXED, head_commit: HEAD, commits_ahead: 4 } }),
      ],
    });
    const refreshed: string[] = [];
    server.use(
      http.post("*/api/spaces/:sid/sources/:id/refresh", ({ params }) => {
        refreshed.push(String(params.id));
        const next = folder({
          commit_hash: HEAD,
          ahead: null,
          stats: { files: 41, chunks: 214, changed_files: 3, unchanged_files: 38 },
        });
        state.sources = [next];
        return HttpResponse.json(next);
      }),
    );
    renderApp(SPACE);

    const row = await screen.findByTestId("source-c1");
    const notice = within(row).getByTestId("source-ahead");
    expect(notice).toHaveTextContent("Indexed at a1b2c3d, folder is 4 commits ahead.");
    expect(within(row).queryByTestId("source-refresh-stats")).not.toBeInTheDocument();

    await user.click(within(notice).getByRole("button", { name: "Refresh?" }));
    await waitFor(() => expect(refreshed).toEqual(["c1"]));

    await waitFor(() => expect(within(row).queryByTestId("source-ahead")).not.toBeInTheDocument());
    expect(within(row).getByTestId("source-refresh-stats")).toHaveTextContent(
      "3 files changed, 38 unchanged",
    );
    expect(within(row).getByText("ffeeddc")).toBeInTheDocument();
  });

  it("shows no commit notice while the folder is being read", async () => {
    spaceBackend({
      sources: [
        folder({
          status: "indexing",
          progress: 0.5,
          ahead: { indexed_commit: INDEXED, head_commit: HEAD, commits_ahead: 2 },
          stats: { changed_files: 3, unchanged_files: 38 },
        }),
      ],
    });
    renderApp(SPACE);
    const row = await screen.findByTestId("source-c1");
    expect(within(row).queryByTestId("source-ahead")).not.toBeInTheDocument();
    expect(within(row).queryByTestId("source-refresh-stats")).not.toBeInTheDocument();
  });
});
