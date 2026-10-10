import { screen, waitFor, within } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { BrowseListing } from "@/api/types";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";
import { spaceBackend } from "@/test/spaceBackend";

const SPACE = "/spaces/sp_1";

async function openAdd(user: UserEvent, item: RegExp) {
  await user.click(await screen.findByRole("button", { name: "Add source" }));
  await user.click(await screen.findByRole("menuitem", { name: item }));
  return screen.findByRole("dialog");
}

describe("add source", () => {
  it("adds pasted text with a title and shows it in the list", async () => {
    const user = userEvent.setup();
    const { state } = spaceBackend();
    renderApp(SPACE);

    let dialog = await openAdd(user, /paste text/i);
    // Text is required.
    await user.click(within(dialog).getByRole("button", { name: "Add text" }));
    expect(within(dialog).getByText("Paste some text first.")).toBeInTheDocument();
    expect(state.calls.paste).toEqual([]);

    await user.type(within(dialog).getByLabelText(/title/i), "Workshop notes");
    await user.type(within(dialog).getByLabelText("Text"), "We agreed to ship in March.");
    await user.click(within(dialog).getByRole("button", { name: "Add text" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(state.calls.paste).toEqual([
      { title: "Workshop notes", text: "We agreed to ship in March." },
    ]);
    const row = await screen.findByText("Workshop notes");
    expect(row.closest("li")).toHaveAttribute("data-source-status", "queued");

    // Without a title, none is sent (the server picks one).
    dialog = await openAdd(user, /paste text/i);
    await user.type(within(dialog).getByLabelText("Text"), "Second note");
    await user.click(within(dialog).getByRole("button", { name: "Add text" }));
    await waitFor(() => expect(state.calls.paste[1]).toEqual({ text: "Second note" }));
  });

  it("adds a web page, says YouTube works, and fills in https://", async () => {
    const user = userEvent.setup();
    const { state } = spaceBackend();
    renderApp(SPACE);

    const dialog = await openAdd(user, /web page/i);
    expect(within(dialog).getByText(/YouTube links work too/)).toBeInTheDocument();

    const input = within(dialog).getByLabelText("Web address");
    await user.type(input, "not a url");
    await user.click(within(dialog).getByRole("button", { name: "Add page" }));
    expect(within(dialog).getByText(/Enter a web address/)).toBeInTheDocument();

    await user.clear(input);
    await user.type(input, "example.com/pricing");
    await user.click(within(dialog).getByRole("button", { name: "Add page" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(state.calls.url).toEqual([{ url: "https://example.com/pricing" }]);
  });

  it("shows the server's reason when adding fails", async () => {
    const user = userEvent.setup();
    spaceBackend();
    server.use(
      http.post("*/api/spaces/:sid/sources/url", () =>
        HttpResponse.json({ detail: "That site blocks automated reading." }, { status: 422 }),
      ),
    );
    renderApp(SPACE);
    const dialog = await openAdd(user, /web page/i);
    await user.type(within(dialog).getByLabelText("Web address"), "https://blocked.example");
    await user.click(within(dialog).getByRole("button", { name: "Add page" }));
    expect(
      await within(dialog).findByText("That site blocks automated reading."),
    ).toBeInTheDocument();
  });

  it("uploads several files in one request", async () => {
    const user = userEvent.setup();
    const { state } = spaceBackend();
    renderApp(SPACE);

    const dialog = await openAdd(user, /upload files/i);
    const files = [
      new File(["%PDF-1.7"], "report.pdf", { type: "application/pdf" }),
      new File(["hello"], "notes.txt", { type: "text/plain" }),
    ];
    await user.upload(within(dialog).getByLabelText("Choose files"), files);
    const list = within(dialog).getByRole("list", { name: "Files to upload" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);

    await user.click(within(dialog).getByRole("button", { name: "Add 2 files" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // One multipart request with both files. (File names are checked in client.test.ts: jsdom's
    // File class is not Node's, so names don't survive the trip through Node's fetch here.)
    expect(state.calls.upload).toHaveLength(1);
    expect(state.calls.upload[0]).toHaveLength(2);
  });

  it("searches the web, ticks results and adds them", async () => {
    const user = userEvent.setup();
    const { state } = spaceBackend({
      searchResults: [
        { title: "Pricing guide", url: "https://a.example/pricing", snippet: "How we price." },
        { title: "Old blog post", url: "https://b.example/blog", snippet: "From 2019." },
        { title: "Terms", url: "https://a.example/terms", snippet: "Legal terms." },
      ],
    });
    renderApp(SPACE);

    const dialog = await openAdd(user, /search the web/i);
    await user.type(within(dialog).getByLabelText("Search for"), "acme pricing");
    await user.click(within(dialog).getByRole("button", { name: "Search" }));

    const results = await within(dialog).findByRole("list", { name: "Search results" });
    expect(state.calls.search).toEqual([{ query: "acme pricing", limit: 10 }]);
    expect(within(dialog).getByRole("button", { name: "Tick pages to add" })).toBeDisabled();

    await user.click(within(results).getByRole("checkbox", { name: /Pricing guide/ }));
    await user.click(within(results).getByRole("checkbox", { name: /Terms/ }));
    await user.click(within(dialog).getByRole("button", { name: "Add 2 sources" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(state.calls.fromSearch).toEqual([
      {
        items: [
          { url: "https://a.example/pricing", title: "Pricing guide" },
          { url: "https://a.example/terms", title: "Terms" },
        ],
      },
    ]);
    expect(await screen.findByText("Pricing guide")).toBeInTheDocument();
  });

  it("greys out web search with 'Set up in Settings' when search isn't working", async () => {
    const user = userEvent.setup();
    const { state } = spaceBackend({ searchReady: false });
    renderApp(SPACE);

    await user.click(await screen.findByRole("button", { name: "Add source" }));
    const item = await screen.findByRole("menuitem", { name: /search the web/i });
    expect(within(item).getByText("Set up in Settings")).toBeInTheDocument();
    await user.click(item);

    const dialog = await screen.findByRole("dialog");
    const link = within(dialog).getByRole("link", { name: /set up in settings/i });
    expect(link).toHaveAttribute("href", "/settings#capability-web_search");
    expect(dialog.querySelector('[data-capability-gate="web_search"]')).not.toBeNull();
    expect(state.calls.search).toEqual([]);
  });

  it("links a folder after navigating the picker with breadcrumbs", async () => {
    const user = userEvent.setup();
    const listing = (path: string, parent: string | null, dirs: string[], files: string[] = []) =>
      ({
        path,
        parent,
        entries: [
          ...files.map((n) => ({
            name: n,
            path: path ? `${path}/${n}` : n,
            is_dir: false,
            size: 2048,
          })),
          ...dirs.map((n) => ({
            name: n,
            path: path ? `${path}/${n}` : n,
            is_dir: true,
            size: null,
          })),
        ],
      }) satisfies BrowseListing;
    const { state } = spaceBackend({
      browse: {
        "": listing("", null, ["Documents", "projects"], [".bashrc"]),
        projects: listing("projects", "", ["acme", "globex"]),
        "projects/acme": listing("projects/acme", "projects", ["src"], ["README.md"]),
      },
    });
    renderApp(SPACE);

    const dialog = await openAdd(user, /link folder/i);
    const contents = await within(dialog).findByRole("list", { name: "Folder contents" });
    // Folders first; files are listed but can't be opened.
    const items = within(contents)
      .getAllByRole("listitem")
      .map((li) => li.textContent);
    expect(items[0]).toBe("Documents");
    expect(items[1]).toBe("projects");
    expect(items[2]).toContain(".bashrc");
    expect(within(contents).queryByRole("button", { name: /bashrc/ })).not.toBeInTheDocument();
    // Linking the whole home folder is not offered.
    expect(within(dialog).getByRole("button", { name: "Link this folder" })).toBeDisabled();

    await user.click(within(contents).getByRole("button", { name: "projects" }));
    await user.click(await within(dialog).findByRole("button", { name: "acme" }));
    await within(dialog).findByText("README.md");

    const crumbs = within(dialog).getByRole("navigation", { name: "Folder path" });
    expect(within(crumbs).getByRole("button", { name: "acme" })).toHaveAttribute(
      "aria-current",
      "location",
    );
    expect(within(dialog).getByText("Selected: projects/acme")).toBeInTheDocument();

    // Breadcrumb back to "projects", then "Up one level" to home, then down again.
    await user.click(within(crumbs).getByRole("button", { name: "projects" }));
    await within(dialog).findByRole("button", { name: "globex" });
    await user.click(within(dialog).getByRole("button", { name: "Up one level" }));
    await within(dialog).findByRole("button", { name: "Documents" });
    expect(state.calls.browse).toEqual(["", "projects", "projects/acme"]);

    await user.click(within(dialog).getByRole("button", { name: "projects" }));
    await user.click(await within(dialog).findByRole("button", { name: "acme" }));
    await within(dialog).findByText("README.md");
    await user.click(within(dialog).getByRole("button", { name: "Link this folder" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(state.calls.folder).toEqual([{ path: "projects/acme" }]);
  });
});
