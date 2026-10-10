import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { Space, SpaceCreate } from "@/api/types";
import { space } from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

/** In-memory spaces backend. */
function spacesBackend(initial: Space[] = []) {
  const state = {
    spaces: [...initial],
    created: [] as SpaceCreate[],
    deleted: [] as string[],
    active: [] as unknown[],
  };
  server.use(
    http.get("*/api/spaces", () => HttpResponse.json(state.spaces)),
    http.post("*/api/spaces", async ({ request }) => {
      const body = (await request.json()) as SpaceCreate;
      state.created.push(body);
      const s = space({
        id: "sp_new",
        slug: "new-space",
        name: body.name,
        description: body.description ?? "",
      });
      state.spaces = [s, ...state.spaces];
      return HttpResponse.json(s, { status: 201 });
    }),
    http.get("*/api/spaces/:id", ({ params }) => {
      const s = state.spaces.find((x) => x.id === params.id);
      return s
        ? HttpResponse.json(s)
        : HttpResponse.json({ detail: "Space not found." }, { status: 404 });
    }),
    http.delete("*/api/spaces/:id", ({ params }) => {
      state.deleted.push(String(params.id));
      state.spaces = state.spaces.filter((x) => x.id !== params.id);
      return new HttpResponse(null, { status: 204 });
    }),
    http.put("*/api/active-space", async ({ request }) => {
      state.active.push(await request.json());
      return new HttpResponse(null, { status: 204 });
    }),
  );
  return state;
}

describe("spaces list", () => {
  it("shows an empty state with a create button", async () => {
    spacesBackend();
    renderApp("/");
    expect(await screen.findByText("No spaces yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /new space/i })).toBeInTheDocument();
  });

  it("creates a space, then opens it and marks it active", async () => {
    const user = userEvent.setup();
    const state = spacesBackend();
    renderApp("/");
    await user.click(await screen.findByRole("button", { name: /new space/i }));
    const dialog = await screen.findByRole("dialog", { name: "New space" });

    // Name is required.
    await user.click(within(dialog).getByRole("button", { name: "Create space" }));
    expect(within(dialog).getByText("Give the space a name.")).toBeInTheDocument();
    expect(state.created).toHaveLength(0);

    await user.type(within(dialog).getByLabelText("Name"), "  Globex migration ");
    await user.type(within(dialog).getByLabelText(/description/i), "Cloud move");
    await user.click(within(dialog).getByRole("button", { name: "Create space" }));

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/spaces/sp_new"));
    expect(state.created).toEqual([{ name: "Globex migration", description: "Cloud move" }]);
    expect(await screen.findByRole("heading", { name: "Globex migration" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add source" })).toBeDisabled();
    expect(screen.getByText("Coming in the next phase.")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Chat" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Ideation" })).toBeInTheDocument();
    await waitFor(() => expect(state.active).toEqual([{ space_id: "sp_new" }]));
  });

  it("asks for confirmation, shows the folder, and only deletes on confirm", async () => {
    const user = userEvent.setup();
    const state = spacesBackend([space(), space({ id: "sp_2", slug: "globex", name: "Globex" })]);
    renderApp("/");

    await user.click(await screen.findByRole("button", { name: "Delete Acme audit" }));
    let dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/Delete “Acme audit”\?/)).toBeInTheDocument();
    expect(
      within(dialog).getByText(/removes the space folder and everything in it/i),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("/data/spaces/acme-audit")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(state.deleted).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Delete Acme audit" }));
    dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: /delete space and folder/i }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(state.deleted).toEqual(["sp_1"]);
    await waitFor(() => expect(screen.queryByText("Acme audit")).not.toBeInTheDocument());
    expect(screen.getByText("Globex")).toBeInTheDocument();
  });

  it("keeps the dialog open and explains when delete fails", async () => {
    const user = userEvent.setup();
    spacesBackend([space()]);
    server.use(
      http.delete("*/api/spaces/:id", () =>
        HttpResponse.json({ detail: "The folder is in use by another program." }, { status: 409 }),
      ),
    );
    renderApp("/");
    await user.click(await screen.findByRole("button", { name: "Delete Acme audit" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: /delete space and folder/i }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The folder is in use by another program.",
    );
  });

  it("greys out the chat box with 'Set up in Settings' when chat is not ready", async () => {
    spacesBackend([space()]);
    renderApp("/spaces/sp_1");
    const link = await screen.findByRole("link", { name: /set up in settings/i });
    expect(link).toHaveAttribute("href", "/settings#capability-chat");
    expect(screen.getByText(/needs the/)).toHaveTextContent(
      "Asking questions needs the Chat model (not set up).",
    );
  });
});
