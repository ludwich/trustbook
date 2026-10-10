import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import { JOBS_POLL_BUSY_MS, JOBS_POLL_IDLE_MS } from "@/api/queries";
import type { JobList } from "@/api/types";
import { job, jobList } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { Header } from "./Header";

function serveJobs(get: () => JobList) {
  let calls = 0;
  server.use(
    http.get("*/api/jobs", () => {
      calls += 1;
      return HttpResponse.json(get());
    }),
  );
  return () => calls;
}

afterEach(() => vi.useRealTimers());

describe("header", () => {
  it("shows wordmark, Settings link and theme toggle; indicator hidden when idle", async () => {
    const calls = serveJobs(() => jobList());
    renderWithProviders(<Header />);
    expect(screen.getByRole("link", { name: "Trustbook" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
    await waitFor(() => expect(calls()).toBe(1));
    expect(screen.getByTestId("processing-indicator")).toBeEmptyDOMElement();
  });

  it("shows 'Processing: N jobs, about M min left' linking to the queue", async () => {
    serveJobs(() =>
      jobList(
        [job({ id: "a", status: "running", progress: 0.4 }), job({ id: "b" }), job({ id: "c" })],
        { eta_seconds: 290 },
      ),
    );
    renderWithProviders(<Header />);
    const link = await screen.findByRole("link", { name: /processing/i });
    expect(link).toHaveTextContent("Processing: 3 jobs, about 5 min left");
    expect(link).toHaveAttribute("href", "/queue");
  });

  it("omits the time when there is no estimate and uses the singular", async () => {
    serveJobs(() => jobList([job({ status: "running" })]));
    renderWithProviders(<Header />);
    expect(await screen.findByRole("link", { name: /processing/i })).toHaveTextContent(
      /^Processing: 1 job$/,
    );
  });

  it("polls every 3 s while busy and every 15 s when idle", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let busy = true;
    const calls = serveJobs(() => (busy ? jobList([job({ status: "running" })]) : jobList()));
    renderWithProviders(<Header />);
    await screen.findByRole("link", { name: /processing/i });
    expect(calls()).toBe(1);

    await act(() => vi.advanceTimersByTimeAsync(JOBS_POLL_BUSY_MS + 50));
    await waitFor(() => expect(calls()).toBe(2));

    busy = false;
    await act(() => vi.advanceTimersByTimeAsync(JOBS_POLL_BUSY_MS + 50));
    await waitFor(() => expect(calls()).toBe(3));
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: /processing/i })).not.toBeInTheDocument(),
    );

    // Idle: nothing for a while, then one call after 15 s.
    await act(() => vi.advanceTimersByTimeAsync(JOBS_POLL_BUSY_MS * 2));
    expect(calls()).toBe(3);
    await act(() => vi.advanceTimersByTimeAsync(JOBS_POLL_IDLE_MS));
    await waitFor(() => expect(calls()).toBe(4));
  });

  it("persists the theme choice and applies the dark class", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Header />);
    const group = screen.getByRole("radiogroup", { name: "Colour theme" });
    expect(screen.getByRole("radio", { name: "Match system" })).toHaveAttribute(
      "aria-checked",
      "true",
    );

    await user.click(screen.getByRole("radio", { name: "Dark" }));
    expect(document.documentElement).toHaveClass("dark");
    expect(localStorage.getItem("trustbook-theme")).toBe("dark");

    await user.click(screen.getByRole("radio", { name: "Light" }));
    expect(document.documentElement).not.toHaveClass("dark");
    expect(localStorage.getItem("trustbook-theme")).toBe("light");
    expect(group).toBeInTheDocument();
  });

  it("says 'less than a minute left' under 60 s and rounds minutes up from eta_seconds", async () => {
    let eta: number | null = 45;
    serveJobs(() => jobList([job({ status: "running" }), job({ id: "b" })], { eta_seconds: eta }));
    const { client } = renderWithProviders(<Header />);
    const link = await screen.findByRole("link", { name: /processing/i });
    expect(link).toHaveTextContent(/^Processing: 2 jobs, less than a minute left$/);

    eta = 25 * 60 + 1;
    await act(() => client.refetchQueries({ queryKey: ["jobs"] }));
    await waitFor(() => expect(link).toHaveTextContent(/^Processing: 2 jobs, about 26 min left$/));

    eta = null;
    await act(() => client.refetchQueries({ queryKey: ["jobs"] }));
    await waitFor(() => expect(link).toHaveTextContent(/^Processing: 2 jobs$/));
  });
});
