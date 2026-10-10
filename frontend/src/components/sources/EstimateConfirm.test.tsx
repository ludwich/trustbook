import { screen, waitFor, within } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { BrowseListing, SourceEstimate } from "@/api/types";
import { renderApp } from "@/test/render";
import { spaceBackend } from "@/test/spaceBackend";

const SPACE = "/spaces/sp_1";
const BIG: SourceEstimate = {
  seconds: 1500,
  files: 4200,
  summary: "About 4,200 files, roughly 25 min on this PC",
  confirm: true,
};
const SMALL: SourceEstimate = {
  seconds: 20,
  files: 3,
  summary: "About 3 files, less than a minute on this PC",
  confirm: false,
};

async function openAdd(user: UserEvent, item: RegExp) {
  await user.click(await screen.findByRole("button", { name: "Add source" }));
  await user.click(await screen.findByRole("menuitem", { name: item }));
  return screen.findByRole("dialog");
}

function listing(path: string, parent: string | null, dirs: string[]): BrowseListing {
  return {
    path,
    parent,
    entries: dirs.map((n) => ({
      name: n,
      path: path ? `${path}/${n}` : n,
      is_dir: true,
      size: null,
    })),
  };
}

describe("estimates before big adds", () => {
  it("asks before a long upload; Cancel adds nothing, Add uploads", async () => {
    const user = userEvent.setup();
    const { state } = spaceBackend({ estimate: () => BIG });
    renderApp(SPACE);

    const dialog = await openAdd(user, /upload files/i);
    const file = new File(["x".repeat(2048)], "archive.pdf", { type: "application/pdf" });
    await user.upload(within(dialog).getByLabelText("Choose files"), file);
    await user.click(within(dialog).getByRole("button", { name: "Add 1 file" }));

    const confirm = await screen.findByRole("alertdialog");
    expect(within(confirm).getByText("This will take a while")).toBeInTheDocument();
    expect(within(confirm).getByTestId("estimate-summary")).toHaveTextContent(BIG.summary);
    expect(state.calls.estimate).toEqual([
      [{ kind: "upload", files: [{ name: "archive.pdf", bytes: 2048 }] }],
    ]);

    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(state.calls.upload).toHaveLength(0);
    // The upload dialog stays open with the file still listed.
    expect(screen.getByRole("dialog")).toHaveTextContent("archive.pdf");

    await user.click(screen.getByRole("button", { name: "Add 1 file" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Add" }),
    );
    await waitFor(() => expect(state.calls.upload).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("adds at once when the estimate is short", async () => {
    const user = userEvent.setup();
    const { state } = spaceBackend({ estimate: () => SMALL });
    renderApp(SPACE);

    const dialog = await openAdd(user, /upload files/i);
    await user.upload(within(dialog).getByLabelText("Choose files"), new File(["a"], "a.txt"));
    await user.click(within(dialog).getByRole("button", { name: "Add 1 file" }));

    await waitFor(() => expect(state.calls.upload).toHaveLength(1));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(state.calls.estimate).toHaveLength(1);
  });

  it("shows the folder estimate in the picker footer and confirms before linking", async () => {
    const user = userEvent.setup();
    const { state } = spaceBackend({
      estimate: () => BIG,
      browse: {
        "": listing("", null, ["projects"]),
        projects: listing("projects", "", ["monorepo"]),
        "projects/monorepo": listing("projects/monorepo", "projects", []),
      },
    });
    renderApp(SPACE);

    const dialog = await openAdd(user, /link folder/i);
    await user.click(await within(dialog).findByRole("button", { name: "projects" }));
    await user.click(await within(dialog).findByRole("button", { name: "monorepo" }));
    await within(dialog).findByText("Selected: projects/monorepo");

    // Debounced: only the folder the user stopped at is estimated, never the home folder.
    const footer = await within(dialog).findByTestId("folder-estimate");
    await waitFor(() => expect(footer).toHaveTextContent(BIG.summary));
    expect(state.calls.estimate).toEqual([[{ kind: "folder", path: "projects/monorepo" }]]);

    await user.click(within(dialog).getByRole("button", { name: "Link this folder" }));
    const confirm = await screen.findByRole("alertdialog");
    expect(confirm).toHaveTextContent(BIG.summary);
    // The footer's estimate is reused, not asked for again.
    expect(state.calls.estimate).toHaveLength(1);

    await user.click(within(confirm).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(state.calls.folder).toEqual([{ path: "projects/monorepo" }]));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("asks before adding many search results", async () => {
    const user = userEvent.setup();
    const { state } = spaceBackend({
      estimate: () => BIG,
      searchResults: [
        { title: "Page one", url: "https://a.example/1", snippet: "" },
        { title: "Page two", url: "https://a.example/2", snippet: "" },
      ],
    });
    renderApp(SPACE);

    const dialog = await openAdd(user, /search the web/i);
    await user.type(within(dialog).getByLabelText("Search for"), "pages");
    await user.click(within(dialog).getByRole("button", { name: "Search" }));
    const results = await within(dialog).findByRole("list", { name: "Search results" });
    await user.click(within(results).getByRole("checkbox", { name: /Page one/ }));
    await user.click(within(results).getByRole("checkbox", { name: /Page two/ }));
    await user.click(within(dialog).getByRole("button", { name: "Add 2 sources" }));

    const confirm = await screen.findByRole("alertdialog");
    expect(state.calls.estimate).toEqual([[{ kind: "url", count: 2 }]]);
    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(state.calls.fromSearch).toHaveLength(0);
  });
});
