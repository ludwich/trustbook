import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { job, jobList } from "@/test/fixtures";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

describe("queue view", () => {
  it("lists jobs with summary, progress and actions; reorders queued jobs", async () => {
    const user = userEvent.setup();
    const actions: string[] = [];
    let reorder: unknown = null;
    server.use(
      http.get("*/api/jobs", () =>
        HttpResponse.json(
          jobList(
            [
              job({ id: "r", title: "Indexing handbook", status: "running", progress: 0.25 }),
              job({ id: "q1", title: "Reading a.pdf", position: 1 }),
              job({ id: "q2", title: "Reading b.pdf", position: 2 }),
              job({ id: "p", title: "Transcribing call", status: "paused", position: 3 }),
              job({
                id: "d",
                title: "Speed test",
                status: "done",
                space_id: null,
                space_name: null,
                finished_at: "2026-10-10T07:00:00Z",
              }),
              job({
                id: "f",
                title: "Fetching site",
                status: "failed",
                error: "The site blocked the request.",
                finished_at: "2026-10-10T07:30:00Z",
              }),
            ],
            { eta_seconds: 600 },
          ),
        ),
      ),
      http.post("*/api/jobs/:id/:action", ({ params }) => {
        actions.push(`${String(params.action)} ${String(params.id)}`);
        return HttpResponse.json(job({ id: String(params.id) }));
      }),
      http.post("*/api/jobs/reorder", async ({ request }) => {
        reorder = await request.json();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderApp("/queue");

    const summary = await screen.findByRole("group", { name: "Queue summary" });
    expect(summary).toHaveTextContent("1running");
    expect(summary).toHaveTextContent("2waiting");
    expect(summary).toHaveTextContent("About 10 min left");

    const running = screen.getByTestId("job-r");
    expect(within(running).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "25");
    const failed = screen.getByTestId("job-f");
    expect(within(failed).getByText("The site blocked the request.")).toBeInTheDocument();
    expect(within(screen.getByTestId("job-d")).getByText("All spaces")).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Move Reading a.pdf up" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Move Reading a.pdf down" }));
    await waitFor(() => expect(reorder).toEqual({ job_ids: ["q2", "q1"] }));

    await user.click(within(screen.getByTestId("job-p")).getByRole("button", { name: "Resume" }));
    await user.click(within(running).getByRole("button", { name: "Pause" }));
    await user.click(within(screen.getByTestId("job-q2")).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(actions).toEqual(["resume p", "pause r", "cancel q2"]));
  });

  it("shows each job's remaining estimate when the server sends one", async () => {
    server.use(
      http.get("*/api/jobs", () =>
        HttpResponse.json(
          jobList(
            [
              job({ id: "r", title: "Indexing repo", status: "running", eta_seconds: 600 }),
              job({ id: "q", title: "Reading a.pdf", eta_seconds: 30 }),
              job({ id: "n", title: "Reading b.pdf", eta_seconds: null }),
              job({
                id: "d",
                title: "Old job",
                status: "done",
                eta_seconds: 90,
                finished_at: "2026-10-10T07:00:00Z",
              }),
            ],
            { eta_seconds: 630 },
          ),
        ),
      ),
    );
    renderApp("/queue");
    expect(await screen.findByTestId("job-eta-r")).toHaveTextContent("about 10 min left");
    expect(screen.getByTestId("job-eta-q")).toHaveTextContent("less than a minute left");
    expect(screen.queryByTestId("job-eta-n")).not.toBeInTheDocument();
    // Finished jobs have nothing left.
    expect(screen.queryByTestId("job-eta-d")).not.toBeInTheDocument();
    // Pause and cancel stay available.
    const running = screen.getByTestId("job-r");
    expect(within(running).getByRole("button", { name: "Pause" })).toBeInTheDocument();
    expect(within(running).getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });
});
